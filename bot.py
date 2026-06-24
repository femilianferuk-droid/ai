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
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import uuid

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not DATABASE_URL:
    print("❌ BOT_TOKEN или DATABASE_URL не установлены!")
    sys.exit(1)

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
    "star": "6041731551845159060",
    "gear": "5870982283724328568",
    "profile": "5870994129244131212",
    "key": "6037249452824072506",
    "link": "5769289093221454192",
    "globe": "5873147866364514353",
    "save": "5870633910337015697",
    "edit": "5870676941614354370",
}

def e(name):
    return f'<tg-emoji emoji-id="{EM[name]}">'

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

available_models = {}
user_states = {}

class ProfileStates(StatesGroup):
    waiting_api_key = State()
    waiting_base_url = State()

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
    cur.execute("""CREATE TABLE IF NOT EXISTS profiles (
        user_id BIGINT PRIMARY KEY, api_key TEXT, base_url TEXT)""")
    conn.commit()
    conn.close()
    print("✅ DB ready")

init_db()

def get_profile(uid):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM profiles WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        conn.close()
        print(f"🔍 get_profile uid={uid}: row={row}")
        if row and row["api_key"] and row["base_url"]:
            return {"api_key": row["api_key"], "base_url": row["base_url"]}
        return None
    except Exception as ex:
        print(f"❌ get_profile error: {ex}")
        return None

def save_profile(uid, api_key, base_url):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO profiles (user_id, api_key, base_url) VALUES (%s,%s,%s)
            ON CONFLICT (user_id) DO UPDATE SET api_key=%s, base_url=%s
        """, (uid, api_key, base_url, api_key, base_url))
        conn.commit()
        conn.close()
        print(f"✅ Profile saved: uid={uid}, key=...{api_key[-8:]}, url={base_url}")
        return True
    except Exception as ex:
        print(f"❌ save_profile error: {ex}")
        return False

def get_state(uid):
    if uid not in user_states:
        default = next((m for m, d in available_models.items() if d.get("works")), None)
        if not default and available_models:
            default = list(available_models.keys())[0]
        user_states[uid] = {'chat': None, 'model': default, 'busy': False}
    return user_states[uid]

async def test_model(model_id, api_key, base_url):
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": [{"role": "user", "content": "Hi"}]}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, ssl=False, timeout=aiohttp.ClientTimeout(total=15)) as r:
                raw = await r.text()
                if r.status == 200:
                    return True, None
                if '额度不足' in raw or 'insufficient' in raw.lower():
                    return False, "Нет квоты"
                if 'model_not_found' in raw:
                    return False, "Не найдена"
                return False, f"Ошибка {r.status}"
    except Exception as ex:
        return False, str(ex)[:50]

async def fetch_models(api_key, base_url):
    global available_models
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, ssl=False) as r:
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
                    
                    test_ids = list(available_models.keys())
                    for mid in test_ids[:8]:
                        works, _ = await test_model(mid, api_key, base_url)
                        available_models[mid]["works"] = works
                        available_models[mid]["name"] = mid.replace("-", " ").title()[:30]
                    
                    for mid in test_ids[8:]:
                        available_models[mid]["name"] = mid.replace("-", " ").title()[:30]
                    
                    return available_models
    except Exception as ex:
        print(f"Error: {ex}")
    
    if not available_models:
        available_models = {}
    return available_models

async def call_api(model, messages, api_key, base_url):
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.7}
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        async with s.post(url, headers=headers, json=payload, ssl=False) as r:
            raw = await r.text()
            if r.status == 200:
                return json.loads(raw)["choices"][0]["message"]["content"]
            
            try:
                err_data = json.loads(raw)
                err_msg = err_data.get("error", {}).get("message", raw)
            except:
                err_msg = raw
            
            if '额度不足' in raw or 'insufficient' in raw.lower():
                raise Exception(f"Нет квоты у модели {model}")
            
            raise Exception(f"{err_msg[:300]}")

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
        icon = EM["check"] if works is True else (EM["cross"] if works is False else EM["clock"])
        name = data.get("name", mid)[:25]
        kb.add(InlineKeyboardButton(text=name, callback_data=f"use_{mid}", icon_custom_emoji_id=icon))
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"mp_{page-1}", icon_custom_emoji_id=EM["back"]))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="Далее", callback_data=f"mp_{page+1}", icon_custom_emoji_id=EM["loading"]))
    if nav:
        kb.row(*nav)
    
    kb.add(InlineKeyboardButton(text="Проверить все", callback_data="refresh_models", icon_custom_emoji_id=EM["reload"]))
    kb.add(InlineKeyboardButton(text="Назад", callback_data="back_main", icon_custom_emoji_id=EM["back"]))
    kb.adjust(1)
    return kb.as_markup()

def main_kb():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="Новый чат", callback_data="new_chat", icon_custom_emoji_id=EM["pencil"]))
    kb.add(InlineKeyboardButton(text="Мои чаты", callback_data="my_chats", icon_custom_emoji_id=EM["file"]))
    kb.add(InlineKeyboardButton(text="Модель", callback_data="select_model", icon_custom_emoji_id=EM["settings"]))
    kb.add(InlineKeyboardButton(text="Профиль", callback_data="profile", icon_custom_emoji_id=EM["profile"]))
    kb.adjust(2, 2)
    return kb.as_markup()

def profile_kb():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="API ключ", callback_data="set_api_key", icon_custom_emoji_id=EM["key"]))
    kb.add(InlineKeyboardButton(text="Base URL", callback_data="set_base_url", icon_custom_emoji_id=EM["link"]))
    kb.add(InlineKeyboardButton(text="Сбросить", callback_data="reset_profile", icon_custom_emoji_id=EM["trash"]))
    kb.add(InlineKeyboardButton(text="Назад", callback_data="back_main", icon_custom_emoji_id=EM["back"]))
    kb.adjust(2, 1, 1)
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
    profile = get_profile(msg.from_user.id)
    if profile:
        await fetch_models(profile["api_key"], profile["base_url"])
    
    s = get_state(msg.from_user.id)
    s['chat'] = str(uuid.uuid4())
    m = s['model']
    name = available_models.get(m, {}).get("name", m) if m else "не выбрана"
    
    txt = f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n\n'
    txt += f'{e("settings")}⚙️</tg-emoji> Модель: {name}\n'
    if profile:
        txt += f'{e("link")}🔗</tg-emoji> API настроен\n'
    else:
        txt += f'{e("info")}ℹ️</tg-emoji> Настройте API в Профиле\n'
    txt += f'{e("info")}ℹ️</tg-emoji> Отправьте сообщение!'
    
    await msg.answer(txt, parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.message(Command("profile"))
async def cmd_profile(msg: types.Message):
    profile = get_profile(msg.from_user.id)
    if profile:
        txt = f'{e("profile")}👤</tg-emoji> <b>Ваш профиль:</b>\n\n'
        txt += f'{e("key")}🔑</tg-emoji> API ключ: <code>...{profile["api_key"][-8:]}</code>\n'
        txt += f'{e("link")}🔗</tg-emoji> Base URL: <code>{profile["base_url"]}</code>'
    else:
        txt = f'{e("cross")}❌</tg-emoji> Профиль не настроен'
    await msg.answer(txt, parse_mode=ParseMode.HTML, reply_markup=profile_kb())

@dp.message(F.text)
async def handle(msg: types.Message):
    uid = msg.from_user.id
    s = get_state(uid)
    if s['busy']:
        await msg.answer(f'{e("clock")}⏰</tg-emoji> Идёт генерация...', parse_mode=ParseMode.HTML)
        return
    
    profile = get_profile(uid)
    if not profile:
        await msg.answer(
            f'{e("cross")}❌</tg-emoji> <b>Не настроен API!</b>\n\nНажмите "Профиль" чтобы добавить API ключ и Base URL.',
            parse_mode=ParseMode.HTML, reply_markup=main_kb()
        )
        return
    
    if not s['chat']:
        s['chat'] = str(uuid.uuid4())
    
    model = s['model']
    if not model:
        await msg.answer(f'{e("cross")}❌</tg-emoji> Выберите модель!', parse_mode=ParseMode.HTML, reply_markup=main_kb())
        return
    
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
        ans = await call_api(model, msgs, profile["api_key"], profile["base_url"])
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

@dp.callback_query(F.data == "profile")
async def cb_profile(cb: types.CallbackQuery):
    profile = get_profile(cb.from_user.id)
    txt = f'{e("profile")}👤</tg-emoji> <b>Профиль</b>\n\n'
    if profile:
        txt += f'{e("key")}🔑</tg-emoji> API ключ: <code>...{profile["api_key"][-8:]}</code>\n'
        txt += f'{e("link")}🔗</tg-emoji> Base URL: <code>{profile["base_url"]}</code>\n'
        txt += f'{e("check")}✅</tg-emoji> Статус: настроен'
    else:
        txt += f'{e("cross")}❌</tg-emoji> API не настроен\n\nНажмите кнопку ниже чтобы добавить ключ'
    await cb.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=profile_kb())
    await cb.answer()

@dp.callback_query(F.data == "set_api_key")
async def cb_set_key(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_api_key)
    await cb.message.edit_text(
        f'{e("key")}🔑</tg-emoji> <b>Отправьте ваш API ключ:</b>\n\nПример: <code>sk-xxxxxxxxxxxxx</code>\n\nТекущий: <code>...{get_profile(cb.from_user.id)["api_key"][-8:] if get_profile(cb.from_user.id) else "не задан"}</code>',
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardBuilder().add(InlineKeyboardButton(text="Отмена", callback_data="profile", icon_custom_emoji_id=EM["back"])).as_markup()
    )
    await cb.answer()

@dp.callback_query(F.data == "set_base_url")
async def cb_set_url(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_base_url)
    await cb.message.edit_text(
        f'{e("link")}🔗</tg-emoji> <b>Отправьте Base URL:</b>\n\nПример: <code>https://gpt-agent.cc/v1</code>\n\nТекущий: <code>{get_profile(cb.from_user.id)["base_url"] if get_profile(cb.from_user.id) else "не задан"}</code>',
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardBuilder().add(InlineKeyboardButton(text="Отмена", callback_data="profile", icon_custom_emoji_id=EM["back"])).as_markup()
    )
    await cb.answer()

@dp.callback_query(F.data == "reset_profile")
async def cb_reset(cb: types.CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM profiles WHERE user_id=%s", (cb.from_user.id,))
    conn.commit()
    conn.close()
    global available_models
    available_models = {}
    await cb.answer("Сброшено")
    await cb_profile(cb)

@dp.message(ProfileStates.waiting_api_key)
async def msg_api_key(msg: types.Message, state: FSMContext):
    api_key = msg.text.strip()
    profile = get_profile(msg.from_user.id)
    base_url = profile["base_url"] if profile else "https://gpt-agent.cc/v1"
    
    ok = save_profile(msg.from_user.id, api_key, base_url)
    await state.clear()
    
    if ok:
        await msg.answer(
            f'{e("check")}✅</tg-emoji> API ключ сохранён!\n\nКлюч: <code>...{api_key[-8:]}</code>\nURL: <code>{base_url}</code>',
            parse_mode=ParseMode.HTML, reply_markup=main_kb()
        )
    else:
        await msg.answer(
            f'{e("cross")}❌</tg-emoji> Ошибка сохранения! Попробуйте ещё раз.',
            parse_mode=ParseMode.HTML, reply_markup=main_kb()
        )

@dp.message(ProfileStates.waiting_base_url)
async def msg_base_url(msg: types.Message, state: FSMContext):
    base_url = msg.text.strip().rstrip("/")
    profile = get_profile(msg.from_user.id)
    api_key = profile["api_key"] if profile else ""
    
    ok = save_profile(msg.from_user.id, api_key, base_url)
    await state.clear()
    
    if ok:
        await msg.answer(
            f'{e("check")}✅</tg-emoji> Base URL сохранён!\n\nURL: <code>{base_url}</code>',
            parse_mode=ParseMode.HTML, reply_markup=main_kb()
        )
    else:
        await msg.answer(
            f'{e("cross")}❌</tg-emoji> Ошибка сохранения! Попробуйте ещё раз.',
            parse_mode=ParseMode.HTML, reply_markup=main_kb()
        )

@dp.callback_query(F.data == "select_model")
async def cb_models(cb: types.CallbackQuery):
    profile = get_profile(cb.from_user.id)
    if not profile:
        await cb.answer("Сначала настройте API в Профиле", show_alert=True)
        return
    if not available_models:
        await cb.answer("Загружаю модели...")
        await fetch_models(profile["api_key"], profile["base_url"])
    working = sum(1 for d in available_models.values() if d.get("works") is True)
    await cb.message.edit_text(
        f'{e("settings")}⚙️</tg-emoji> <b>Модели</b> ({working} из {len(available_models)} работают):',
        parse_mode=ParseMode.HTML, reply_markup=model_kb(0)
    )

@dp.callback_query(F.data == "refresh_models")
async def cb_refresh(cb: types.CallbackQuery):
    profile = get_profile(cb.from_user.id)
    if not profile:
        return
    await cb.answer("Проверяю модели...")
    await fetch_models(profile["api_key"], profile["base_url"])
    working = sum(1 for d in available_models.values() if d.get("works") is True)
    await cb.message.edit_text(
        f'{e("check")}✅</tg-emoji> Проверено! {working} рабочих из {len(available_models)}',
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
    name = available_models.get(s['model'], {}).get("name", s['model']) if s['model'] else "не выбрана"
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
    name = available_models.get(s['model'], {}).get("name", s['model']) if s['model'] else "не выбрана"
    await cb.message.edit_text(f'{e("bot")}🤖</tg-emoji> <b>AI Агент</b>\n{e("settings")}⚙️</tg-emoji> {name}', parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "none")
async def cb_none(cb: types.CallbackQuery):
    await cb.answer("Нет чатов")

async def main():
    print("Starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
