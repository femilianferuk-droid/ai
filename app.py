from flask import Flask, request, jsonify
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import secrets
import json
import time
import uuid
import traceback
import sys

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Конфигурация БД
DATABASE_URL = "postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c"

# Конфигурация API
API_URL = "https://gpt-agent.cc/v1/chat/completions"
API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"

# Настройки повторных попыток
MAX_RETRIES = 3
RETRY_DELAY = 2  # секунды между попытками

MODELS = {
    "claude-sonnet-4.6": "claude-sonnet-4.6",
    "minimax-M2.7": "minimax-M2.7",
    "KIMI-2.6": "KIMI-2.6",
    "DEEPSEEK-V4-FLASH": "DEEPSEEK-V4-FLASH"
}

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", file=sys.stderr)
    sys.stderr.flush()

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        log("Database connection established")
        return conn
    except Exception as e:
        log(f"Database connection error: {e}", "ERROR")
        raise

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'chats'
            )
        """)
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            log("Creating new table 'chats'")
            cur.execute("""
                CREATE TABLE chats (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    title TEXT DEFAULT 'Новый чат',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    model TEXT,
                    role TEXT,
                    content TEXT
                )
            """)
            cur.execute("CREATE INDEX idx_chat_id ON chats(chat_id)")
            log("Table 'chats' created successfully")
        else:
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'chats' AND column_name = 'chat_id'
            """)
            if not cur.fetchone():
                log("Old table structure detected, recreating...", "WARNING")
                cur.execute("DROP TABLE chats")
                cur.execute("""
                    CREATE TABLE chats (
                        id SERIAL PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        title TEXT DEFAULT 'Новый чат',
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        model TEXT,
                        role TEXT,
                        content TEXT
                    )
                """)
                cur.execute("CREATE INDEX idx_chat_id ON chats(chat_id)")
                log("Table recreated successfully")
            else:
                log("Table already exists with correct structure")
        
        conn.commit()
        conn.close()
        log("Database initialization completed")
    except Exception as e:
        log(f"Database initialization error: {e}\n{traceback.format_exc()}", "ERROR")
        raise

init_db()

@app.route('/')
def index():
    return HTML_CONTENT

@app.route('/api/chat/new', methods=['POST'])
def new_chat():
    chat_id = str(uuid.uuid4())
    log(f"New chat created: {chat_id}")
    return jsonify({'chat_id': chat_id})

@app.route('/api/chat/list', methods=['GET'])
def chat_list():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT chat_id, 
                   MAX(title) as title, 
                   MIN(timestamp) as created, 
                   COUNT(*) as messages_count
            FROM chats 
            GROUP BY chat_id 
            ORDER BY created DESC
        """)
        chats = cur.fetchall()
        conn.close()
        log(f"Chat list loaded: {len(chats)} chats")
        return jsonify([dict(chat) for chat in chats])
    except Exception as e:
        log(f"Chat list error: {e}\n{traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/send', methods=['POST'])
def send_message():
    try:
        data = request.json
        user_message = data.get('message', '')
        model = data.get('model', 'claude-sonnet-4.6')
        chat_id = data.get('chat_id', 'default')
        
        log(f"Received message for chat {chat_id}, model: {model}, length: {len(user_message)}")
        
        if not user_message:
            log("Empty message received", "WARNING")
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Проверяем количество сообщений в чате
        cur.execute("SELECT COUNT(*) as cnt FROM chats WHERE chat_id = %s", (chat_id,))
        msg_count = cur.fetchone()['cnt']
        log(f"Messages in chat: {msg_count}")
        
        # Сохраняем сообщение пользователя
        title = user_message[:50] if msg_count == 0 else None
        cur.execute(
            "INSERT INTO chats (chat_id, title, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, title if title else 'Новый чат', model, 'user', user_message)
        )
        
        if msg_count == 0:
            cur.execute(
                "UPDATE chats SET title = %s WHERE chat_id = %s",
                (user_message[:50], chat_id)
            )
        
        # Получаем историю чата
        cur.execute(
            "SELECT role, content FROM chats WHERE chat_id = %s ORDER BY timestamp ASC LIMIT 20",
            (chat_id,)
        )
        history = cur.fetchall()
        messages = [{"role": msg['role'], "content": msg['content']} for msg in history]
        
        log(f"History loaded: {len(messages)} messages")
        
        # Формируем запрос к API
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке подробно и понятно."},
                *messages
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        log(f"API request prepared: model={model}, messages={len(payload['messages'])}")
        
        # Пробуем отправить запрос с повторными попытками
        ai_response = None
        last_error = None
        
        for attempt in range(MAX_RETRIES):
            try:
                log(f"API attempt {attempt + 1}/{MAX_RETRIES}")
                start_time = time.time()
                
                response = requests.post(
                    API_URL, 
                    json=payload, 
                    headers=headers, 
                    timeout=60
                )
                
                elapsed_time = time.time() - start_time
                
                log(f"API response: status={response.status_code}, time={elapsed_time:.2f}s, attempt={attempt+1}")
                
                if response.status_code == 200:
                    response_data = response.json()
                    log(f"API response parsed successfully, choices: {len(response_data.get('choices', []))}")
                    
                    if 'choices' in response_data and len(response_data['choices']) > 0:
                        ai_response = response_data['choices'][0]['message']['content']
                        log(f"AI response received, length: {len(ai_response)}")
                        break
                    else:
                        log(f"Unexpected API response structure: {response_data}", "ERROR")
                        last_error = "Unexpected API response structure"
                
                elif response.status_code == 503:
                    log(f"API 503 error (attempt {attempt+1}): {response.text[:200]}", "WARNING")
                    last_error = f"API 503 Service Unavailable"
                    
                    # Пробуем с другими параметрами
                    if attempt == 0:
                        log("Retrying with different parameters...")
                        payload["temperature"] = 0.5
                        payload["max_tokens"] = 1000
                    elif attempt == 1:
                        log("Retrying without system message...")
                        payload["messages"] = payload["messages"][1:]  # Убираем system message
                    
                    if attempt < MAX_RETRIES - 1:
                        log(f"Waiting {RETRY_DELAY}s before retry...")
                        time.sleep(RETRY_DELAY * (attempt + 1))
                
                elif response.status_code == 401:
                    log(f"API 401 Unauthorized: {response.text}", "ERROR")
                    last_error = "API ключ недействителен"
                    break
                
                elif response.status_code == 429:
                    log(f"API 429 Rate limit: {response.text}", "WARNING")
                    last_error = "Превышен лимит запросов"
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY * (attempt + 1) * 2)
                
                else:
                    log(f"API error {response.status_code}: {response.text[:300]}", "ERROR")
                    last_error = f"API error: {response.status_code}"
                    break
                    
            except requests.exceptions.Timeout:
                log(f"API timeout (attempt {attempt+1})", "WARNING")
                last_error = "Таймаут запроса к API"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    
            except requests.exceptions.ConnectionError as e:
                log(f"API connection error: {e}", "ERROR")
                last_error = f"Ошибка подключения: {str(e)}"
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    
            except Exception as e:
                log(f"Unexpected API error: {e}\n{traceback.format_exc()}", "ERROR")
                last_error = f"Неожиданная ошибка: {str(e)}"
                break
        
        if ai_response:
            # Сохраняем ответ AI
            cur.execute(
                "INSERT INTO chats (chat_id, model, role, content) VALUES (%s, %s, %s, %s)",
                (chat_id, model, 'assistant', ai_response)
            )
            conn.commit()
            conn.close()
            
            log(f"Chat completed successfully, response length: {len(ai_response)}")
            
            return jsonify({
                'response': ai_response,
                'model': model,
                'chat_id': chat_id,
                'response_time': round(time.time() - start_time, 2),
                'timestamp': datetime.now().isoformat(),
                'attempts': attempt + 1
            })
        else:
            conn.close()
            log(f"All API attempts failed. Last error: {last_error}", "ERROR")
            
            # Возвращаем детальную информацию об ошибке
            error_message = f"❌ Не удалось получить ответ от API после {MAX_RETRIES} попыток.\n\n"
            error_message += f"Последняя ошибка: {last_error}\n\n"
            error_message += "Возможные причины:\n"
            error_message += "• API сервер временно недоступен\n"
            error_message += "• Проверьте правильность API ключа\n"
            error_message += "• Проверьте лимиты запросов\n\n"
            error_message += "Попробуйте:\n"
            error_message += "• Отправить сообщение еще раз\n"
            error_message += "• Выбрать другую модель\n"
            error_message += "• Подождать несколько секунд"
            
            return jsonify({
                'error': 'API недоступен',
                'details': error_message,
                'status_code': 503
            }), 503
            
    except Exception as e:
        log(f"Send message error: {e}\n{traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/chat/history/<chat_id>', methods=['GET'])
def get_chat_history(chat_id):
    try:
        log(f"Loading history for chat: {chat_id}")
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM chats WHERE chat_id = %s ORDER BY timestamp ASC",
            (chat_id,)
        )
        messages = cur.fetchall()
        conn.close()
        log(f"History loaded: {len(messages)} messages")
        return jsonify([dict(msg) for msg in messages])
    except Exception as e:
        log(f"History error: {e}\n{traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/delete/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    try:
        log(f"Deleting chat: {chat_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM chats WHERE chat_id = %s", (chat_id,))
        conn.commit()
        conn.close()
        log(f"Chat deleted: {chat_id}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        log(f"Delete error: {e}\n{traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e)}), 500

@app.route('/api/test', methods=['GET'])
def test_api():
    """Тестовый эндпоинт для проверки API"""
    try:
        log("Testing API connection...")
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "claude-sonnet-4.6",
            "messages": [
                {"role": "user", "content": "Привет, ответь кратко"}
            ],
            "max_tokens": 50
        }
        
        response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        
        return jsonify({
            'status': response.status_code,
            'response': response.text[:500] if response.status_code != 200 else 'OK',
            'headers': dict(response.headers)
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

HTML_CONTENT = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Агент</title>
    <style>
        :root {
            --bg-primary: #0f0f0f;
            --bg-secondary: #1a1a1a;
            --bg-tertiary: #2a2a2a;
            --text-primary: #ffffff;
            --text-secondary: #a0a0a0;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --border: #333;
            --user-msg: #1e3a5f;
            --assistant-msg: #1a1a2e;
            --error-bg: #3a1a1a;
            --error-border: #ef4444;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            height: 100dvh;
            overflow: hidden;
            -webkit-tap-highlight-color: transparent;
        }
        
        .app {
            display: flex;
            height: 100vh;
            height: 100dvh;
        }
        
        .overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 90;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .overlay.active {
            opacity: 1;
        }
        
        .sidebar {
            width: 300px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            transition: transform 0.3s ease;
            z-index: 100;
        }
        
        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        
        .sidebar-logo {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 12px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .new-chat-btn {
            width: 100%;
            padding: 12px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s;
        }
        
        .new-chat-btn:active {
            transform: scale(0.98);
        }
        
        .chats-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
            -webkit-overflow-scrolling: touch;
        }
        
        .chat-item {
            padding: 12px;
            margin-bottom: 4px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-item:active {
            background: var(--bg-tertiary);
        }
        
        .chat-item.active {
            background: var(--bg-tertiary);
            border-left: 3px solid var(--accent);
        }
        
        .chat-item-content {
            flex: 1;
            min-width: 0;
        }
        
        .chat-item-title {
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        }
        
        .chat-item-meta {
            font-size: 12px;
            color: var(--text-secondary);
        }
        
        .chat-item-delete {
            background: none;
            border: none;
            color: #ef4444;
            cursor: pointer;
            padding: 8px;
            border-radius: 6px;
            font-size: 14px;
            opacity: 0;
            transition: all 0.2s;
        }
        
        .chat-item:hover .chat-item-delete,
        .chat-item:active .chat-item-delete {
            opacity: 1;
        }
        
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        
        .header {
            padding: 12px 16px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .menu-btn {
            width: 40px;
            height: 40px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 10px;
            color: var(--text-primary);
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 4px;
            padding: 8px;
            flex-shrink: 0;
        }
        
        .menu-btn span {
            display: block;
            width: 18px;
            height: 2px;
            background: var(--text-primary);
            border-radius: 2px;
        }
        
        .header-info {
            flex: 1;
            min-width: 0;
        }
        
        .header-title {
            font-size: 16px;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .model-select {
            padding: 8px 12px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            flex-shrink: 0;
            max-width: 160px;
        }
        
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            -webkit-overflow-scrolling: touch;
        }
        
        .message {
            max-width: 750px;
            margin: 0 auto 20px;
            display: flex;
            gap: 10px;
        }
        
        .message.user {
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            flex-shrink: 0;
        }
        
        .user .message-avatar {
            background: var(--accent);
        }
        
        .assistant .message-avatar {
            background: #8b5cf6;
        }
        
        .message-body {
            flex: 1;
            min-width: 0;
        }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .message-role {
            font-size: 13px;
            font-weight: 600;
        }
        
        .message-meta {
            font-size: 11px;
            color: var(--text-secondary);
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }
        
        .message-content {
            padding: 12px 16px;
            border-radius: 12px;
            line-height: 1.5;
            font-size: 14px;
            white-space: pre-wrap;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }
        
        .user .message-content {
            background: var(--user-msg);
        }
        
        .assistant .message-content {
            background: var(--assistant-msg);
        }
        
        .message-content.error {
            background: var(--error-bg);
            border: 1px solid var(--error-border);
        }
        
        .input-area {
            padding: 12px 16px;
            padding-bottom: max(12px, env(safe-area-inset-bottom));
            max-width: 750px;
            width: 100%;
            margin: 0 auto;
        }
        
        .input-wrapper {
            display: flex;
            gap: 8px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 4px;
            transition: border-color 0.2s;
        }
        
        .input-wrapper:focus-within {
            border-color: var(--accent);
        }
        
        #messageInput {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-primary);
            font-size: 16px;
            outline: none;
            resize: none;
            padding: 10px 12px;
            max-height: 120px;
            min-height: 44px;
            font-family: inherit;
        }
        
        #messageInput::placeholder {
            color: #666;
        }
        
        .send-btn {
            padding: 10px 16px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: all 0.2s;
            white-space: nowrap;
            flex-shrink: 0;
            min-width: 44px;
            min-height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .send-btn:active {
            transform: scale(0.95);
            background: var(--accent-hover);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .send-icon {
            font-size: 18px;
        }
        
        .typing-indicator {
            display: flex;
            gap: 4px;
            padding: 4px 0;
        }
        
        .typing-dot {
            width: 6px;
            height: 6px;
            background: var(--accent);
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }
        
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        
        @keyframes typing {
            0%, 60%, 100% { opacity: 0.3; }
            30% { opacity: 1; }
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        
        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }
        
        .log-panel {
            background: var(--bg-secondary);
            border-top: 1px solid var(--border);
            padding: 8px 16px;
            font-size: 11px;
            color: var(--text-secondary);
            max-height: 100px;
            overflow-y: auto;
            font-family: monospace;
        }
        
        @media (max-width: 768px) {
            .sidebar {
                position: fixed;
                left: 0;
                top: 0;
                bottom: 0;
                transform: translateX(-100%);
                width: 85%;
                max-width: 320px;
                z-index: 100;
            }
            
            .sidebar.open {
                transform: translateX(0);
                box-shadow: 4px 0 20px rgba(0,0,0,0.3);
            }
            
            .overlay {
                display: block;
                pointer-events: none;
            }
            
            .overlay.active {
                pointer-events: all;
            }
            
            .message {
                max-width: 100%;
            }
            
            .header {
                padding: 8px 12px;
            }
            
            .header-title {
                font-size: 15px;
            }
            
            .model-select {
                font-size: 12px;
                padding: 6px 8px;
            }
            
            .input-area {
                padding: 8px 12px;
                padding-bottom: max(8px, env(safe-area-inset-bottom));
            }
            
            .send-btn {
                padding: 10px 14px;
            }
            
            .send-btn-text {
                display: none;
            }
            
            .message-content {
                font-size: 15px;
                padding: 10px 14px;
            }
        }
        
        @media (min-width: 769px) {
            .menu-btn {
                display: none;
            }
            
            .overlay {
                display: none !important;
            }
        }
    </style>
</head>
<body>
    <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
    
    <div class="app">
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-logo">🤖 AI Агент</div>
                <button class="new-chat-btn" onclick="createNewChat()">
                    ✨ Новый чат
                </button>
            </div>
            <div class="chats-list" id="chatsList"></div>
        </div>
        
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">
                    <span></span>
                    <span></span>
                    <span></span>
                </button>
                <div class="header-info">
                    <div class="header-title" id="headerTitle">AI Агент</div>
                </div>
                <select class="model-select" id="modelSelect" onchange="updateModel()">
                    <option value="claude-sonnet-4.6">Claude 4.6</option>
                    <option value="minimax-M2.7">MiniMax 2.7</option>
                    <option value="KIMI-2.6">KIMI 2.6</option>
                    <option value="DEEPSEEK-V4-FLASH">DeepSeek V4</option>
                </select>
            </div>
            
            <div class="messages" id="messagesContainer">
                <div class="empty-state">
                    <div class="empty-state-icon">🚀</div>
                    <div>Создайте новый чат и начните общение</div>
                </div>
            </div>
            
            <div class="log-panel" id="logPanel" style="display:none;"></div>
            
            <div class="input-area">
                <div class="input-wrapper">
                    <textarea 
                        id="messageInput" 
                        placeholder="Введите сообщение..."
                        rows="1"
                        onkeydown="handleKeyDown(event)"
                        oninput="autoResize(this)"
                    ></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">
                        <span class="send-btn-text">Отправить</span>
                        <span class="send-icon">↑</span>
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentChatId = null;
        let isProcessing = false;
        let currentModel = 'claude-sonnet-4.6';
        
        function addLog(message) {
            const panel = document.getElementById('logPanel');
            panel.style.display = 'block';
            const time = new Date().toLocaleTimeString();
            panel.innerHTML += `[${time}] ${message}<br>`;
            panel.scrollTop = panel.scrollHeight;
        }
        
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('overlay');
            sidebar.classList.toggle('open');
            overlay.classList.toggle('active');
        }
        
        function closeSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('overlay');
            sidebar.classList.remove('open');
            overlay.classList.remove('active');
        }
        
        async function init() {
            addLog('Инициализация приложения...');
            await loadChatsList();
            if (!currentChatId) {
                await createNewChat();
            }
        }
        
        async function createNewChat() {
            try {
                addLog('Создание нового чата...');
                const response = await fetch('/api/chat/new', { method: 'POST' });
                const data = await response.json();
                currentChatId = data.chat_id;
                addLog(`Чат создан: ${currentChatId}`);
                await loadChatsList();
                document.getElementById('messagesContainer').innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">💬</div>
                        <div>Новый чат создан. Начните общение!</div>
                    </div>
                `;
                document.getElementById('headerTitle').textContent = 'Новый чат';
                closeSidebar();
            } catch (error) {
                addLog(`Ошибка: ${error.message}`);
            }
        }
        
        async function loadChatsList() {
            try {
                const response = await fetch('/api/chat/list');
                const chats = await response.json();
                
                const container = document.getElementById('chatsList');
                container.innerHTML = '';
                
                chats.forEach(chat => {
                    const div = document.createElement('div');
                    div.className = `chat-item ${chat.chat_id === currentChatId ? 'active' : ''}`;
                    div.onclick = () => switchChat(chat.chat_id);
                    
                    const date = new Date(chat.created).toLocaleDateString('ru-RU');
                    
                    div.innerHTML = `
                        <div class="chat-item-content">
                            <div class="chat-item-title">${chat.title || 'Новый чат'}</div>
                            <div class="chat-item-meta">${date} • ${chat.messages_count} сообщ.</div>
                        </div>
                        <button class="chat-item-delete" onclick="deleteChat(event, '${chat.chat_id}')">✕</button>
                    `;
                    
                    container.appendChild(div);
                });
                
                addLog(`Загружено чатов: ${chats.length}`);
            } catch (error) {
                addLog(`Ошибка загрузки чатов: ${error.message}`);
            }
        }
        
        async function switchChat(chatId) {
            currentChatId = chatId;
            addLog(`Переключение на чат: ${chatId}`);
            await loadChatsList();
            closeSidebar();
            
            try {
                const response = await fetch(`/api/chat/history/${chatId}`);
                const messages = await response.json();
                
                const container = document.getElementById('messagesContainer');
                container.innerHTML = '';
                
                if (messages.length === 0) {
                    container.innerHTML = `
                        <div class="empty-state">
                            <div class="empty-state-icon">💬</div>
                            <div>Чат пуст. Начните общение!</div>
                        </div>
                    `;
                } else {
                    messages.forEach(msg => {
                        addMessageToUI(msg.role, msg.content, msg.model, msg.timestamp, null);
                    });
                }
                
                document.getElementById('headerTitle').textContent = 
                    messages.length > 0 ? messages[0].content.substring(0, 30) + '...' : 'Новый чат';
                    
            } catch (error) {
                addLog(`Ошибка: ${error.message}`);
            }
        }
        
        async function deleteChat(event, chatId) {
            event.stopPropagation();
            
            if (!confirm('Удалить этот чат?')) return;
            
            try {
                addLog(`Удаление чата: ${chatId}`);
                await fetch(`/api/chat/delete/${chatId}`, { method: 'DELETE' });
                
                if (currentChatId === chatId) {
                    await createNewChat();
                } else {
                    await loadChatsList();
                }
            } catch (error) {
                addLog(`Ошибка: ${error.message}`);
            }
        }
        
        function updateModel() {
            currentModel = document.getElementById('modelSelect').value;
            addLog(`Модель изменена: ${currentModel}`);
        }
        
        function addMessageToUI(role, content, model, timestamp, responseTime) {
            const container = document.getElementById('messagesContainer');
            
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) emptyState.remove();
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;
            
            const time = new Date(timestamp).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            const isError = content.includes('❌') || content.includes('Ошибка');
            
            messageDiv.innerHTML = `
                <div class="message-avatar">${role === 'user' ? '👤' : '🤖'}</div>
                <div class="message-body">
                    <div class="message-header">
                        <span class="message-role">${role === 'user' ? 'Вы' : 'AI Агент'}</span>
                        <div class="message-meta">
                            <span>${model}</span>
                            <span>${time}</span>
                            ${responseTime ? `<span>⏱ ${responseTime}с</span>` : ''}
                        </div>
                    </div>
                    <div class="message-content ${isError ? 'error' : ''}">${escapeHtml(content)}</div>
                </div>
            `;
            
            container.appendChild(messageDiv);
            container.scrollTop = container.scrollHeight;
        }
        
        function addTypingIndicator() {
            const container = document.getElementById('messagesContainer');
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) emptyState.remove();
            
            const indicator = document.createElement('div');
            indicator.className = 'message assistant';
            indicator.id = 'typingIndicator';
            indicator.innerHTML = `
                <div class="message-avatar">🤖</div>
                <div class="message-body">
                    <div class="message-header">
                        <span class="message-role">AI Агент</span>
                    </div>
                    <div class="message-content">
                        <div class="typing-indicator">
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                        </div>
                    </div>
                </div>
            `;
            
            container.appendChild(indicator);
            container.scrollTop = container.scrollHeight;
        }
        
        function removeTypingIndicator() {
            const indicator = document.getElementById('typingIndicator');
            if (indicator) indicator.remove();
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        async function sendMessage() {
            if (isProcessing) return;
            
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if (!message || !currentChatId) return;
            
            input.value = '';
            input.style.height = 'auto';
            
            isProcessing = true;
            document.getElementById('sendBtn').disabled = true;
            
            addLog(`Отправка: "${message.substring(0, 50)}..." модель: ${currentModel}`);
            addMessageToUI('user', message, currentModel, new Date().toISOString());
            addTypingIndicator();
            
            const startTime = Date.now();
            
            try {
                const response = await fetch('/api/chat/send', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: message,
                        model: currentModel,
                        chat_id: currentChatId
                    })
                });
                
                const data = await response.json();
                removeTypingIndicator();
                
                const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
                addLog(`Ответ получен за ${elapsed}с, статус: ${response.status}`);
                
                if (data.response) {
                    addMessageToUI('assistant', data.response, data.model, data.timestamp, data.response_time);
                    await loadChatsList();
                } else {
                    addMessageToUI('assistant', data.details || `❌ Ошибка: ${data.error || 'Неизвестная ошибка'}`, currentModel, new Date().toISOString());
                }
            } catch (error) {
                removeTypingIndicator();
                addLog(`Ошибка соединения: ${error.message}`);
                addMessageToUI('assistant', `❌ Ошибка соединения: ${error.message}`, currentModel, new Date().toISOString());
            } finally {
                isProcessing = false;
                document.getElementById('sendBtn').disabled = false;
            }
        }
        
        function handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }
        
        function autoResize(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
        }
        
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeSidebar();
        });
        
        window.onload = init;
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    log("Starting AI Agent server...")
    app.run(debug=True, host='0.0.0.0', port=5000)
