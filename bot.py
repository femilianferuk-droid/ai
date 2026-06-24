import asyncio
import os
import sys
import json
import aiohttp
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from aiogram.enums import ParseMode
import uuid

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен!")
    sys.exit(1)
if not DATABASE_URL:
    print("❌ DATABASE_URL не установлен!")
    sys.exit(1)

AI_API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"
AI_API_URL = "https://gpt-agent.cc/v1/chat/completions"
AI_MODELS_URL = "https://gpt-agent.cc/v1/models"

# Дефолтные эмодзи для моделей
MODEL_EMOJIS = {
    "claude": "5870982283724328568",
    "minimax": "5870930636742595124",
    "kimi": "5870921681735781843",
    "deepseek": "6030400221232501136",
    "gpt": "6030400221232501136",
    "gemini": "5870930636742595124",
    "llama": "5870982283724328568",
    "default": "6030400221232501136"
}

EM = {
    "bot": "6030400221232501136",
    "settings": "5870982283724328568",
    "file": "5870528606328852614",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "pencil": "5870676941614354370",
    "trash": "5870875489362513438",
    "info": "6028435952299413210",
    "clock": "5983150113483134607",
    "loading": "5345906554510012647",
    "back": "6039450962865688331",
    "smile": "5870764288364252592",
    "money": "5904462880941545555",
    "reload": "5345906554510012647",
}

def e(name):
    return f'<tg-emoji emoji-id="{EM[name]}">'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальный кеш моделей
available_models = {}
user_states = {}

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='chats' AND column_name='user_id'")
        if not cur.fetchone():
            cur.execute("DROP TABLE IF EXISTS chats")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id TEXT NOT NULL,
                title TEXT DEFAULT 'Новый чат',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model TEXT,
                role TEXT,
                content TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_chat ON chats(user_id, chat_id)")
        conn.commit()
        conn.close()
        print("✅ DB ready")
    except Exception as ex:
        print(f"❌ DB error: {ex}")

init_db()

def get_state(uid):
    if uid not in user_states:
        user_states[uid] = {'chat': None, 'model': list(available_models.keys())[0] if available_models else 'claude-sonnet-4-6', 'busy': False}
    return user_states[uid]

def get_emoji_for_model(model_id):
    """Подбирает эмодзи по названию модели"""
    model_lower = model_id.lower()
    for key, emoji_id in MODEL_EMOJIS.items():
        if key in model_lower:
            return emoji_id
    return MODEL_EMOJIS["default"]

def get_short_name(model_id):
    """Короткое красивое имя модели"""
    name = model_id.replace("-", " ").replace(".", " ")
    words = name.split()
    if len(words) > 2:
        return " ".join(words[:2]).title()
    return name.title()

async def fetch_models():
    """Загружает список доступных моделей с API"""
    global available_models
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(AI_MODELS_URL, headers=headers, ssl=False) as r:
                if r.status == 200:
                    data = await r.json()
                    models_list = []
                    
                    if isinstance(data, dict) and 'data' in data:
                        for m in data['data']:
                            models_list.append(m.get('id', ''))
                    elif isinstance(data, list):
                        for m in data:
                            if isinstance(m, dict):
                                models_list.append(m.get('id', ''))
                            else:
                                models_list.append(str(m))
                    
                    # Фильтруем и сортируем
                    available_models = {}
                    for model_id in sorted(models_list):
                        if model_id and not model_id.startswith('#'):
                            available_models[model_id] = get_short_name(model_id)
                    
                    print(f"📋 Loaded {len(available_models)} models: {list(available_models.keys())}")
                    return available_models
                else:
                    print(f"❌ Failed to fetch models: {r.status}")
    except Exception as e:
        print(f"❌ Error fetching models: {e}")
    
    # Fallback models
    if not available_models:
        available_models = {
            "claude-sonnet-4-6": "Claude Sonnet 4.6",
            "claude-sonnet-4.6": "Claude Sonnet 4.6",
            "minimax-M2.7": "MiniMax M2.7",
            "KIMI-2.6": "Kimi 2.6",
            "DEEPSEEK-V4-FLASH": "DeepSeek V4 Flash",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
        }
    
    return available_models

async def call_api(model, messages):
    """Запрос к API"""
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    
    timeout = aiohttp.ClientTimeout(total=120)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Попытка 1
        print(f"🔄 Trying model: {model}")
        async with session.post(AI_API_URL, headers=headers, json=payload, ssl=False) as resp:
            raw = await resp.text()
            print(f"📡 Status={resp.status}, response={raw[:300]}")
            
            if resp.status == 200:
                data = json.loads(raw)
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
            
            if '额度不足' in raw or 'insufficient' in raw.lower():
                raise Exception("💰 Закончился баланс API. Проверьте счёт на gpt-agent.cc")
        
        # Попытка 2: без system message
        payload["messages"] = [m for m in messages if m["role"] != "system"]
        payload["temperature"] = 0.5
        
        async with session.post(AI_API_URL, headers=headers, json=payload, ssl=False) as resp:
            raw = await resp.text()
            if resp.status == 200:
                data = json.loads(raw)
                if "choices" in data:
                    return data["choices"][0]["message"]["content"]
        
        raise Exception(f"❌ API error {resp.status}: {raw[:200]}")

def model_kb(page=0):
    """Клавиатура с моделями (по 8 на странице)"""
    models = list(available_models.items())
    per_page = 8
    total_pages = (len(models) + per_page - 1) // per_page
    start = page * per_page
    end = start + per_page
    page_models = models[start:end]
    
    kb = InlineKeyboardBuilder()
    
    for model_id, short_name in page_models:
        emoji_id = get_emoji_for_model(model_id)
        kb.add(InlineKeyboardButton(
            text=short_name,
            callback_data=f"selmodel_{model_id}",
            icon_custom_emoji_id=emoji_id
        ))
    
    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◁ Назад",
            callback_data=f"models_page_{page-1}",
            icon_custom_emoji_id=EM["back"]
        ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="Вперёд ▷",
            callback_data=f"models_page_{page+1}",
            icon_custom_emoji_id=EM["loading"]
        ))
    if nav:
        kb.row(*nav)
    
    kb.add(InlineKeyboardButton(
        text="🔄 Обновить список",
        callback_data="refresh_models",
        icon_custom_emoji_id=EM["reload"]
    ))
    kb.add(InlineKeyboardButton(
        text="🔙 Главное меню",
        callback_data="back_main",
        icon_custom_emoji_id=EM["back"]
    ))
    
    kb.adjust(1)
    return kb.as_markup()

def main_kb():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="Новый чат", callback_data="new_chat", icon_custom_emoji_id=EM["pencil"]))
    kb.add(InlineKeyboardButton(text="Мои чаты", callback_data="my_chats", icon_custom_emoji_id=EM["file"]))
    kb.add(InlineKeyboardButton(text="Модель", callback_data="select_model", icon_custom_emoji_id=EM["settings"]))
    kb.adjust(2, 1)
    return kb.as_markup()

def chats_kb(uid):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT chat_id, MAX(title) as title, COUNT(*) as cnt FROM chats WHERE user_id=%s GROUP BY chat_id ORDER BY MAX(timestamp) DESC LIMIT 10", (uid,))
        chats = cur.fetchall()
        conn.close()
        kb = InlineKeyboardBuilder()
        if chats:
            for c in chats:
                t = (c['title'] or 'Новый чат')[:30]
                kb.add(InlineKeyboardButton(text=f"{t} ({c['cnt']})", callback_data=f"switch_{c['chat_id']}", icon_custom_emoji_id=EM["smile"]))
            kb.add(InlineKeyboardButton(text="Очистить всё", callback_data="clear_all", icon_custom_emoji_id=EM["trash"]))
        else:
            kb.add(InlineKeyboardButton(text="Нет чатов", callback_data="none", icon_custom_emoji_id=EM["info"]))
        kb.add(InlineKeyboardButton(text="Назад", callback_data="back_main", icon_custom_emoji_id=EM["back"]))
        kb.adjust(1)
        return kb.as_markup()
    except:
        return main_kb()

@dp.message(Command("start"))
async def start(msg: types.Message):
    if not available_models:
        await msg.answer(f'{e("loading")}🔄</tg-emoji> Загружаю модели...', parse_mode=ParseMode.HTML)
        await fetch_models()
    
    s = get_state(msg.from_user.id)
    s['chat'] = str(uuid.uuid4())
    
    current_model = s['model']
    model_name = available_models.get(current_model, current_model)
    
    await msg.answer(
        f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n\n'
        f'{e("settings")}⚙️</tg-emoji> Модель: <b>{model_name}</b>\n'
        f'{e("info")}ℹ️</tg-emoji> Отправьте сообщение!\n\n'
        f'<i>Доступно моделей: {len(available_models)}</i>',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
    )

@dp.message(Command("models"))
async def cmd_models(msg: types.Message):
    await fetch_models()
    await msg.answer(
        f'{e("check")}✅</tg-emoji> <b>Доступные модели:</b>\n\n' + 
        '\n'.join(f'• <code>{k}</code>' for k in available_models.keys()),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text)
async def handle(msg: types.Message):
    uid = msg.from_user.id
    s = get_state(uid)
    
    if s['busy']:
        await msg.answer(f'{e("clock")}⏰</tg-emoji> Идёт генерация...', parse_mode=ParseMode.HTML)
        return
    
    if not s['chat']:
        s['chat'] = str(uuid.uuid4())
    
    model = s['model']
    cid = s['chat']
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO chats (user_id, chat_id, title, model, role, content) VALUES (%s,%s,%s,%s,%s,%s)",
                (uid, cid, msg.text[:50], model, 'user', msg.text))
    cur.execute("SELECT role, content FROM chats WHERE user_id=%s AND chat_id=%s ORDER BY timestamp ASC LIMIT 20", (uid, cid))
    hist = [{"role": r[0], "content": r[1]} for r in cur.fetchall()]
    conn.commit()
    conn.close()
    
    msgs = [{"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."}] + hist
    
    model_name = available_models.get(model, model)
    st = await msg.answer(
        f'{e("loading")}🔄</tg-emoji> <i>Думаю...</i> ({model_name})\n{e("clock")}⏰</tg-emoji> 0с',
        parse_mode=ParseMode.HTML
    )
    s['busy'] = True
    
    async def timer():
        start = datetime.now()
        while s['busy']:
            sec = (datetime.now() - start).seconds
            await asyncio.sleep(5)
            if s['busy']:
                try:
                    await st.edit_text(
                        f'{e("loading")}🔄</tg-emoji> <i>Думаю...</i> ({model_name})\n{e("clock")}⏰</tg-emoji> {sec}с',
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
    
    tsk = asyncio.create_task(timer())
    
    try:
        ans = await call_api(model, msgs)
        s['busy'] = False
        tsk.cancel()
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO chats (user_id, chat_id, model, role, content) VALUES (%s,%s,%s,%s,%s)",
                    (uid, cid, model, 'assistant', ans))
        conn.commit()
        conn.close()
        
        if len(ans) > 4000:
            await st.delete()
            for i in range(0, len(ans), 4000):
                await msg.answer(ans[i:i+4000])
        else:
            await st.edit_text(ans)
    except Exception as ex:
        s['busy'] = False
        tsk.cancel()
        await st.edit_text(f'{e("cross")}❌</tg-emoji> {str(ex)[:500]}', parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "new_chat")
async def cb_new(cb: types.CallbackQuery):
    s = get_state(cb.from_user.id)
    s['chat'] = str(uuid.uuid4())
    model_name = available_models.get(s['model'], s['model'])
    await cb.message.edit_text(
        f'{e("check")}✅</tg-emoji> Новый чат!\n{e("settings")}⚙️</tg-emoji> {model_name}',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
    )

@dp.callback_query(F.data == "my_chats")
async def cb_chats(cb: types.CallbackQuery):
    await cb.message.edit_text(f'{e("file")}📁</tg-emoji> <b>Ваши чаты:</b>', parse_mode=ParseMode.HTML, reply_markup=chats_kb(cb.from_user.id))

@dp.callback_query(F.data == "select_model")
async def cb_models(cb: types.CallbackQuery):
    await cb.message.edit_text(
        f'{e("settings")}⚙️</tg-emoji> <b>Выберите модель</b> ({len(available_models)} доступно):',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(0)
    )

@dp.callback_query(F.data == "refresh_models")
async def cb_refresh(cb: types.CallbackQuery):
    await cb.answer("🔄 Обновляю...")
    await fetch_models()
    await cb.message.edit_text(
        f'{e("check")}✅</tg-emoji> Модели обновлены! ({len(available_models)} шт.)',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(0)
    )

@dp.callback_query(F.data.startswith("models_page_"))
async def cb_models_page(cb: types.CallbackQuery):
    page = int(cb.data.split("_")[2])
    await cb.message.edit_text(
        f'{e("settings")}⚙️</tg-emoji> <b>Выберите модель</b> (стр. {page+1}):',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(page)
    )

@dp.callback_query(F.data.startswith("selmodel_"))
async def cb_model_set(cb: types.CallbackQuery):
    model_id = cb.data.split("_", 1)[1]
    get_state(cb.from_user.id)['model'] = model_id
    model_name = available_models.get(model_id, model_id)
    await cb.message.edit_text(
        f'{e("check")}✅</tg-emoji> Модель: <b>{model_name}</b>\n<code>{model_id}</code>',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
    )

@dp.callback_query(F.data.startswith("switch_"))
async def cb_switch(cb: types.CallbackQuery):
    cid = cb.data.split("_", 1)[1]
    get_state(cb.from_user.id)['chat'] = cid
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT content FROM chats WHERE user_id=%s AND chat_id=%s LIMIT 1", (cb.from_user.id, cid))
    row = cur.fetchone()
    conn.close()
    t = row[0][:30] if row else "Новый чат"
    await cb.message.edit_text(f'{e("smile")}🙂</tg-emoji> {t}\n{e("info")}ℹ️</tg-emoji> Отправьте сообщение!', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "clear_all")
async def cb_clear(cb: types.CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chats WHERE user_id=%s", (cb.from_user.id,))
    conn.commit()
    conn.close()
    get_state(cb.from_user.id)['chat'] = str(uuid.uuid4())
    await cb.message.edit_text(f'{e("trash")}🗑️</tg-emoji> Все чаты удалены!', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: types.CallbackQuery):
    s = get_state(cb.from_user.id)
    model_name = available_models.get(s['model'], s['model'])
    await cb.message.edit_text(
        f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n{e("settings")}⚙️</tg-emoji> {model_name}',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
    )

async def main():
    print("🤖 Bot starting...")
    print("📋 Fetching models...")
    await fetch_models()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
