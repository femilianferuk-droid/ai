from flask import Flask, request, jsonify
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import secrets
import time
import uuid
import traceback
import sys

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Конфигурация БД
DATABASE_URL = "postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c"

# Конфигурация API через OpenAI клиент
API_URL = "https://gpt-agent.cc/v1"
API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"

# Инициализация OpenAI клиента
client = OpenAI(
    api_key=API_KEY,
    base_url=API_URL
)

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
            log("Table created")
        else:
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'chats' AND column_name = 'chat_id'
            """)
            if not cur.fetchone():
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
                log("Table recreated")
        
        conn.commit()
        conn.close()
        log("Database ready")
    except Exception as e:
        log(f"DB init error: {e}", "ERROR")

init_db()

@app.route('/')
def index():
    return HTML_CONTENT

@app.route('/api/chat/new', methods=['POST'])
def new_chat():
    return jsonify({'chat_id': str(uuid.uuid4())})

@app.route('/api/chat/list', methods=['GET'])
def chat_list():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT chat_id, MAX(title) as title, MIN(timestamp) as created, COUNT(*) as messages_count
            FROM chats GROUP BY chat_id ORDER BY created DESC
        """)
        chats = cur.fetchall()
        conn.close()
        return jsonify([dict(chat) for chat in chats])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/send', methods=['POST'])
def send_message():
    try:
        data = request.json
        user_message = data.get('message', '')
        model = data.get('model', 'claude-sonnet-4.6')
        chat_id = data.get('chat_id', 'default')
        
        if not user_message:
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400
        
        log(f"📨 Message: '{user_message[:50]}...' | Model: {model}")
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Сохраняем сообщение пользователя
        cur.execute("SELECT COUNT(*) as cnt FROM chats WHERE chat_id = %s", (chat_id,))
        msg_count = cur.fetchone()['cnt']
        
        title = user_message[:50] if msg_count == 0 else None
        cur.execute(
            "INSERT INTO chats (chat_id, title, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, title or 'Новый чат', model, 'user', user_message)
        )
        
        if msg_count == 0:
            cur.execute("UPDATE chats SET title = %s WHERE chat_id = %s", (user_message[:50], chat_id))
        
        # Получаем историю
        cur.execute(
            "SELECT role, content FROM chats WHERE chat_id = %s ORDER BY timestamp ASC LIMIT 20",
            (chat_id,)
        )
        history = cur.fetchall()
        messages = [{"role": msg['role'], "content": msg['content']} for msg in history]
        
        # Формируем запрос
        system_msg = {"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."}
        all_messages = [system_msg] + messages
        
        log(f"🔄 Sending {len(all_messages)} messages to {model}...")
        
        start_time = time.time()
        
        # Используем OpenAI клиент
        try:
            response = client.chat.completions.create(
                model=model,
                messages=all_messages,
                temperature=0.7,
                max_tokens=2000
            )
            
            elapsed = time.time() - start_time
            ai_response = response.choices[0].message.content
            
            log(f"✅ Response received in {elapsed:.2f}s | Length: {len(ai_response)}")
            
            # Сохраняем ответ
            cur.execute(
                "INSERT INTO chats (chat_id, model, role, content) VALUES (%s, %s, %s, %s)",
                (chat_id, model, 'assistant', ai_response)
            )
            conn.commit()
            conn.close()
            
            return jsonify({
                'response': ai_response,
                'model': model,
                'chat_id': chat_id,
                'response_time': round(elapsed, 2),
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as api_error:
            elapsed = time.time() - start_time
            log(f"❌ API error after {elapsed:.2f}s: {api_error}", "ERROR")
            
            # Пробуем без system message
            try:
                log("🔄 Retrying without system message...")
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.5,
                    max_tokens=1000
                )
                
                ai_response = response.choices[0].message.content
                elapsed2 = time.time() - start_time
                log(f"✅ Retry successful in {elapsed2:.2f}s")
                
                cur.execute(
                    "INSERT INTO chats (chat_id, model, role, content) VALUES (%s, %s, %s, %s)",
                    (chat_id, model, 'assistant', ai_response)
                )
                conn.commit()
                conn.close()
                
                return jsonify({
                    'response': ai_response,
                    'model': model,
                    'chat_id': chat_id,
                    'response_time': round(elapsed2, 2),
                    'timestamp': datetime.now().isoformat()
                })
                
            except Exception as retry_error:
                log(f"❌ Retry also failed: {retry_error}", "ERROR")
                conn.close()
                
                error_msg = (
                    f"❌ Ошибка API: {str(api_error)}\n\n"
                    "Возможные причины:\n"
                    "• Модель недоступна\n"
                    "• API ключ недействителен\n"
                    "• Исчерпан лимит запросов\n\n"
                    "Попробуйте другую модель или проверьте API ключ"
                )
                
                return jsonify({
                    'error': 'API error',
                    'details': error_msg
                }), 500
                
    except Exception as e:
        log(f"💥 Fatal error: {e}\n{traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/history/<chat_id>', methods=['GET'])
def get_chat_history(chat_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM chats WHERE chat_id = %s ORDER BY timestamp ASC", (chat_id,))
        messages = cur.fetchall()
        conn.close()
        return jsonify([dict(msg) for msg in messages])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/delete/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM chats WHERE chat_id = %s", (chat_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test', methods=['GET'])
def test_api():
    """Тест всех моделей через OpenAI клиент"""
    results = []
    
    for model_name, model_id in MODELS.items():
        log(f"Testing {model_name}...")
        try:
            start = time.time()
            response = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "Привет"}],
                max_tokens=50
            )
            elapsed = time.time() - start
            
            results.append({
                "model": model_name,
                "status": "✅ OK",
                "time": round(elapsed, 2),
                "response": response.choices[0].message.content[:100]
            })
            log(f"✅ {model_name}: OK ({elapsed:.2f}s)")
            
        except Exception as e:
            results.append({
                "model": model_name,
                "status": "❌ Error",
                "error": str(e)[:200]
            })
            log(f"❌ {model_name}: {e}", "ERROR")
    
    return jsonify(results)

@app.route('/api/models', methods=['GET'])
def list_models():
    """Получить список доступных моделей"""
    try:
        models = client.models.list()
        return jsonify([m.id for m in models])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            height: 100dvh;
            overflow: hidden;
            -webkit-tap-highlight-color: transparent;
        }
        
        .app { display: flex; height: 100vh; height: 100dvh; }
        
        .overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 90;
            opacity: 0;
            transition: opacity 0.3s;
        }
        .overlay.active { opacity: 1; }
        
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
        
        .sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
        
        .sidebar-logo {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 12px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .new-chat-btn, .test-btn {
            width: 100%;
            padding: 12px;
            margin-bottom: 8px;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .new-chat-btn {
            background: var(--accent);
            color: white;
        }
        
        .test-btn {
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            border: 1px solid var(--border);
        }
        
        .new-chat-btn:active, .test-btn:active { transform: scale(0.98); }
        
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
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .chat-item:active { background: var(--bg-tertiary); }
        .chat-item.active {
            background: var(--bg-tertiary);
            border-left: 3px solid var(--accent);
        }
        
        .chat-item-content { flex: 1; min-width: 0; }
        
        .chat-item-title {
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        }
        
        .chat-item-meta { font-size: 12px; color: var(--text-secondary); }
        
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
        .chat-item:active .chat-item-delete { opacity: 1; }
        
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
        
        .header-info { flex: 1; min-width: 0; }
        
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
        
        .message.user { flex-direction: row-reverse; }
        
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
        
        .user .message-avatar { background: var(--accent); }
        .assistant .message-avatar { background: #8b5cf6; }
        
        .message-body { flex: 1; min-width: 0; }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .message-role { font-size: 13px; font-weight: 600; }
        
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
        
        .user .message-content { background: var(--user-msg); }
        .assistant .message-content { background: var(--assistant-msg); }
        .message-content.error { background: var(--error-bg); border: 1px solid var(--error-border); }
        
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
        
        .input-wrapper:focus-within { border-color: var(--accent); }
        
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
        
        #messageInput::placeholder { color: #666; }
        
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
        
        .send-btn:active { transform: scale(0.95); background: var(--accent-hover); }
        .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        
        .send-icon { font-size: 18px; }
        
        .typing-indicator { display: flex; gap: 4px; padding: 4px 0; }
        
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
        
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
        .empty-state-icon { font-size: 48px; margin-bottom: 16px; }
        
        @media (max-width: 768px) {
            .sidebar {
                position: fixed;
                left: 0; top: 0; bottom: 0;
                transform: translateX(-100%);
                width: 85%;
                max-width: 320px;
                z-index: 100;
            }
            
            .sidebar.open {
                transform: translateX(0);
                box-shadow: 4px 0 20px rgba(0,0,0,0.3);
            }
            
            .overlay { display: block; pointer-events: none; }
            .overlay.active { pointer-events: all; }
            
            .message { max-width: 100%; }
            .header { padding: 8px 12px; }
            .header-title { font-size: 15px; }
            .model-select { font-size: 12px; padding: 6px 8px; }
            .input-area { padding: 8px 12px; padding-bottom: max(8px, env(safe-area-inset-bottom)); }
            .send-btn { padding: 10px 14px; }
            .send-btn-text { display: none; }
            .message-content { font-size: 15px; padding: 10px 14px; }
        }
        
        @media (min-width: 769px) {
            .menu-btn { display: none; }
            .overlay { display: none !important; }
        }
    </style>
</head>
<body>
    <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
    
    <div class="app">
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-logo">🤖 AI Агент</div>
                <button class="new-chat-btn" onclick="createNewChat()">✨ Новый чат</button>
                <button class="test-btn" onclick="testAPI()">🔧 Тест API</button>
            </div>
            <div class="chats-list" id="chatsList"></div>
        </div>
        
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()">
                    <span></span><span></span><span></span>
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
            
            <div class="input-area">
                <div class="input-wrapper">
                    <textarea id="messageInput" placeholder="Введите сообщение..." rows="1"
                        onkeydown="handleKeyDown(event)" oninput="autoResize(this)"></textarea>
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
        
        async function testAPI() {
            const container = document.getElementById('messagesContainer');
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔧</div><div>Тестирую API...</div></div>';
            
            try {
                const response = await fetch('/api/test');
                const results = await response.json();
                
                let html = '<div style="max-width:750px;margin:0 auto;">';
                html += '<h3 style="margin-bottom:16px;">Результаты теста:</h3>';
                
                results.forEach(r => {
                    html += `<div style="background:var(--bg-secondary);padding:12px;border-radius:8px;margin-bottom:8px;">`;
                    html += `<strong>${r.status} ${r.model}</strong><br>`;
                    if (r.time) html += `⏱ ${r.time}с<br>`;
                    if (r.response) html += `💬 ${r.response}<br>`;
                    if (r.error) html += `❌ ${r.error}<br>`;
                    html += `</div>`;
                });
                
                html += '</div>';
                container.innerHTML = html;
            } catch (error) {
                container.innerHTML = `<div class="empty-state">Ошибка: ${error.message}</div>`;
            }
        }
        
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('active');
        }
        
        function closeSidebar() {
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('overlay').classList.remove('active');
        }
        
        async function init() {
            await loadChatsList();
            if (!currentChatId) await createNewChat();
        }
        
        async function createNewChat() {
            const response = await fetch('/api/chat/new', { method: 'POST' });
            const data = await response.json();
            currentChatId = data.chat_id;
            await loadChatsList();
            document.getElementById('messagesContainer').innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">💬</div>
                    <div>Новый чат создан. Начните общение!</div>
                </div>`;
            document.getElementById('headerTitle').textContent = 'Новый чат';
            closeSidebar();
        }
        
        async function loadChatsList() {
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
                    <button class="chat-item-delete" onclick="deleteChat(event, '${chat.chat_id}')">✕</button>`;
                container.appendChild(div);
            });
        }
        
        async function switchChat(chatId) {
            currentChatId = chatId;
            await loadChatsList();
            closeSidebar();
            
            const response = await fetch(`/api/chat/history/${chatId}`);
            const messages = await response.json();
            const container = document.getElementById('messagesContainer');
            container.innerHTML = '';
            
            if (messages.length === 0) {
                container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💬</div><div>Чат пуст.</div></div>';
            } else {
                messages.forEach(msg => addMessageToUI(msg.role, msg.content, msg.model, msg.timestamp, null));
            }
            
            document.getElementById('headerTitle').textContent = 
                messages.length > 0 ? messages[0].content.substring(0, 30) + '...' : 'Новый чат';
        }
        
        async function deleteChat(event, chatId) {
            event.stopPropagation();
            if (!confirm('Удалить чат?')) return;
            await fetch(`/api/chat/delete/${chatId}`, { method: 'DELETE' });
            if (currentChatId === chatId) await createNewChat();
            else await loadChatsList();
        }
        
        function updateModel() { currentModel = document.getElementById('modelSelect').value; }
        
        function addMessageToUI(role, content, model, timestamp, responseTime) {
            const container = document.getElementById('messagesContainer');
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) emptyState.remove();
            
            const div = document.createElement('div');
            div.className = `message ${role}`;
            const time = new Date(timestamp).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            const isError = content.includes('❌');
            
            div.innerHTML = `
                <div class="message-avatar">${role === 'user' ? '👤' : '🤖'}</div>
                <div class="message-body">
                    <div class="message-header">
                        <span class="message-role">${role === 'user' ? 'Вы' : 'AI Агент'}</span>
                        <div class="message-meta">
                            <span>${model}</span><span>${time}</span>
                            ${responseTime ? `<span>⏱ ${responseTime}с</span>` : ''}
                        </div>
                    </div>
                    <div class="message-content ${isError ? 'error' : ''}">${escapeHtml(content)}</div>
                </div>`;
            
            container.appendChild(div);
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
                    <div class="message-header"><span class="message-role">AI Агент</span></div>
                    <div class="message-content">
                        <div class="typing-indicator">
                            <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
                        </div>
                    </div>
                </div>`;
            
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
            
            addMessageToUI('user', message, currentModel, new Date().toISOString());
            addTypingIndicator();
            
            try {
                const response = await fetch('/api/chat/send', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message, model: currentModel, chat_id: currentChatId })
                });
                
                const data = await response.json();
                removeTypingIndicator();
                
                if (data.response) {
                    addMessageToUI('assistant', data.response, data.model, data.timestamp, data.response_time);
                    await loadChatsList();
                } else {
                    addMessageToUI('assistant', data.details || `❌ ${data.error}`, currentModel, new Date().toISOString());
                }
            } catch (error) {
                removeTypingIndicator();
                addMessageToUI('assistant', `❌ Ошибка: ${error.message}`, currentModel, new Date().toISOString());
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
    log("=" * 50)
    log("Starting AI Agent with OpenAI client...")
    log(f"API: {API_URL}")
    log("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)
