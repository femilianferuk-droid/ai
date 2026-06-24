from flask import Flask, render_template, request, jsonify, session
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import secrets
import os

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
    return psycopg2.connect(DATABASE_URL)

def init_db():
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

init_db()

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '')
    model = data.get('model', 'claude-sonnet-4.6')
    
    if not user_message:
        return jsonify({'error': 'Сообщение не может быть пустым'}), 400
    
    try:
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
        
        # Отправляем запрос к API
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."},
                *messages
            ]
        }
        
        response = requests.post(API_URL, json=payload, headers=headers)
        
        if response.status_code == 200:
            ai_response = response.json()['choices'][0]['message']['content']
            
            # Сохраняем ответ AI
            cur.execute(
                "INSERT INTO chats (model, role, content) VALUES (%s, %s, %s)",
                (model, 'assistant', ai_response)
            )
            conn.commit()
            conn.close()
            
            return jsonify({
                'response': ai_response,
                'model': model,
                'timestamp': datetime.now().isoformat()
            })
        else:
            conn.close()
            return jsonify({'error': 'Ошибка API'}), 500
            
    except Exception as e:
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
        return jsonify({'error': str(e)}), 500

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Агент</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 800px;
            height: 90vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .header h1 {
            font-size: 24px;
            font-weight: 600;
        }
        
        .controls {
            display: flex;
            gap: 10px;
        }
        
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }
        
        .btn-clear {
            background: rgba(255,255,255,0.2);
            color: white;
        }
        
        .btn-clear:hover {
            background: rgba(255,255,255,0.3);
        }
        
        .model-select {
            padding: 10px;
            border-radius: 10px;
            border: 2px solid #e0e0e0;
            font-size: 14px;
            margin-bottom: 10px;
            width: 100%;
        }
        
        .chat-area {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background: #f8f9fa;
        }
        
        .message {
            margin-bottom: 20px;
            display: flex;
            flex-direction: column;
        }
        
        .message.user {
            align-items: flex-end;
        }
        
        .message.assistant {
            align-items: flex-start;
        }
        
        .message-content {
            max-width: 70%;
            padding: 15px;
            border-radius: 15px;
            word-wrap: break-word;
        }
        
        .user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .assistant .message-content {
            background: white;
            border: 2px solid #e0e0e0;
        }
        
        .message-info {
            font-size: 12px;
            color: #6c757d;
            margin-top: 5px;
        }
        
        .input-area {
            padding: 20px;
            background: white;
            border-top: 2px solid #e0e0e0;
        }
        
        .input-group {
            display: flex;
            gap: 10px;
        }
        
        .message-input {
            flex: 1;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 15px;
            font-size: 16px;
            resize: none;
            outline: none;
        }
        
        .message-input:focus {
            border-color: #667eea;
        }
        
        .send-btn {
            padding: 15px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 15px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.3s;
        }
        
        .send-btn:hover {
            transform: scale(1.05);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .typing-indicator {
            display: none;
            padding: 20px;
            color: #6c757d;
        }
        
        .typing-indicator.active {
            display: block;
        }
        
        @media (max-width: 768px) {
            .container {
                height: 100vh;
                border-radius: 0;
            }
            
            .message-content {
                max-width: 85%;
            }
            
            .header h1 {
                font-size: 20px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 AI Агент</h1>
            <div class="controls">
                <button class="btn btn-clear" onclick="clearHistory()">Очистить</button>
            </div>
        </div>
        
        <div class="chat-area" id="chatArea">
            <div class="typing-indicator" id="typingIndicator">
                AI печатает...
            </div>
        </div>
        
        <div class="input-area">
            <select class="model-select" id="modelSelect">
                <option value="claude-sonnet-4.6">Claude Sonnet 4.6</option>
                <option value="minimax-M2.7">MiniMax M2.7</option>
                <option value="KIMI-2.6">KIMI 2.6</option>
                <option value="DEEPSEEK-V4-FLASH">DeepSeek V4 Flash</option>
            </select>
            <div class="input-group">
                <textarea 
                    class="message-input" 
                    id="messageInput" 
                    placeholder="Введите сообщение..."
                    rows="1"
                    onkeypress="handleKeyPress(event)"
                ></textarea>
                <button class="send-btn" onclick="sendMessage()">Отправить</button>
            </div>
        </div>
    </div>
    
    <script>
        const chatArea = document.getElementById('chatArea');
        const messageInput = document.getElementById('messageInput');
        const modelSelect = document.getElementById('modelSelect');
        const typingIndicator = document.getElementById('typingIndicator');
        
        function handleKeyPress(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }
        
        async function sendMessage() {
            const message = messageInput.value.trim();
            if (!message) return;
            
            const model = modelSelect.value;
            
            addMessage('user', message, model);
            messageInput.value = '';
            
            typingIndicator.classList.add('active');
            
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
                
                const data = await response.json();
                
                if (data.response) {
                    addMessage('assistant', data.response, model);
                } else {
                    addMessage('assistant', 'Ошибка: ' + (data.error || 'Неизвестная ошибка'), model);
                }
            } catch (error) {
                addMessage('assistant', 'Ошибка соединения: ' + error.message, model);
            } finally {
                typingIndicator.classList.remove('active');
            }
        }
        
        function addMessage(role, content, model) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;
            
            const now = new Date();
            const time = now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            
            messageDiv.innerHTML = `
                <div class="message-content">${content}</div>
                <div class="message-info">${role === 'user' ? 'Вы' : 'AI'} • ${model} • ${time}</div>
            `;
            
            chatArea.appendChild(messageDiv);
            chatArea.scrollTop = chatArea.scrollHeight;
        }
        
        async function clearHistory() {
            if (confirm('Очистить историю чата?')) {
                await fetch('/api/clear', { method: 'POST' });
                chatArea.innerHTML = '';
                addMessage('assistant', 'История очищена. Начните новый диалог!', 'system');
            }
        }
        
        async function loadHistory() {
            try {
                const response = await fetch('/api/history');
                const history = await response.json();
                
                history.reverse().forEach(msg => {
                    addMessage(msg.role, msg.content, msg.model);
                });
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
