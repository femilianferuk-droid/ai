from flask import Flask, request, jsonify, Response
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import secrets
import json
import time
import uuid

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Конфигурация БД
DATABASE_URL = "postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c"

# Конфигурация API
API_URL = "https://gpt-agent.cc/v1/chat/completions"
API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"

MODELS = {
    "claude-sonnet-4.6": "claude-sonnet-4.6",
    "minimax-M2.7": "minimax-M2.7",
    "KIMI-2.6": "KIMI-2.6",
    "DEEPSEEK-V4-FLASH": "DEEPSEEK-V4-FLASH"
}

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                title TEXT DEFAULT 'Новый чат',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model TEXT,
                role TEXT,
                content TEXT
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_id ON chats(chat_id)
        """)
        conn.commit()
        conn.close()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")

init_db()

@app.route('/')
def index():
    return HTML_CONTENT

@app.route('/api/chat/new', methods=['POST'])
def new_chat():
    chat_id = str(uuid.uuid4())
    return jsonify({'chat_id': chat_id})

@app.route('/api/chat/list', methods=['GET'])
def chat_list():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT chat_id, title, MIN(timestamp) as created, 
                   COUNT(*) as messages_count
            FROM chats 
            GROUP BY chat_id, title 
            ORDER BY created DESC
        """)
        chats = cur.fetchall()
        conn.close()
        return jsonify([dict(chat) for chat in chats])
    except Exception as e:
        print(f"Chat list error: {e}")
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
        
        # Определяем заголовок чата по первому сообщению
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Проверяем, есть ли сообщения в этом чате
        cur.execute("SELECT COUNT(*) as cnt FROM chats WHERE chat_id = %s", (chat_id,))
        msg_count = cur.fetchone()['cnt']
        
        # Сохраняем сообщение пользователя
        cur.execute(
            "INSERT INTO chats (chat_id, title, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, user_message[:50] if msg_count == 0 else None, model, 'user', user_message)
        )
        
        # Обновляем заголовок, если это первое сообщение
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
        
        # Отправляем запрос к API
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
        
        print(f"Sending to API: model={model}, messages_count={len(messages)}")
        
        start_time = time.time()
        response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        elapsed_time = time.time() - start_time
        
        print(f"API response: status={response.status_code}, time={elapsed_time:.2f}s")
        
        if response.status_code == 200:
            response_data = response.json()
            ai_response = response_data['choices'][0]['message']['content']
            
            # Сохраняем ответ AI
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
                'response_time': round(elapsed_time, 2),
                'timestamp': datetime.now().isoformat()
            })
        else:
            print(f"API error details: {response.text}")
            conn.close()
            return jsonify({
                'error': f'API ошибка: {response.status_code}',
                'details': response.text[:200]
            }), 500
            
    except Exception as e:
        print(f"Send message error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/history/<chat_id>', methods=['GET'])
def get_chat_history(chat_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM chats WHERE chat_id = %s ORDER BY timestamp ASC",
            (chat_id,)
        )
        messages = cur.fetchall()
        conn.close()
        return jsonify([dict(msg) for msg in messages])
    except Exception as e:
        print(f"History error: {e}")
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
        print(f"Delete error: {e}")
        return jsonify({'error': str(e)}), 500

HTML_CONTENT = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            overflow: hidden;
        }
        
        .app {
            display: flex;
            height: 100vh;
        }
        
        .sidebar {
            width: 280px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }
        
        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        
        .new-chat-btn {
            width: 100%;
            padding: 10px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s;
        }
        
        .new-chat-btn:hover {
            background: var(--accent-hover);
            transform: translateY(-1px);
        }
        
        .chats-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }
        
        .chat-item {
            padding: 12px;
            margin-bottom: 4px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            justify-content: space-between;
            align-items: center;
            group: true;
        }
        
        .chat-item:hover {
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
            font-size: 13px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        }
        
        .chat-item-meta {
            font-size: 11px;
            color: var(--text-secondary);
        }
        
        .chat-item-delete {
            opacity: 0;
            background: none;
            border: none;
            color: #ef4444;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            transition: all 0.2s;
        }
        
        .chat-item:hover .chat-item-delete {
            opacity: 1;
        }
        
        .chat-item-delete:hover {
            background: rgba(239, 68, 68, 0.1);
        }
        
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }
        
        .header {
            padding: 12px 20px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .header-title {
            font-size: 16px;
            font-weight: 600;
        }
        
        .model-select {
            padding: 8px 12px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
        }
        
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
        }
        
        .message {
            max-width: 800px;
            margin: 0 auto 24px;
            display: flex;
            gap: 12px;
        }
        
        .message.user {
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 30px;
            height: 30px;
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
        }
        
        .message-role {
            font-size: 13px;
            font-weight: 600;
        }
        
        .message-meta {
            font-size: 11px;
            color: var(--text-secondary);
            display: flex;
            gap: 12px;
            align-items: center;
        }
        
        .message-content {
            padding: 12px 16px;
            border-radius: 12px;
            line-height: 1.6;
            font-size: 14px;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        
        .user .message-content {
            background: var(--user-msg);
        }
        
        .assistant .message-content {
            background: var(--assistant-msg);
        }
        
        .input-area {
            padding: 16px 20px;
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
        }
        
        .input-wrapper {
            display: flex;
            gap: 8px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 8px 12px;
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
            font-size: 14px;
            outline: none;
            resize: none;
            padding: 8px 0;
            max-height: 120px;
        }
        
        #messageInput::placeholder {
            color: var(--text-secondary);
        }
        
        .send-btn {
            padding: 8px 20px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .send-btn:hover:not(:disabled) {
            background: var(--accent-hover);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
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
            0%, 60%, 100% { opacity: 0.3; transform: scale(1); }
            30% { opacity: 1; transform: scale(1.2); }
        }
        
        .timer {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            color: var(--text-secondary);
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
        
        @media (max-width: 768px) {
            .sidebar {
                display: none;
                position: fixed;
                left: 0;
                top: 0;
                bottom: 0;
                z-index: 100;
            }
            
            .sidebar.open {
                display: flex;
            }
            
            .message {
                max-width: 100%;
            }
        }
    </style>
</head>
<body>
    <div class="app">
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <button class="new-chat-btn" onclick="createNewChat()">
                    ✨ Новый чат
                </button>
            </div>
            <div class="chats-list" id="chatsList"></div>
        </div>
        
        <div class="main">
            <div class="header">
                <span class="header-title" id="headerTitle">AI Агент</span>
                <select class="model-select" id="modelSelect" onchange="updateModel()">
                    <option value="claude-sonnet-4.6">Claude Sonnet 4.6</option>
                    <option value="minimax-M2.7">MiniMax M2.7</option>
                    <option value="KIMI-2.6">KIMI 2.6</option>
                    <option value="DEEPSEEK-V4-FLASH">DeepSeek V4 Flash</option>
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
                    <textarea 
                        id="messageInput" 
                        placeholder="Введите сообщение... (Enter для отправки)"
                        rows="1"
                        onkeydown="handleKeyDown(event)"
                        oninput="autoResize(this)"
                    ></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">
                        <span>Отправить</span>
                        <span>↵</span>
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentChatId = null;
        let isProcessing = false;
        let currentModel = 'claude-sonnet-4.6';
        
        async function init() {
            await loadChatsList();
            if (!currentChatId) {
                await createNewChat();
            }
        }
        
        async function createNewChat() {
            try {
                const response = await fetch('/api/chat/new', { method: 'POST' });
                const data = await response.json();
                currentChatId = data.chat_id;
                await loadChatsList();
                document.getElementById('messagesContainer').innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">💬</div>
                        <div>Новый чат создан. Начните общение!</div>
                    </div>
                `;
                document.getElementById('headerTitle').textContent = 'Новый чат';
            } catch (error) {
                console.error('Error creating chat:', error);
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
            } catch (error) {
                console.error('Error loading chats:', error);
            }
        }
        
        async function switchChat(chatId) {
            currentChatId = chatId;
            await loadChatsList();
            
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
                        addMessageToUI(msg.role, msg.content, msg.model, msg.timestamp, msg.response_time);
                    });
                }
                
                document.getElementById('headerTitle').textContent = 
                    messages.length > 0 ? messages[0].content.substring(0, 30) + '...' : 'Новый чат';
                    
            } catch (error) {
                console.error('Error switching chat:', error);
            }
        }
        
        async function deleteChat(event, chatId) {
            event.stopPropagation();
            
            if (!confirm('Удалить этот чат?')) return;
            
            try {
                await fetch(`/api/chat/delete/${chatId}`, { method: 'DELETE' });
                
                if (currentChatId === chatId) {
                    await createNewChat();
                } else {
                    await loadChatsList();
                }
            } catch (error) {
                console.error('Error deleting chat:', error);
            }
        }
        
        function updateModel() {
            currentModel = document.getElementById('modelSelect').value;
        }
        
        function addMessageToUI(role, content, model, timestamp, responseTime) {
            const container = document.getElementById('messagesContainer');
            
            // Удаляем empty state если есть
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) emptyState.remove();
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;
            
            const time = new Date(timestamp).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            
            messageDiv.innerHTML = `
                <div class="message-avatar">${role === 'user' ? '👤' : '🤖'}</div>
                <div class="message-body">
                    <div class="message-header">
                        <span class="message-role">${role === 'user' ? 'Вы' : 'AI Агент'}</span>
                        <div class="message-meta">
                            <span>${model}</span>
                            <span>${time}</span>
                            ${responseTime ? `<span class="timer">⏱ ${responseTime}с</span>` : ''}
                        </div>
                    </div>
                    <div class="message-content">${content}</div>
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
        
        async function sendMessage() {
            if (isProcessing) return;
            
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if (!message || !currentChatId) return;
            
            input.value = '';
            input.style.height = 'auto';
            
            isProcessing = true;
            document.getElementById('sendBtn').disabled = true;
            
            const sendStartTime = Date.now();
            addMessageToUI('user', message, currentModel, new Date().toISOString());
            addTypingIndicator();
            
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
                
                if (data.response) {
                    addMessageToUI('assistant', data.response, data.model, data.timestamp, data.response_time);
                    await loadChatsList();
                } else {
                    addMessageToUI('assistant', `❌ Ошибка: ${data.error || 'Неизвестная ошибка'}\n${data.details || ''}`, currentModel, new Date().toISOString());
                }
            } catch (error) {
                removeTypingIndicator();
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
        
        window.onload = init;
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
