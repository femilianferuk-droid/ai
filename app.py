from flask import Flask, request, jsonify, Response
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import secrets
import json
import time

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
        return psycopg2.connect(DATABASE_URL)
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
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model TEXT,
                role TEXT,
                content TEXT
            )
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

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_message = data.get('message', '')
        model = data.get('model', 'claude-sonnet-4.6')
        
        if not user_message:
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400
        
        # Сохраняем сообщение пользователя
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chats (model, role, content) VALUES (%s, %s, %s)",
            (model, 'user', user_message)
        )
        
        # Получаем историю чата
        cur.execute(
            "SELECT role, content FROM chats ORDER BY timestamp DESC LIMIT 10"
        )
        history = cur.fetchall()
        history.reverse()
        
        messages = [{"role": msg[0], "content": msg[1]} for msg in history]
        
        # Отправляем запрос к API с stream=True
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке подробно и понятно."},
                *messages
            ],
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        print(f"Sending request to API with model: {model}")
        response = requests.post(API_URL, json=payload, headers=headers, stream=True, timeout=60)
        
        if response.status_code == 200:
            def generate():
                full_response = ""
                start_time = time.time()
                
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data_str = line[6:]
                            if data_str.strip() == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data_str)
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    delta = chunk['choices'][0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        full_response += content
                                        yield f"data: {json.dumps({'content': content, 'time': time.time() - start_time})}\n\n"
                            except json.JSONDecodeError:
                                continue
                
                # Сохраняем полный ответ в БД
                try:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO chats (model, role, content) VALUES (%s, %s, %s)",
                        (model, 'assistant', full_response)
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"Error saving response: {e}")
                
                yield f"data: {json.dumps({'done': True, 'full_time': time.time() - start_time})}\n\n"
            
            return Response(generate(), mimetype='text/event-stream')
        else:
            print(f"API error: {response.status_code}, {response.text}")
            conn.close()
            
            # Пробуем без stream если 503
            if response.status_code == 503:
                payload['stream'] = False
                response2 = requests.post(API_URL, json=payload, headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                }, timeout=60)
                
                if response2.status_code == 200:
                    ai_response = response2.json()['choices'][0]['message']['content']
                    cur.execute(
                        "INSERT INTO chats (model, role, content) VALUES (%s, %s, %s)",
                        (model, 'assistant', ai_response)
                    )
                    conn.commit()
                    conn.close()
                    
                    def generate():
                        yield f"data: {json.dumps({'content': ai_response, 'time': 0})}\n\n"
                        yield f"data: {json.dumps({'done': True, 'full_time': 0})}\n\n"
                    
                    return Response(generate(), mimetype='text/event-stream')
            
            return jsonify({'error': f'API error: {response.status_code}'}), 500
            
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM chats ORDER BY timestamp DESC LIMIT 50"
        )
        history = cur.fetchall()
        conn.close()
        return jsonify(list(history))
    except Exception as e:
        print(f"History error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/clear', methods=['POST'])
def clear_history():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM chats")
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        print(f"Clear error: {e}")
        return jsonify({'error': str(e)}), 500

HTML_CONTENT = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Агент - DeepSeek Style</title>
    <style>
        :root {
            --bg-primary: #1a1a1a;
            --bg-secondary: #2d2d2d;
            --bg-input: #3d3d3d;
            --text-primary: #e0e0e0;
            --text-secondary: #a0a0a0;
            --accent: #4a9eff;
            --accent-hover: #3a7ecc;
            --border: #444;
            --user-bg: #2b5a8c;
            --assistant-bg: #2d2d2d;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 12px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            backdrop-filter: blur(10px);
        }
        
        .header-left {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo {
            font-size: 20px;
            font-weight: 600;
            background: linear-gradient(135deg, #4a9eff, #6c5ce7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .model-select {
            padding: 8px 12px;
            background: var(--bg-input);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            outline: none;
        }
        
        .model-select:hover {
            border-color: var(--accent);
        }
        
        .btn {
            padding: 8px 16px;
            background: var(--bg-input);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }
        
        .btn:hover {
            background: var(--accent);
            border-color: var(--accent);
        }
        
        .main-container {
            flex: 1;
            display: flex;
            overflow: hidden;
        }
        
        .sidebar {
            width: 260px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        
        .new-chat-btn {
            padding: 10px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.2s;
            width: 100%;
        }
        
        .new-chat-btn:hover {
            background: var(--accent-hover);
        }
        
        .chat-list {
            flex: 1;
            overflow-y: auto;
            margin-top: 8px;
        }
        
        .chat-item {
            padding: 8px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            color: var(--text-secondary);
            transition: background 0.2s;
        }
        
        .chat-item:hover {
            background: var(--bg-input);
        }
        
        .chat-item.active {
            background: var(--bg-input);
            color: var(--text-primary);
        }
        
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        
        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }
        
        .message-wrapper {
            display: flex;
            gap: 12px;
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
        }
        
        .message-wrapper.user {
            flex-direction: row-reverse;
        }
        
        .avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            flex-shrink: 0;
        }
        
        .user .avatar {
            background: var(--accent);
        }
        
        .assistant .avatar {
            background: #6c5ce7;
        }
        
        .message-content-wrapper {
            flex: 1;
        }
        
        .message-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }
        
        .message-role {
            font-size: 13px;
            font-weight: 600;
        }
        
        .message-time {
            font-size: 11px;
            color: var(--text-secondary);
        }
        
        .message-content {
            background: var(--assistant-bg);
            padding: 12px 16px;
            border-radius: 12px;
            line-height: 1.6;
            font-size: 14px;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        
        .user .message-content {
            background: var(--user-bg);
        }
        
        .typing-time {
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 4px;
        }
        
        .input-container {
            padding: 16px 20px;
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
        }
        
        .input-wrapper {
            display: flex;
            gap: 8px;
            background: var(--bg-input);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 8px;
            transition: border-color 0.2s;
        }
        
        .input-wrapper:focus-within {
            border-color: var(--accent);
        }
        
        .message-input {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-primary);
            font-size: 14px;
            outline: none;
            resize: none;
            padding: 8px;
            max-height: 120px;
        }
        
        .message-input::placeholder {
            color: var(--text-secondary);
        }
        
        .send-btn {
            padding: 8px 16px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.2s;
            white-space: nowrap;
        }
        
        .send-btn:hover {
            background: var(--accent-hover);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .stop-btn {
            padding: 8px 16px;
            background: #e74c3c;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
        }
        
        .thinking {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 1; }
        }
        
        @media (max-width: 768px) {
            .sidebar {
                display: none;
            }
            
            .message-wrapper {
                max-width: 100%;
            }
            
            .header {
                padding: 8px 12px;
            }
        }
        
        .markdown-content code {
            background: rgba(255,255,255,0.1);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 13px;
        }
        
        .markdown-content pre {
            background: rgba(0,0,0,0.3);
            padding: 12px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 8px 0;
        }
        
        .markdown-content p {
            margin: 8px 0;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <span class="logo">🤖 AI Agent</span>
            <select class="model-select" id="modelSelect">
                <option value="claude-sonnet-4.6">Claude Sonnet 4.6</option>
                <option value="minimax-M2.7">MiniMax M2.7</option>
                <option value="KIMI-2.6">KIMI 2.6</option>
                <option value="DEEPSEEK-V4-FLASH">DeepSeek V4 Flash</option>
            </select>
        </div>
        <button class="btn" onclick="clearHistory()">🗑️ Очистить</button>
    </div>
    
    <div class="main-container">
        <div class="sidebar">
            <button class="new-chat-btn" onclick="clearHistory()">+ Новый чат</button>
            <div class="chat-list" id="chatList">
                <div class="chat-item active">Текущий чат</div>
            </div>
        </div>
        
        <div class="chat-area">
            <div class="messages-container" id="messagesContainer">
                <div style="text-align: center; color: var(--text-secondary); margin-top: 40px;">
                    <div style="font-size: 24px; margin-bottom: 12px;">🤖</div>
                    <div style="font-size: 16px;">AI Агент готов к работе</div>
                    <div style="font-size: 13px; margin-top: 8px;">Выберите модель и начните диалог</div>
                </div>
            </div>
            
            <div class="input-container">
                <div class="input-wrapper">
                    <textarea 
                        class="message-input" 
                        id="messageInput" 
                        placeholder="Введите сообщение... (Shift+Enter для новой строки)"
                        rows="1"
                        onkeypress="handleKeyPress(event)"
                        oninput="autoResize(this)"
                    ></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let isProcessing = false;
        let currentResponseDiv = null;
        let typingStartTime = null;
        let abortController = null;
        
        function autoResize(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
        }
        
        function handleKeyPress(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }
        
        async function sendMessage() {
            if (isProcessing) return;
            
            const messageInput = document.getElementById('messageInput');
            const message = messageInput.value.trim();
            if (!message) return;
            
            const model = document.getElementById('modelSelect').value;
            
            addMessage('user', message, model);
            messageInput.value = '';
            messageInput.style.height = 'auto';
            
            isProcessing = true;
            document.getElementById('sendBtn').textContent = '⏹';
            document.getElementById('sendBtn').classList.add('stop-btn');
            
            const responseDiv = addMessage('assistant', '', model);
            currentResponseDiv = responseDiv;
            typingStartTime = Date.now();
            
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        message: message,
                        model: model
                    })
                });
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let fullText = '';
                let buffer = '';
                
                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, {stream: true});
                    const lines = buffer.split('\\n');
                    buffer = lines.pop() || '';
                    
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const data = JSON.parse(line.substring(6));
                            
                            if (data.content) {
                                fullText += data.content;
                                updateMessageContent(responseDiv, fullText);
                                
                                if (data.time) {
                                    updateTypingTime(responseDiv, data.time);
                                }
                            }
                            
                            if (data.done) {
                                updateTypingTime(responseDiv, data.full_time, true);
                                isProcessing = false;
                                document.getElementById('sendBtn').textContent = '➤';
                                document.getElementById('sendBtn').classList.remove('stop-btn');
                            }
                        }
                    }
                }
            } catch (error) {
                updateMessageContent(responseDiv, 'Ошибка: ' + error.message);
                isProcessing = false;
                document.getElementById('sendBtn').textContent = '➤';
                document.getElementById('sendBtn').classList.remove('stop-btn');
            }
        }
        
        function addMessage(role, content, model) {
            const container = document.getElementById('messagesContainer');
            const wrapper = document.createElement('div');
            wrapper.className = `message-wrapper ${role}`;
            
            const now = new Date();
            const time = now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            
            wrapper.innerHTML = `
                <div class="avatar">${role === 'user' ? '👤' : '🤖'}</div>
                <div class="message-content-wrapper">
                    <div class="message-header">
                        <span class="message-role">${role === 'user' ? 'Вы' : 'AI Агент'}</span>
                        <span class="message-time">${time}</span>
                    </div>
                    <div class="message-content markdown-content">${content || '<span class="thinking"></span>'}</div>
                    <div class="typing-time"></div>
                </div>
            `;
            
            container.appendChild(wrapper);
            container.scrollTop = container.scrollHeight;
            
            return wrapper.querySelector('.message-content');
        }
        
        function updateMessageContent(element, text) {
            element.innerHTML = text;
            element.parentElement.parentElement.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }
        
        function updateTypingTime(element, seconds, done = false) {
            const timeDiv = element.parentElement.querySelector('.typing-time');
            if (done) {
                timeDiv.textContent = `⏱️ ${seconds.toFixed(1)}с`;
            } else {
                timeDiv.textContent = `⏱️ ${seconds.toFixed(1)}с...`;
            }
        }
        
        async function clearHistory() {
            if (confirm('Очистить историю чата?')) {
                await fetch('/api/clear', { method: 'POST' });
                document.getElementById('messagesContainer').innerHTML = `
                    <div style="text-align: center; color: var(--text-secondary); margin-top: 40px;">
                        <div style="font-size: 24px; margin-bottom: 12px;">🤖</div>
                        <div style="font-size: 16px;">Чат очищен</div>
                        <div style="font-size: 13px; margin-top: 8px;">Начните новый диалог</div>
                    </div>
                `;
            }
        }
        
        async function loadHistory() {
            try {
                const response = await fetch('/api/history');
                const history = await response.json();
                
                if (history.length > 0) {
                    document.getElementById('messagesContainer').innerHTML = '';
                    history.reverse().forEach(msg => {
                        addMessage(msg.role, msg.content, msg.model);
                    });
                }
            } catch (error) {
                console.error('Ошибка загрузки истории:', error);
            }
        }
        
        window.onload = loadHistory;
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
