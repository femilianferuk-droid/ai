import asyncio
import os
import sys
from datetime import datetime
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
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

API_URL = "https://gpt-agent.cc/v1"
API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"

client_openai = OpenAI(api_key=API_KEY, base_url=API_URL)

MODELS = {
    "claude": "claude-sonnet-4.6",
    "minimax": "minimax-M2.7",
    "kimi": "KIMI-2.6",
    "deepseek": "DEEPSEEK-V4-FLASH"
}

MODEL_NAMES = {
    "claude": "🤖 Claude Sonnet 4.6",
    "minimax": "🌟 MiniMax M2.7",
    "kimi": "🚀 KIMI 2.6",
    "deepseek": "⚡ DeepSeek V4 Flash"
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
    kb.button(text="💬 Новый чат", callback_data="new_chat")
    kb.button(text="📋 Мои чаты", callback_data="my_chats")
    kb.button(text="⚙️ Модель", callback_data="select_model")
    kb.adjust(2, 1)
    return kb.as_markup()

def model_keyboard():
    kb = InlineKeyboardBuilder()
    for key, name in MODEL_NAMES.items():
        kb.button(text=name, callback_data=f"model_{key}")
    kb.button(text="🔙 Назад", callback_data="back_main")
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
                kb.button(
                    text=f"💬 {title} ({chat['cnt']})",
                    callback_data=f"switch_{chat['chat_id']}"
                )
            kb.button(text="🗑️ Очистить всё", callback_data="clear_all")
        else:
            kb.button(text="😴 Нет чатов", callback_data="none")
        kb.button(text="🔙 Назад", callback_data="back_main")
        kb.adjust(1)
        return kb.as_markup()
    except Exception as e:
        print(f"Error: {e}")
        return main_keyboard()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state = get_user_state(message.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    
    await message.answer(
        f"🤖 <b>AI Агент</b>\n\n"
        f"Модель: {MODEL_NAMES[state['current_model']]}\n"
        f"Чат создан. Отправьте сообщение!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    state = get_user_state(message.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    await message.answer("✅ Новый чат создан!", reply_markup=main_keyboard())

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    await message.answer("⚙️ Выберите модель:", reply_markup=model_keyboard())

@dp.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state['processing']:
        await message.answer("⏳ Идёт генерация ответа...")
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
    
    # Получаем историю
    cur.execute(
        "SELECT role, content FROM chats WHERE user_id = %s AND chat_id = %s ORDER BY timestamp ASC LIMIT 20",
        (user_id, chat_id)
    )
    history = [{"role": r[0], "content": r[1]} for r in cur.fetchall()]
    conn.commit()
    conn.close()
    
    messages = [{"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."}] + history
    
    # Отправляем статус
    status_msg = await message.answer("🤔 <i>Думаю...</i>\n⏱ 0с", parse_mode="HTML")
    state['processing'] = True
    
    # Запускаем обновление таймера
    async def update_timer():
        start = datetime.now()
        while state['processing']:
            elapsed = (datetime.now() - start).seconds
            await asyncio.sleep(5)
            if state['processing']:
                try:
                    await status_msg.edit_text(
                        f"🤔 <i>Думаю...</i>\n⏱ {elapsed}с",
                        parse_mode="HTML"
                    )
                except:
                    pass
    
    timer_task = asyncio.create_task(update_timer())
    
    try:
        # Запрос к API
        try:
            resp = client_openai.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000
            )
            ai_response = resp.choices[0].message.content
        except:
            # Ретрай без system
            resp = client_openai.chat.completions.create(
                model=model,
                messages=history,
                temperature=0.5,
                max_tokens=1000
            )
            ai_response = resp.choices[0].message.content
        
        state['processing'] = False
        timer_task.cancel()
        
        # Сохраняем ответ
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats (user_id, chat_id, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (user_id, chat_id, model, 'assistant', ai_response)
        )
        conn.commit()
        conn.close()
        
        # Отправляем ответ (разбиваем если длинный)
        if len(ai_response) > 4000:
            for i in range(0, len(ai_response), 4000):
                await message.answer(ai_response[i:i+4000])
            await status_msg.delete()
        else:
            await status_msg.edit_text(ai_response, parse_mode="HTML")
        
    except Exception as e:
        state['processing'] = False
        timer_task.cancel()
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:500]}", parse_mode="HTML")

@dp.callback_query(F.data == "new_chat")
async def cb_new_chat(callback: CallbackQuery):
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    await callback.message.edit_text(
        f"✅ Новый чат создан!\nМодель: {MODEL_NAMES[state['current_model']]}\nОтправьте сообщение.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "my_chats")
async def cb_my_chats(callback: CallbackQuery):
    await callback.message.edit_text(
        "📋 <b>Ваши чаты:</b>",
        parse_mode="HTML",
        reply_markup=chats_keyboard(callback.from_user.id)
    )

@dp.callback_query(F.data == "select_model")
async def cb_select_model(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ <b>Выберите модель:</b>",
        parse_mode="HTML",
        reply_markup=model_keyboard()
    )

@dp.callback_query(F.data.startswith("model_"))
async def cb_model(callback: CallbackQuery):
    model_key = callback.data.split("_")[1]
    state = get_user_state(callback.from_user.id)
    state['current_model'] = model_key
    await callback.message.edit_text(
        f"✅ Модель: {MODEL_NAMES[model_key]}\nОтправьте сообщение!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data.startswith("switch_"))
async def cb_switch_chat(callback: CallbackQuery):
    chat_id = callback.data.split("_", 1)[1]
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = chat_id
    
    # Загружаем историю
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM chats WHERE user_id = %s AND chat_id = %s ORDER BY timestamp ASC",
        (callback.from_user.id, chat_id)
    )
    history = cur.fetchall()
    conn.close()
    
    title = "Новый чат"
    if history:
        title = history[0][1][:30]
    
    await callback.message.edit_text(
        f"💬 Чат: {title}\nСообщений: {len(history)}\nОтправьте сообщение!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "clear_all")
async def cb_clear_all(callback: CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM chats WHERE user_id = %s", (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    state = get_user_state(callback.from_user.id)
    state['current_chat'] = str(uuid.uuid4())
    
    await callback.message.edit_text(
        "🗑️ Все чаты удалены!",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "back_main")
async def cb_back(callback: CallbackQuery):
    state = get_user_state(callback.from_user.id)
    await callback.message.edit_text(
        f"🤖 <b>AI Агент</b>\nМодель: {MODEL_NAMES[state['current_model']]}\nОтправьте сообщение!",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

async def main():
    print("🤖 Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
