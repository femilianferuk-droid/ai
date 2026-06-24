import asyncio
import os
import sys
from datetime import datetime
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.enums import ParseMode
import uuid

# Окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен!")
    sys.exit(1)
if not DATABASE_URL:
    print("❌ DATABASE_URL не установлен!")
    sys.exit(1)

# OpenAI клиент
client = OpenAI(
    api_key="sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB",
    base_url="https://gpt-agent.cc/v1"
)

MODELS = {
    "claude": "claude-sonnet-4.6",
    "minimax": "minimax-M2.7",
    "kimi": "KIMI-2.6",
    "deepseek": "DEEPSEEK-V4-FLASH"
}

MODEL_NAMES = {
    "claude": "Claude Sonnet 4.6",
    "minimax": "MiniMax M2.7",
    "kimi": "KIMI 2.6",
    "deepseek": "DeepSeek V4 Flash"
}

MODEL_EMOJI = {
    "claude": "5870982283724328568",
    "minimax": "5870930636742595124",
    "kimi": "5870921681735781843",
    "deepseek": "6030400221232501136"
}

# Премиум эмодзи ID
EMOJI = {
    "bot": "6030400221232501136",
    "settings": "5870982283724328568",
    "profile": "5870994129244131212",
    "people": "5870772616305839506",
    "file": "5870528606328852614",
    "stats": "5870930636742595124",
    "stats2": "5870921681735781843",
    "lock": "6037249452824072506",
    "unlock": "6037496202990194718",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "pencil": "5870676941614354370",
    "trash": "5870875489362513438",
    "link": "5769289093221454192",
    "info": "6028435952299413210",
    "eye": "6037397706505195857",
    "send": "5963103826075456248",
    "download": "6039802767931871481",
    "bell": "6039486778597970865",
    "gift": "6032644646587338669",
    "clock": "5983150113483134607",
    "party": "6041731551845159060",
    "photo": "6035128606563241721",
    "location": "6042011682497106307",
    "wallet": "5769126056262898415",
    "box": "5884479287171485878",
    "calendar": "5890937706803894250",
    "tag": "5886285355279193209",
    "time": "5775896410780079073",
    "apps": "5778672437122045013",
    "money": "5904462880941545555",
    "money_send": "5890848474563352982",
    "money_get": "5879814368572478751",
    "code": "5940433880585605708",
    "loading": "5345906554510012647",
    "back": "6039450962865688331",
    "megaphone": "6039422865189638057",
    "home": "5873147866364514353",
    "smile": "5870764288364252592",
    "write": "5870753782874246579",
    "brush": "6050679691004612757",
    "text_add": "5771851822897566479",
    "format": "5778479949572738874",
    "down": "5893057118545646106",
    "clip": "6039451237743595514",
    "hidden": "6037243349675544634"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_states = {}

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
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
    except Exception as e:
        print(f"❌ DB error: {e}")

init_db()

def get_user_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = {
            'current_chat': None,
            'current_model': 'claude',
            'processing': False
        }
    return user_states[user_id]

def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(
        text="Новый чат",
        callback_data="new_chat",
        icon_custom_emoji_id=EMOJI["pencil"]
    ))
    kb.add(InlineKeyboardButton(
        text="Мои чаты",
        callback_data="my_chats",
        icon_custom_emoji_id=EMOJI["file"]
    ))
    kb.add(InlineKeyboardButton(
        text="Модель",
        callback_data="select_model",
        icon_custom_emoji_id=EMOJI["settings"]
    ))
    kb.adjust(2, 1)
    return kb.as_markup()

def model_keyboard():
    kb = InlineKeyboardBuilder()
    for key, name in MODEL_NAMES.items():
        kb.add(InlineKeyboardButton(
            text=name,
            callback_data=f"model_{key}",
            icon_custom_emoji_id=MODEL_EMOJI[key]
        ))
    kb.add(InlineKeyboardButton(
        text="Назад",
        callback_data="back_main",
        icon_custom_emoji_id=EMOJI["back"]
    ))
    kb.adjust(1)
    return kb.as_markup()

def chats_keyboard(user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT chat_id, MAX(title) as title, COUNT(*) as cnt
            FROM chats WHERE user_id = %s
            GROUP BY chat_id ORDER BY MAX(timestamp) DESC LIMIT 10
        """, (user_id,))
        chats = cur.fetchall()
        conn.close()
        
        kb = InlineKeyboardBuilder()
        if chats:
            for chat in chats:
                title = (chat['title'] or 'Новый чат')[:30]
                kb.add(InlineKeyboardButton(
                    text=f"{title} ({chat['cnt']})",
                    callback_data=f"switch_{chat['chat_id']}",
                    icon_custom_emoji_id=EMOJI["smile"]
                ))
            kb.add(InlineKeyboardButton(
                text="Очистить всё",
                callback_data="clear_all",
                icon_custom_emoji_id=EMOJI["trash"]
            ))
        else:
            kb.add(InlineKeyboardButton(
                text="Нет чатов",
                callback_data="none",
                icon_custom_emoji_id=EMOJI["info"]
            ))
        kb.add(InlineKeyboardButton(
            text="Назад",
            callback_data="back_main",
            icon_custom_emoji_id=EMOJI["back"]
        ))
        kb.adjust(1)
        return kb.as_markup()
    except Exception as e:
        print(f"Error: {e}")
        return main_keyboard()

def reply_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="Новый чат", icon_custom_emoji_id=EMOJI["pencil"]))
    kb.add(KeyboardButton(text="Мои чаты", icon_custom_emoji_id=EMOJI["file"]))
    kb.add(KeyboardButton(text="Модель", icon_custom_emoji_id=EMOJI["settings"]))
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state = get_user_state(message.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    
    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI["bot"]}">🤖</tg-emoji> <b>AI Агент</b>\n\n'
        f'<tg-emoji emoji-id="{MODEL_EMOJI[state["current_model"]]}">⚙️</tg-emoji> '
        f'Модель: {MODEL_NAMES[state["current_model"]]}\n'
        f'<tg-emoji emoji-id="{EMOJI["info"]}">ℹ️</tg-emoji> Чат создан. Отправьте сообщение!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    state = get_user_state(message.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI["check"]}">✅</tg-emoji> Новый чат создан!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI["settings"]}">⚙️</tg-emoji> <b>Выберите модель:</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=model_keyboard()
    )

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if message.text in ["Новый чат", "Мои чаты", "Модель"]:
        if message.text == "Новый чат":
            state['current_chat'] = str(uuid.uuid4())
            await message.answer(
                f'<tg-emoji emoji-id="{EMOJI["check"]}">✅</tg-emoji> Новый чат создан!',
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard()
            )
        elif message.text == "Мои чаты":
            await message.answer(
                f'<tg-emoji emoji-id="{EMOJI["file"]}">📁</tg-emoji> <b>Ваши чаты:</b>',
                parse_mode=ParseMode.HTML,
                reply_markup=chats_keyboard(user_id)
            )
        elif message.text == "Модель":
            await message.answer(
                f'<tg-emoji emoji-id="{EMOJI["settings"]}">⚙️</tg-emoji> <b>Выберите модель:</b>',
                parse_mode=ParseMode.HTML,
                reply_markup=model_keyboard()
            )
        return
    
    if state['processing']:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI["clock"]}">⏰</tg-emoji> Идёт генерация ответа...',
            parse_mode=ParseMode.HTML
        )
        return
    
    if not state['current_chat']:
        state['current_chat'] = str(uuid.uuid4())
    
    model = MODELS[state['current_model']]
    chat_id = state['current_chat']
    
    # Сохраняем сообщение
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chats (user_id, chat_id, title, model, role, content) VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, chat_id, message.text[:50], model, 'user', message.text)
    )
    
    cur.execute(
        "SELECT role, content FROM chats WHERE user_id = %s AND chat_id = %s ORDER BY timestamp ASC LIMIT 20",
        (user_id, chat_id)
    )
    history = [{"role": r[0], "content": r[1]} for r in cur.fetchall()]
    conn.commit()
    conn.close()
    
    messages = [{"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."}] + history
    
    status_msg = await message.answer(
        f'<tg-emoji emoji-id="{EMOJI["loading"]}">🔄</tg-emoji> <i>Думаю...</i>\n'
        f'<tg-emoji emoji-id="{EMOJI["clock"]}">⏰</tg-emoji> 0с',
        parse_mode=ParseMode.HTML
    )
    state['processing'] = True
    
    async def update_timer():
        start = datetime.now()
        while state['processing']:
            elapsed = (datetime.now() - start).seconds
            await asyncio.sleep(5)
            if state['processing']:
                try:
                    await status_msg.edit_text(
                        f'<tg-emoji emoji-id="{EMOJI["loading"]}">🔄</tg-emoji> <i>Думаю...</i>\n'
                        f'<tg-emoji emoji-id="{EMOJI["clock"]}">⏰</tg-emoji> {elapsed}с',
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
    
    timer_task = asyncio.create_task(update_timer())
    
    try:
        # Пробуем с system message
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000
            )
        except:
            # Без system message
            resp = client.chat.completions.create(
                model=model,
                messages=history,
                temperature=0.5,
                max_tokens=1000
            )
        
        ai_response = resp.choices[0].message.content
        
        state['processing'] = False
        timer_task.cancel()
        
        # Сохраняем
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats (user_id, chat_id, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (user_id, chat_id, model, 'assistant', ai_response)
        )
        conn.commit()
        conn.close()
        
        if len(ai_response) > 4000:
            await status_msg.delete()
            for i in range(0, len(ai_response), 4000):
                await message.answer(ai_response[i:i+4000])
        else:
            await status_msg.edit_text(ai_response)
        
    except Exception as e:
        state['processing'] = False
        timer_task.cancel()
        await status_msg.edit_text(
            f'<tg-emoji emoji-id="{EMOJI["cross"]}">❌</tg-emoji> Ошибка: {str(e)[:500]}',
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data == "new_chat")
async def cb_new_chat(callback: types.CallbackQuery):
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["check"]}">✅</tg-emoji> Новый чат создан!\n'
        f'<tg-emoji emoji-id="{MODEL_EMOJI[state["current_model"]]}">⚙️</tg-emoji> '
        f'Модель: {MODEL_NAMES[state["current_model"]]}\n'
        f'<tg-emoji emoji-id="{EMOJI["info"]}">ℹ️</tg-emoji> Отправьте сообщение.',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "my_chats")
async def cb_my_chats(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["file"]}">📁</tg-emoji> <b>Ваши чаты:</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=chats_keyboard(callback.from_user.id)
    )

@dp.callback_query(F.data == "select_model")
async def cb_select_model(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["settings"]}">⚙️</tg-emoji> <b>Выберите модель:</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=model_keyboard()
    )

@dp.callback_query(F.data.startswith("model_"))
async def cb_model(callback: types.CallbackQuery):
    model_key = callback.data.split("_")[1]
    state = get_user_state(callback.from_user.id)
    state['current_model'] = model_key
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["check"]}">✅</tg-emoji> Модель: {MODEL_NAMES[model_key]}\n'
        f'<tg-emoji emoji-id="{EMOJI["info"]}">ℹ️</tg-emoji> Отправьте сообщение!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data.startswith("switch_"))
async def cb_switch_chat(callback: types.CallbackQuery):
    chat_id = callback.data.split("_", 1)[1]
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = chat_id
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM chats WHERE user_id = %s AND chat_id = %s ORDER BY timestamp ASC",
        (callback.from_user.id, chat_id)
    )
    history = cur.fetchall()
    conn.close()
    
    title = history[0][1][:30] if history else "Новый чат"
    
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["smile"]}">🙂</tg-emoji> Чат: {title}\n'
        f'<tg-emoji emoji-id="{EMOJI["stats"]}">📊</tg-emoji> Сообщений: {len(history)}\n'
        f'<tg-emoji emoji-id="{EMOJI["info"]}">ℹ️</tg-emoji> Отправьте сообщение!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "clear_all")
async def cb_clear_all(callback: types.CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chats WHERE user_id = %s", (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["trash"]}">🗑️</tg-emoji> Все чаты удалены!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "back_main")
async def cb_back(callback: types.CallbackQuery):
    state = get_user_state(callback.from_user.id)
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI["bot"]}">🤖</tg-emoji> <b>AI Агент</b>\n'
        f'<tg-emoji emoji-id="{MODEL_EMOJI[state["current_model"]]}">⚙️</tg-emoji> '
        f'Модель: {MODEL_NAMES[state["current_model"]]}\n'
        f'<tg-emoji emoji-id="{EMOJI["info"]}">ℹ️</tg-emoji> Отправьте сообщение!',
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard()
    )

async def main():
    print("🤖 Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
