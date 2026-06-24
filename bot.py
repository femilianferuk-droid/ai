#!/usr/bin/env python3
import asyncio
import aiohttp
import asyncpg
import os
import json
import re
import random
import logging
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, BusinessConnection,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile
)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

load_dotenv()

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
AI_API_KEY   = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"
AI_API_URL   = "https://gpt-agent.cc/v1/chat/completions"
AI_MODEL     = os.getenv("AI_MODEL", "claude-sonnet-4-6")
AI_SYSTEM    = "Ты — ассистент по программированию. Твоя задача — помогать с кодом: писать, объяснять, исправлять ошибки и отправлять код файлом, если он длинный. Отвечай чётко и по делу."
MAX_HISTORY  = int(os.getenv("MAX_HISTORY", "20"))
MAX_TOKENS   = 100000
ADMIN_IDS    = [7973988177]
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/bot_db")

# ── Database Pool ────────────────────────────────────────────────────────────
db_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return db_pool

async def db_init():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                id         TEXT PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                username   TEXT,
                first_name TEXT,
                active     INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id      BIGSERIAL PRIMARY KEY,
                conn_id TEXT NOT NULL,
                chat_id BIGINT NOT NULL,
                role    TEXT NOT NULL,
                content TEXT NOT NULL,
                ts      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_conn_chat 
            ON history(conn_id, chat_id);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_ts 
            ON history(ts DESC);
        """)

async def db_upsert_conn(conn_id: str, user_id: int, username: str | None,
                         first_name: str | None, active: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO connections (id, user_id, username, first_name, active)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                active = EXCLUDED.active;
        """, conn_id, user_id, username, first_name, int(active))

async def db_get_conn(conn_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("""
            SELECT id, user_id, username, first_name, active, 
                   to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at
            FROM connections WHERE id = $1
        """, conn_id)

async def db_all_conns():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("""
            SELECT id, user_id, username, first_name, active,
                   to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at
            FROM connections 
            ORDER BY created_at DESC
        """)

async def db_conn_by_uid(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("""
            SELECT id, user_id, username, first_name, active,
                   to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at
            FROM connections 
            WHERE user_id = $1 
            ORDER BY created_at DESC 
            LIMIT 1
        """, user_id)

async def db_add_msg(conn_id: str, chat_id: int, role: str, content: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO history(conn_id, chat_id, role, content) 
            VALUES ($1, $2, $3, $4)
        """, conn_id, chat_id, role, content)

async def db_get_history(conn_id: str, chat_id: int, limit: int = MAX_HISTORY) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role, content FROM history 
            WHERE conn_id = $1 AND chat_id = $2 
            ORDER BY id DESC LIMIT $3
        """, conn_id, chat_id, limit)
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

async def db_delete(conn_id: str | None = None, chat_id: int | None = None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if conn_id is None:
            await conn.execute("DELETE FROM history")
        elif chat_id is None:
            await conn.execute("DELETE FROM history WHERE conn_id = $1", conn_id)
        else:
            await conn.execute(
                "DELETE FROM history WHERE conn_id = $1 AND chat_id = $2", 
                conn_id, chat_id
            )

async def db_stats(conn_id: str) -> tuple[int, int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        msgs = await conn.fetchval(
            "SELECT COUNT(*) FROM history WHERE conn_id = $1", conn_id
        )
        chats = await conn.fetchval(
            "SELECT COUNT(DISTINCT chat_id) FROM history WHERE conn_id = $1", conn_id
        )
    return msgs or 0, chats or 0

async def db_get_system() -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = 'system_prompt'")
    return row["value"] if row else AI_SYSTEM

async def db_set_system(prompt: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO config (key, value) VALUES ('system_prompt', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, prompt)

async def db_get_source() -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM config WHERE key = 'prompt_source'")
    return row["value"] if row else ""

async def db_set_source(source: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO config (key, value) VALUES ('prompt_source', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, source)

def _list_str(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)

def _dict_str(v: dict) -> str:
    return ", ".join(f"{k}: {val}" for k, val in v.items())

def build_prompt_from_config(cfg: dict) -> str:
    name         = cfg.get("name", "")
    personality  = cfg.get("personality", "ассистент по коду")
    age          = cfg.get("age")
    city         = cfg.get("city", "")
    job          = cfg.get("job", "")
    company      = cfg.get("company", "")
    rel_status   = cfg.get("relationship_status", "")

    who = name if name else personality

    bio_parts: list[str] = [f"Ты — {who}, {personality}."]
    if age:
        bio_parts.append(f"Тебе {age} лет.")
    if city:
        bio_parts.append(f"Ты живёшь в {city}.")
    if job:
        bio_parts.append(f"Работаешь {job}" + (f" в {company}" if company else "") + ".")
    if rel_status:
        bio_parts.append(f"Статус: {rel_status}.")

    char_parts: list[str] = []
    for key, label in [
        ("traits",         "Черты характера"),
        ("hobbies",        "Хобби"),
        ("habits",         "Привычки"),
        ("favorite_games", "Любимые игры"),
        ("favorite_food",  "Любимая еда"),
        ("music",          "Слушает"),
    ]:
        val = cfg.get(key)
        if val:
            char_parts.append(f"{label}: {_list_str(val)}.")

    devices = cfg.get("devices")
    if isinstance(devices, dict) and devices:
        char_parts.append(f"Девайсы: {_dict_str(devices)}.")

    known_keys = {
        "name", "personality", "age", "city", "job", "company",
        "relationship_status", "traits", "hobbies", "habits",
        "favorite_games", "favorite_food", "music", "devices",
        "tone", "style", "lowercase", "emoji", "rare_exclamation_marks",
        "response_length", "language", "restrictions", "context",
        "communication_style", "communication_examples",
    }
    for key, val in cfg.items():
        if key in known_keys or val in (None, "", [], {}):
            continue
        if isinstance(val, list):
            char_parts.append(f"{key}: {_list_str(val)}.")
        elif isinstance(val, dict):
            char_parts.append(f"{key}: {_dict_str(val)}.")
        else:
            char_parts.append(f"{key}: {val}.")

    tone         = cfg.get("tone", "")
    style        = cfg.get("style", "")
    lowercase    = cfg.get("lowercase", False)
    use_emoji    = cfg.get("emoji", False)
    length       = cfg.get("response_length", "medium")
    language     = cfg.get("language", "")
    restrictions = cfg.get("restrictions", "")
    context      = cfg.get("context", "")
    rare_excl    = cfg.get("rare_exclamation_marks", False)
    comm         = cfg.get("communication_style", {})
    examples     = cfg.get("communication_examples", [])

    tone_map = {
        "friendly":     "дружелюбный, тёплый",
        "formal":       "официальный, сдержанный",
        "casual":       "расслабленный, неформальный",
        "professional": "профессиональный",
        "strict":       "строгий, лаконичный",
    }
    length_map = {
        "short":  "Пиши КОРОТКО — 1-2 предложения, не больше.",
        "medium": "Отвечай развёрнуто, но по делу.",
        "long":   "Можешь отвечать подробно.",
    }

    rules: list[str] = []

    if tone:
        rules.append(f"Тон: {tone_map.get(tone, tone)}.")
    if style == "informal":
        rules.append("Общайся на «ты».")
    elif style == "formal":
        rules.append("Общайся на «Вы».")

    if lowercase:
        rules.append("Пиши ВСЁ строчными буквами — никаких заглавных вообще, даже в начале предложения.")

    emoji_flag = use_emoji if "emoji" in cfg else comm.get("uses_emojis", True)
    if emoji_flag:
        rules.append("Используй эмодзи.")
    else:
        rules.append("Никогда не используй эмодзи.")

    if rare_excl or comm.get("rarely_uses_exclamation_marks"):
        rules.append("Восклицательные знаки — крайне редко, почти никогда.")

    bracket = comm.get("often_uses_bracket", "")
    if bracket:
        rules.append(f"Часто ставь «{bracket}» в конце фраз — вместо эмодзи.")

    if comm.get("short_messages"):
        rules.append("Сообщения короткие.")
    if comm.get("asks_questions_often"):
        rules.append("Часто задавай вопросы собеседнику.")

    if length in length_map:
        rules.append(length_map[length])
    if language:
        lang_map = {"ru": "русском", "en": "английском", "uk": "украинском"}
        rules.append(f"Всегда отвечай на {lang_map.get(language, language)} языке.")
    if restrictions:
        rules.append(f"ЗАПРЕЩЕНО обсуждать: {restrictions}.")
    if context:
        rules.append(f"Контекст: {context}.")

    rules.append("Если твой ответ содержит код больше 20 строк — ОБЯЗАТЕЛЬНО предложи отправить его файлом.")

    bio_block  = " ".join(bio_parts)
    char_block = "\n".join(char_parts)
    rules_block = "\n".join(f"• {r}" for r in rules)
    examples_block = ""
    if examples:
        ex_lines = "\n".join(f'  — «{e}»' for e in examples)
        examples_block = f"\n\nПримеры твоих сообщений (пиши именно ТАК):\n{ex_lines}"

    return (
        f"{bio_block}\n"
        f"{char_block}\n\n"
        f"ЖЁСТКОЕ ПРАВИЛО: ты НИКОГДА не называешь себя Claude, GPT, ИИ, нейросетью "
        f"или ботом. Ты — {who}. Если спрашивают «кто ты?» — отвечай от лица {who}, "
        f"не раскрывая, что ты ИИ.\n\n"
        f"Правила общения:\n{rules_block}"
        f"{examples_block}"
    )

# ── AI ────────────────────────────────────────────────────────────────────────

async def ai_chat(messages: list[dict]) -> str:
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
    }
    timeout    = aiohttp.ClientTimeout(total=120)
    ssl_ctx    = False
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(AI_API_URL, headers=headers, json=payload, ssl=ssl_ctx) as r:
            raw = await r.text()
            logging.info("AI response status=%s body=%s", r.status, raw[:500])
            if r.status != 200:
                raise RuntimeError(f"API error {r.status}: {raw[:300]}")
            import json as _json
            data = _json.loads(raw)
    if "choices" not in data:
        raise RuntimeError(f"Unexpected API response: {data}")
    return data["choices"][0]["message"]["content"]


def truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"

def extract_code_blocks(text: str) -> list[tuple[str | None, str]]:
    """Извлекает блоки кода из текста. Возвращает список (язык, код)."""
    pattern = r'```(\w+)?\n(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    return [(lang, code.strip()) for lang, code in matches]

# ── Keyboards ─────────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Подключения",
            callback_data="conns",
        )],
        [InlineKeyboardButton(
            text="Личность ИИ",
            callback_data="personality",
        )],
        [InlineKeyboardButton(
            text="Очистить всю историю",
            callback_data="clear_all",
        )],
    ])

def kb_personality() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Изменить",
            callback_data="personality_edit",
        )],
        [InlineKeyboardButton(
            text="Сбросить к стандартному",
            callback_data="personality_reset",
        )],
        [InlineKeyboardButton(text="◁ Назад", callback_data="main")],
    ])

def kb_personality_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◁ Отмена", callback_data="personality")
    ]])

def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◁ Назад", callback_data="main")
    ]])

def kb_conns(conns: list) -> InlineKeyboardMarkup:
    rows = []
    for row in conns:
        uid    = row["user_id"]
        name   = row["first_name"] or row["username"] or str(uid)
        active = row["active"]
        icon   = "✅" if active else "❌"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {name}", callback_data=f"u:{uid}"
        )])
    rows.append([InlineKeyboardButton(text="◁ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_conn_detail(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Очистить историю",
            callback_data=f"clr:{uid}",
        )],
        [InlineKeyboardButton(text="◁ Назад", callback_data="conns")],
    ])

def kb_confirm_clear_all() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Да, удалить",
            callback_data="clear_all_ok",
        ),
        InlineKeyboardButton(text="◁ Отмена", callback_data="main"),
    ]])

# ── FSM ───────────────────────────────────────────────────────────────────────

class PersonalityForm(StatesGroup):
    waiting_prompt = State()

# ── Bot ───────────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

MAIN_TEXT = (
    '<b>🤖 Панель управления</b>\n\n'
    'Бот подключён к бизнес-аккаунту и отвечает клиентам с помощью ИИ.\n\n'
    '⚙ Выберите действие:'
)

async def render_conn_detail(cb: CallbackQuery, uid: int) -> None:
    row = await db_conn_by_uid(uid)
    if not row:
        await cb.answer("Подключение не найдено", show_alert=True)
        return
    conn_id = row["id"]
    username = row["username"]
    first_name = row["first_name"]
    active = row["active"]
    created_at = row["created_at"]
    msgs, chats = await db_stats(conn_id)
    name = first_name or username or str(uid)
    tag  = f' (@{username})' if username else ''
    st   = "Активно" if active else "Отключено"
    await cb.message.edit_text(
        f'<b>👤 {name}{tag}</b>\n\n'
        f'Статус: {st}\n'
        f'🕓 Подключено: {created_at[:10]}\n'
        f'✍ Сообщений: <b>{msgs}</b>\n'
        f'👥 Чатов: <b>{chats}</b>',
        reply_markup=kb_conn_detail(uid)
    )

# ── Отправка кода файлом ─────────────────────────────────────────────────────

async def send_code_as_file(chat_id: int, text: str, bc_id: str | None = None):
    """Если в ответе есть большие блоки кода — отправляет их файлами."""
    code_blocks = extract_code_blocks(text)
    if not code_blocks:
        return False
    
    for lang, code in code_blocks:
        if len(code.split('\n')) > 20:
            ext = lang if lang else "txt"
            filename = f"code.{ext}"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(code)
            
            kwargs = {"business_connection_id": bc_id} if bc_id else {}
            await bot.send_document(
                chat_id,
                FSInputFile(filename),
                caption=f"📄 Код ({ext})",
                **kwargs
            )
            os.remove(filename)
            return True
    return False

# ── Message handlers ──────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if is_admin(msg.from_user.id):
        await msg.answer(MAIN_TEXT, reply_markup=kb_main())
    else:
        await msg.answer(
            '<b>🤖 ИИ-ассистент по коду</b>\n\n'
            'Этот бот помогает с программированием через бизнес-аккаунт.'
        )

@dp.message(Command("testai"))
async def cmd_testai(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await msg.answer("Проверяю API...")
    try:
        reply = await ai_chat([
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Скажи 'OK' и ничего больше."},
        ])
        await msg.answer(f"<b>API работает:</b>\n<code>{reply[:500]}</code>")
    except Exception as e:
        await msg.answer(f"<b>Ошибка API:</b>\n<code>{e}</code>")

@dp.message(F.text, ~F.text.startswith("/"))
async def on_direct_msg(msg: Message, state: FSMContext):
    if await state.get_state() is not None:
        return

    cid     = msg.chat.id
    conn_id = f"direct_{cid}"

    await db_add_msg(conn_id, cid, "user", msg.text)

    history  = await db_get_history(conn_id, cid)
    messages = [{"role": "system", "content": await db_get_system()}] + history

    ai_task = asyncio.create_task(ai_chat(messages))
    await asyncio.sleep(random.uniform(1.0, 3.0))

    while not ai_task.done():
        await bot.send_chat_action(cid, action="typing")
        try:
            await asyncio.wait_for(asyncio.shield(ai_task), timeout=4.0)
        except asyncio.TimeoutError:
            pass

    try:
        reply = ai_task.result()
    except Exception as e:
        logging.exception("Direct AI error for chat_id=%s: %s", cid, e)
        reply = f"Ошибка ИИ: {e}"

    reply = truncate(reply)
    await db_add_msg(conn_id, cid, "assistant", reply)

    # Проверяем, есть ли большой код — отправляем файлом
    file_sent = await send_code_as_file(cid, reply)
    
    # Отправляем текстовый ответ
    parts = split_reply(reply)
    for i, part in enumerate(parts):
        await _type_and_send(cid, part)
        if i < len(parts) - 1:
            await asyncio.sleep(random.uniform(0.8, 2.0))

@dp.business_connection()
async def on_bc(bc: BusinessConnection):
    u = bc.user
    await db_upsert_conn(bc.id, u.id, u.username, u.first_name, bc.is_enabled)

    name = u.first_name or u.username or str(u.id)
    tag  = f' (@{u.username})' if u.username else ''

    if bc.is_enabled:
        text = (
            f'<b>✅ Бизнес-подключение активно</b>\n\n'
            f'👤 {name}{tag}\n'
            f'⚙ ID: <code>{bc.id}</code>'
        )
    else:
        text = (
            f'<b>❌ Бизнес-подключение отключено</b>\n\n'
            f'👤 {name}{tag}'
        )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text)
        except Exception as e:
            logging.warning("Failed to notify admin %s: %s", aid, e)

@dp.business_message(Command("clear"))
async def cmd_clear_business_chat(msg: Message):
    row = await db_get_conn(msg.business_connection_id)
    if not row or msg.from_user.id != row["user_id"]:
        return
    await db_delete(msg.business_connection_id, msg.chat.id)
    await bot.send_message(
        msg.chat.id,
        '✅ <b>История чата с ИИ очищена.</b>',
        business_connection_id=msg.business_connection_id
    )

def split_reply(text: str) -> list[str]:
    """Разбивает ответ на 2–3 естественные части по границам предложений."""
    text = text.strip()
    if len(text) < 90:
        return [text]

    sentences = re.split(r'(?<=[.!?…])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) <= 1:
        parts = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
        if len(parts) >= 2:
            return parts[:3]
        mid = len(text) // 2
        idx = text.rfind(' ', mid - 40, mid + 40)
        return [text[:idx].strip(), text[idx:].strip()] if idx > 0 else [text]

    n = 2 if len(sentences) < 4 or len(text) < 220 else random.choice([2, 3])
    n = min(n, len(sentences))

    parts, per = [], len(sentences) // n
    for i in range(n):
        start = i * per
        end   = len(sentences) if i == n - 1 else (i + 1) * per
        chunk = ' '.join(sentences[start:end]).strip()
        if chunk:
            parts.append(chunk)

    return parts or [text]

async def _keep_typing(cid: int, duration: float, bc_id: str | None = None) -> None:
    kwargs = {"business_connection_id": bc_id} if bc_id else {}
    elapsed = 0.0
    while elapsed < duration:
        await bot.send_chat_action(cid, action="typing", **kwargs)
        chunk = min(4.0, duration - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk

async def _type_and_send(cid: int, text: str, bc_id: str | None = None) -> None:
    chars_per_sec = random.uniform(15, 25)
    duration = max(1.5, min(len(text) / chars_per_sec, 7.0))
    await _keep_typing(cid, duration, bc_id)
    kwargs = {"business_connection_id": bc_id} if bc_id else {}
    await bot.send_message(cid, text, **kwargs)

@dp.business_message()
async def on_business_msg(msg: Message):
    if not msg.text or msg.text.startswith("/"):
        return

    bc_id = msg.business_connection_id
    cid   = msg.chat.id
    row   = await db_get_conn(bc_id)

    if row and msg.from_user.id == row["user_id"]:
        return

    await db_add_msg(bc_id, cid, "user", msg.text)

    history  = await db_get_history(bc_id, cid)
    messages = [{"role": "system", "content": await db_get_system()}] + history

    ai_task = asyncio.create_task(ai_chat(messages))
    await asyncio.sleep(random.uniform(1.5, 4.0))

    while not ai_task.done():
        await bot.send_chat_action(cid, action="typing", business_connection_id=bc_id)
        try:
            await asyncio.wait_for(asyncio.shield(ai_task), timeout=4.0)
        except asyncio.TimeoutError:
            pass

    try:
        reply = ai_task.result()
    except Exception as e:
        logging.exception("AI error for chat_id=%s: %s", cid, e)
        reply = f"Ошибка ИИ: {e}"

    reply = truncate(reply)
    await db_add_msg(bc_id, cid, "assistant", reply)

    # Проверяем, есть ли большой код — отправляем файлом
    file_sent = await send_code_as_file(cid, reply, bc_id)

    parts = split_reply(reply)
    for i, part in enumerate(parts):
        await _type_and_send(cid, part, bc_id)
        if i < len(parts) - 1:
            await asyncio.sleep(random.uniform(0.8, 2.0))

# ── Callback handlers ─────────────────────────────────────────────────────────

@dp.callback_query(F.data == "personality")
async def cb_personality(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await state.clear()

    source = await db_get_source()
    prompt = await db_get_system()

    if source and source.strip().startswith("{"):
        src_preview = source if len(source) <= 600 else source[:597] + "…"
        body = (
            f'<b>Режим:</b> JSON-конфиг\n\n'
            f'<pre><code class="language-json">{src_preview}</code></pre>'
        )
    else:
        preview = prompt if len(prompt) <= 700 else prompt[:697] + "…"
        body = (
            f'<b>Режим:</b> текстовый промпт\n\n'
            f'<blockquote>{preview}</blockquote>'
        )

    await cb.message.edit_text(
        f'<b>✍ Личность ИИ</b>\n\n' + body,
        reply_markup=kb_personality()
    )
    await cb.answer()

JSON_EXAMPLE = (
    '{\n'
    '  "name": "Кодер",\n'
    '  "personality": "ассистент по программированию",\n'
    '  "tone": "professional",\n'
    '  "style": "informal",\n'
    '  "lowercase": false,\n'
    '  "emoji": false,\n'
    '  "response_length": "medium",\n'
    '  "language": "ru",\n'
    '  "restrictions": "",\n'
    '  "context": "помощь с кодом, отправка файлов"\n'
    '}'
)

@dp.callback_query(F.data == "personality_edit")
async def cb_personality_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await state.set_state(PersonalityForm.waiting_prompt)
    await cb.message.edit_text(
        f'<b>✍ Настройка личности</b>\n\n'
        f'Отправь <b>JSON-конфиг</b> или <b>текстовый промпт</b>.\n\n'
        f'<b>JSON (рекомендуется):</b>\n'
        f'<pre><code class="language-json">{JSON_EXAMPLE}</code></pre>\n'
        f'<b>Поля JSON:</b>\n'
        f'<code>name</code> — имя ИИ\n'
        f'<code>personality</code> — кто он (роль)\n'
        f'<code>tone</code> — friendly / formal / casual / professional / strict\n'
        f'<code>style</code> — informal / formal\n'
        f'<code>lowercase</code> — true/false\n'
        f'<code>emoji</code> — true/false\n'
        f'<code>response_length</code> — short / medium / long\n'
        f'<code>language</code> — ru / en / uk\n'
        f'<code>restrictions</code> — что нельзя обсуждать\n'
        f'<code>context</code> — контекст бизнеса',
        reply_markup=kb_personality_cancel()
    )
    await cb.answer()

@dp.callback_query(F.data == "personality_reset")
async def cb_personality_reset(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await state.clear()
    await db_set_system(AI_SYSTEM)
    await db_set_source("")
    await cb.answer("✅ Сброшено к стандартному", show_alert=True)
    await cb_personality(cb, state)

@dp.message(PersonalityForm.waiting_prompt)
async def msg_personality_input(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return

    raw = (msg.text or "").strip()
    if len(raw) < 5:
        await msg.answer('❌ Слишком короткий ввод. Напишите подробнее.')
        return

    if raw.startswith("{"):
        try:
            cfg = json.loads(raw)
            if not isinstance(cfg, dict):
                raise ValueError("not a dict")
            built_prompt = build_prompt_from_config(cfg)
            await db_set_source(raw)
            mode_label = "JSON-конфиг"
            preview_block = (
                f'<b>Конфиг:</b>\n'
                f'<pre><code class="language-json">{raw[:600]}</code></pre>\n\n'
                f'<b>Сгенерированный промпт:</b>\n'
                f'<blockquote>{built_prompt[:600]}</blockquote>'
            )
        except (json.JSONDecodeError, ValueError) as e:
            await msg.answer(
                f'❌ Ошибка в JSON: <code>{e}</code>\n\nПроверьте синтаксис и отправьте снова.'
            )
            return
    else:
        built_prompt = raw
        await db_set_source(raw)
        mode_label = "текстовый промпт"
        preview_block = f'<blockquote>{raw[:700]}</blockquote>'

    await db_set_system(built_prompt)
    await state.clear()

    await msg.answer(
        f'<b>✅ Личность обновлена</b> <i>({mode_label})</i>\n\n' + preview_block,
        reply_markup=kb_main()
    )

@dp.callback_query(F.data == "main")
async def cb_main(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await state.clear()
    await cb.message.edit_text(MAIN_TEXT, reply_markup=kb_main())
    await cb.answer()

@dp.callback_query(F.data == "conns")
async def cb_conns(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    conns = await db_all_conns()
    if not conns:
        return await cb.answer("Нет подключений", show_alert=True)
    await cb.message.edit_text(
        f'<b>👥 Бизнес-подключения</b>\n\nВсего: <b>{len(conns)}</b>',
        reply_markup=kb_conns(conns)
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("u:"))
async def cb_conn_detail(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    uid = int(cb.data[2:])
    await render_conn_detail(cb, uid)
    await cb.answer()

@dp.callback_query(F.data.startswith("clr:"))
async def cb_clr_conn(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    uid = int(cb.data[4:])
    row = await db_conn_by_uid(uid)
    if not row:
        return await cb.answer("Не найдено", show_alert=True)
    await db_delete(row["id"])
    await cb.answer("✅ История очищена", show_alert=True)
    await render_conn_detail(cb, uid)

@dp.callback_query(F.data == "clear_all")
async def cb_clear_all(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.message.edit_text(
        '<b>🗑 Подтверждение</b>\n\n'
        'Удалить <b>всю историю</b> всех чатов с ИИ? Это действие необратимо.',
        reply_markup=kb_confirm_clear_all()
    )
    await cb.answer()

@dp.callback_query(F.data == "clear_all_ok")
async def cb_clear_all_ok(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await db_delete()
    await cb.message.edit_text(
        '<b>✅ Готово</b>\n\nВся история чатов с ИИ удалена.',
        reply_markup=kb_back_main()
    )
    await cb.answer()

# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    banner = """
\033[35m
 █████╗ ██╗    ██████╗  ██████╗ ████████╗
██╔══██╗██║    ██╔══██╗██╔═══██╗╚══██╔══╝
███████║██║    ██████╔╝██║   ██║   ██║
██╔══██║██║    ██╔══██║██║   ██║   ██║
██║  ██║██║    ██████╔╝╚██████╔╝   ██║
╚═╝  ╚═╝╚═╝    ╚═════╝  ╚═════╝   ╚═╝
\033[0m
    \033[36mСоздатель:\033[0m t.me/wivvi
    \033[36mТелеграм: \033[0m t.me/StriverDev

    \033[32mСтатус: Газ\033[0m
    """
    print(banner)
    await db_init()
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message", "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ]
    )

if __name__ == "__main__":
    asyncio.run(main())
