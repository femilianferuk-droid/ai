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

if not BOT_TOKEN or not DATABASE_URL:
    print("❌ BOT_TOKEN или DATABASE_URL не установлены!")
    sys.exit(1)

AI_API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"
AI_API_URL = "https://gpt-agent.cc/v1/chat/completions"
AI_MODELS_URL = "https://gpt-agent.cc/v1/models"

EM = {
    "bot": "6030400221232501136", "settings": "5870982283724328568",
    "file": "5870528606328852614", "check": "5870633910337015697",
    "cross": "5870657884844462243", "pencil": "5870676941614354370",
    "trash": "5870875489362513438", "info": "6028435952299413210",
    "clock": "5983150113483134607", "loading": "5345906554510012647",
    "back": "6039450962865688331", "smile": "5870764288364252592",
    "money": "5904462880941545555", "reload": "5345906554510012647",
    "star": "6041731551845159060",
}

def e(name): return f'<tg-emoji emoji-id="{EM[name]}">'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

available_models = {}
user_states = {}

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='chats' AND column_name='user_id'")
    if not cur.fetchone():
        cur.execute("DROP TABLE IF EXISTS chats")
    cur.execute("""CREATE TABLE IF NOT EXISTS chats (
        id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, chat_id TEXT NOT NULL,
        title TEXT DEFAULT 'Новый чат', timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        model TEXT, role TEXT, content TEXT)""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_chat ON chats(user_id, chat_id)")
    conn.commit()
    conn.close()
    print("✅ DB ready")

init_db()

def get_state(uid):
    if uid not in user_states:
        default = next((m for m, d in available_models.items() if d.get("works")), None)
        if not default and available_models:
            default = list(available_models.keys())[0]
        user_states[uid] = {'chat': None, 'model': default, 'busy': False}
    return user_states[uid]

async def test_model(model_id):
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(AI_API_URL, headers=headers, json=payload, ssl=False, timeout=aiohttp.ClientTimeout(total=15)) as r:
                raw = await r.text()
                if r.status == 200:
                    return True, None
                if '额度不足' in raw or 'insufficient' in raw.lower():
                    return False, "💰 Нет квоты"
                if 'model_not_found' in raw:
                    return False, "❌ Не найдена"
                return False, f"Ошибка {r.status}"
    except Exception as e:
        return False, str(e)[:50]

async def fetch_models():
    global available_models
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(AI_MODELS_URL, headers=headers, ssl=False) as r:
                if r.status == 200:
                    data = await r.json()
                    models_list = []
                    if isinstance(data, dict) and 'data' in data:
                        models_list = [m.get('id', '') for m in data['data']]
                    elif isinstance(data, list):
                        models_list = [m.get('id', '') if isinstance(m, dict) else str(m) for m in data]
                    
                    available_models = {}
                    for mid in sorted(set(models_list)):
                        if mid and not mid.startswith('#'):
                            available_models[mid] = {"name": mid, "works": None}
                    
                    print(f"📋 Found {len(available_models)} models, testing...")
                    
                    test_ids = list(available_models.keys())
                    for mid in test_ids[:8]:
                        works, err = await test_model(mid)
                        available_models[mid]["works"] = works
                        available_models[mid]["name"] = mid.replace("-", " ").title()[:30]
                        status = "✅" if works else f"❌ {err}"
                        print(f"  {status}: {mid}")
                    
                    for mid in test_ids[8:]:
                        available_models[mid]["name"] = mid.replace("-", " ").title()[:30]
                    
                    return available_models
    except Exception as e:
        print(f"❌ Error: {e}")
    
    if not available_models:
        available_models = {"claude-sonnet-4-6": {"name": "Claude Sonnet 4.6", "works": None}}
    return available_models

async def call_api(model, messages):
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.7}
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        async with s.post(AI_API_URL, headers=headers, json=payload, ssl=False) as r:
            raw = await r.text()
            if r.status == 200:
                return json.loads(raw)["choices"][0]["message"]["content"]
            if '额度不足' in raw:
                raise Exception("💰 У этой модели закончилась квота. Выберите другую модель.")
        
        payload["messages"] = [m for m in messages if m["role"] != "system"]
        payload["temperature"] = 0.5
        async with s.post(AI_API_URL, headers=headers, json=payload, ssl=False) as r:
            raw = await r.text()
            if r.status == 200:
                return json.loads(raw)["choices"][0]["message"]["content"]
        
        raise Exception(f"❌ Ошибка API: {raw[:200]}")

def model_kb(page=0):
    models = [(mid, d) for mid, d in available_models.items()]
    models.sort(key=lambda x: (not x[1].get("works") if x[1].get("works") is not None else 1, x[0]))
    
    per_page = 8
    total = (len(models) + per_page - 1) // per_page
    start = page * per_page
    page_models = models[start:start + per_page]
    
    kb = InlineKeyboardBuilder()
    
    for mid, data in page_models:
        works = data.get("works")
        if works is True:
            icon = EM["check"]
            prefix = "✅ "
        elif works is False:
            icon = EM["cross"]
            prefix = "❌ "
        else:
            icon = EM["clock"]
            prefix = "⏳ "
        
        name = data.get("name", mid)[:25]
        kb.add(InlineKeyboardButton(
            text=f"{prefix}{name}",
            callback_data=f"use_{mid}",
            icon_custom_emoji_id=icon
        ))
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◁", callback_data=f"mp_{page-1}", icon_custom_emoji_id=EM["back"]))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="▷", callback_data=f"mp_{page+1}", icon_custom_emoji_id=EM["loading"]))
    if nav:
        kb.row(*nav)
    
    kb.add(InlineKeyboardButton(text="🔄 Обновить и проверить", callback_data="refresh_models", icon_custom_emoji_id=EM["reload"]))
    kb.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_main", icon_custom_emoji_id=EM["back"]))
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
                t = (c['title'] or 'Новый чат')[:25]
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
    m = s['model']
    name = available_models.get(m, {}).get("name", m)
    await msg.answer(
        f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n\n{e("settings")}⚙️</tg-emoji> {name}\n{e("info")}ℹ️</tg-emoji> Отправьте сообщение!',
        parse_mode=ParseMode.HTML, reply_markup=main_kb()
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
    model_name = available_models.get(model, {}).get("name", model)
    
    st = await msg.answer(f'{e("loading")}🔄</tg-emoji> <i>{model_name} думает...</i>\n{e("clock")}⏰</tg-emoji> 0с', parse_mode=ParseMode.HTML)
    s['busy'] = True
    
    async def timer():
        start = datetime.now()
        while s['busy']:
            sec = (datetime.now() - start).seconds
            await asyncio.sleep(5)
            if s['busy']:
                try:
                    await st.edit_text(f'{e("loading")}🔄</tg-emoji> <i>{model_name} думает...</i>\n{e("clock")}⏰</tg-emoji> {sec}с', parse_mode=ParseMode.HTML)
                except: pass
    
    tsk = asyncio.create_task(timer())
    try:
        ans = await call_api(model, msgs)
        s['busy'] = False
        tsk.cancel()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO chats (user_id, chat_id, model, role, content) VALUES (%s,%s,%s,%s,%s)", (uid, cid, model, 'assistant', ans))
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

@dp.callback_query(F.data == "select_model")
async def cb_models(cb: types.CallbackQuery):
    working = sum(1 for d in available_models.values() if d.get("works") is True)
    await cb.message.edit_text(
        f'{e("settings")}⚙️</tg-emoji> <b>Модели</b> (✅{working} работают из {len(available_models)}):\n'
        f'<i>✅ проверена ❌ нет квоты ⏳ не проверена</i>',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(0)
    )

@dp.callback_query(F.data == "refresh_models")
async def cb_refresh(cb: types.CallbackQuery):
    await cb.answer("🔄 Проверяю модели...")
    await fetch_models()
    working = sum(1 for d in available_models.values() if d.get("works") is True)
    await cb.message.edit_text(
        f'{e("check")}✅</tg-emoji> Проверено! ✅{working} рабочих из {len(available_models)}',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(0)
    )

@dp.callback_query(F.data.startswith("mp_"))
async def cb_page(cb: types.CallbackQuery):
    page = int(cb.data.split("_")[1])
    await cb.message.edit_text(f'{e("settings")}⚙️</tg-emoji> <b>Модели</b>:', parse_mode=ParseMode.HTML, reply_markup=model_kb(page))

@dp.callback_query(F.data.startswith("use_"))
async def cb_use(cb: types.CallbackQuery):
    mid = cb.data.split("_", 1)[1]
    get_state(cb.from_user.id)['model'] = mid
    name = available_models.get(mid, {}).get("name", mid)
    await cb.message.edit_text(f'{e("check")}✅</tg-emoji> <b>{name}</b>\n<code>{mid}</code>', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "new_chat")
async def cb_new(cb: types.CallbackQuery):
    s = get_state(cb.from_user.id)
    s['chat'] = str(uuid.uuid4())
    name = available_models.get(s['model'], {}).get("name", s['model'])
    await cb.message.edit_text(f'{e("check")}✅</tg-emoji> Новый чат!\n{e("settings")}⚙️</tg-emoji> {name}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "my_chats")
async def cb_chats(cb: types.CallbackQuery):
    await cb.message.edit_text(f'{e("file")}📁</tg-emoji> <b>Ваши чаты:</b>', parse_mode=ParseMode.HTML, reply_markup=chats_kb(cb.from_user.id))

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
    name = available_models.get(s['model'], {}).get("name", s['model'])
    await cb.message.edit_text(f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n{e("settings")}⚙️</tg-emoji> {name}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

async def main():
    print("🤖 Starting...")
    await fetch_models()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
