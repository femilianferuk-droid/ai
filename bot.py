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

# Популярные модели gpt-agent.cc (из документации)
MODELS = {
    "claude": "claude-sonnet-4-6",
    "minimax": "minimax-M2.7",
    "kimi": "kimi-2.6",
    "deepseek": "deepseek-v4-flash"
}

MODEL_NAMES = {
    "claude": "Claude Sonnet 4.6",
    "minimax": "MiniMax M2.7",
    "kimi": "Kimi 2.6",
    "deepseek": "DeepSeek V4 Flash"
}

MODEL_EMOJI = {
    "claude": "5870982283724328568",
    "minimax": "5870930636742595124",
    "kimi": "5870921681735781843",
    "deepseek": "6030400221232501136"
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
}

def e(name):
    return f'<tg-emoji emoji-id="{EM[name]}">'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
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
        user_states[uid] = {'chat': None, 'model': 'deepseek', 'busy': False}
    return user_states[uid]

async def get_available_models():
    """Получает список доступных моделей"""
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
                    return models_list
    except Exception as e:
        print(f"Error fetching models: {e}")
    return []

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
    
    timeout = aiohttp.ClientTimeout(total=60)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print(f"🔄 Calling {model} with {len(messages)} messages")
        
        async with session.post(AI_API_URL, headers=headers, json=payload, ssl=False) as resp:
            raw = await resp.text()
            print(f"📡 Status={resp.status}, body={raw[:500]}")
            
            if resp.status == 200:
                data = json.loads(raw)
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
            
            if '额度不足' in raw or 'insufficient' in raw.lower():
                raise Exception("💰 Закончился баланс API")
            
            if 'model_not_found' in raw:
                # Пробуем без system message и с другой температурой
                payload["messages"] = [m for m in messages if m["role"] != "system"]
                payload["temperature"] = 0.5
                
                async with session.post(AI_API_URL, headers=headers, json=payload, ssl=False) as resp2:
                    raw2 = await resp2.text()
                    if resp2.status == 200:
                        data = json.loads(raw2)
                        return data["choices"][0]["message"]["content"]
                    
                    raise Exception(f"❌ Модель {model} недоступна. Попробуйте другую модель.")
            
            raise Exception(f"❌ API error {resp.status}: {raw[:300]}")

def main_kb():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="Новый чат", callback_data="new_chat", icon_custom_emoji_id=EM["pencil"]))
    kb.add(InlineKeyboardButton(text="Мои чаты", callback_data="my_chats", icon_custom_emoji_id=EM["file"]))
    kb.add(InlineKeyboardButton(text="Модель", callback_data="select_model", icon_custom_emoji_id=EM["settings"]))
    kb.adjust(2, 1)
    return kb.as_markup()

def model_kb():
    kb = InlineKeyboardBuilder()
    for k, v in MODEL_NAMES.items():
        kb.add(InlineKeyboardButton(text=v, callback_data=f"model_{k}", icon_custom_emoji_id=MODEL_EMOJI[k]))
    kb.add(InlineKeyboardButton(text="Назад", callback_data="back_main", icon_custom_emoji_id=EM["back"]))
    kb.adjust(1)
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
    s = get_state(msg.from_user.id)
    s['chat'] = str(uuid.uuid4())
    await msg.answer(
        f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n\n{e("settings")}⚙️</tg-emoji> {MODEL_NAMES[s["model"]]}\n{e("info")}ℹ️</tg-emoji> Отправьте сообщение!\n\n<i>Команда /models — проверить доступные модели</i>',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
    )

@dp.message(Command("models"))
async def cmd_models(msg: types.Message):
    """Проверка доступных моделей"""
    await msg.answer(f'{e("loading")}🔄</tg-emoji> Проверяю доступные модели...', parse_mode=ParseMode.HTML)
    
    models = await get_available_models()
    
    if models:
        text = f'{e("check")}✅</tg-emoji> <b>Доступные модели:</b>\n\n' + '\n'.join(f'• <code>{m}</code>' for m in models)
    else:
        text = f'{e("cross")}❌</tg-emoji> Не удалось получить список моделей'
    
    await msg.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text)
async def handle(msg: types.Message):
    uid = msg.from_user.id
    s = get_state(uid)
    
    if s['busy']:
        await msg.answer(f'{e("clock")}⏰</tg-emoji> Идёт генерация...', parse_mode=ParseMode.HTML)
        return
    
    if not s['chat']:
        s['chat'] = str(uuid.uuid4())
    
    model = MODELS[s['model']]
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
    
    st = await msg.answer(f'{e("loading")}🔄</tg-emoji> <i>Думаю...</i>\n{e("clock")}⏰</tg-emoji> 0с', parse_mode=ParseMode.HTML)
    s['busy'] = True
    
    async def timer():
        start = datetime.now()
        while s['busy']:
            sec = (datetime.now() - start).seconds
            await asyncio.sleep(5)
            if s['busy']:
                try:
                    await st.edit_text(f'{e("loading")}🔄</tg-emoji> <i>Думаю...</i>\n{e("clock")}⏰</tg-emoji> {sec}с', parse_mode=ParseMode.HTML)
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
    await cb.message.edit_text(f'{e("check")}✅</tg-emoji> Новый чат!\n{e("settings")}⚙️</tg-emoji> {MODEL_NAMES[s["model"]]}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "my_chats")
async def cb_chats(cb: types.CallbackQuery):
    await cb.message.edit_text(f'{e("file")}📁</tg-emoji> <b>Ваши чаты:</b>', parse_mode=ParseMode.HTML, reply_markup=chats_kb(cb.from_user.id))

@dp.callback_query(F.data == "select_model")
async def cb_models(cb: types.CallbackQuery):
    await cb.message.edit_text(f'{e("settings")}⚙️</tg-emoji> <b>Выберите модель:</b>', parse_mode=ParseMode.HTML, reply_markup=model_kb())

@dp.callback_query(F.data.startswith("model_"))
async def cb_model_set(cb: types.CallbackQuery):
    k = cb.data.split("_")[1]
    get_state(cb.from_user.id)['model'] = k
    await cb.message.edit_text(f'{e("check")}✅</tg-emoji> {MODEL_NAMES[k]}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

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
    await cb.message.edit_text(f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n{e("settings")}⚙️</tg-emoji> {MODEL_NAMES[s["model"]]}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

async def main():
    print("🤖 Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
