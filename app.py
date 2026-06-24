import os
import json
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from openai import OpenAI

app = Flask(__name__)
app.secret_key = 'versel-chat-secret-key-2024'

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# API configuration
API_BASE_URL = 'https://capi.aerolink.lat/'
API_TOKEN = 'aero_live_yEEr83dI6tcp7744ZdCXAb7IirUUVM_uVGbor8IeXAk'

# Initialize OpenAI client
client = OpenAI(
    base_url=API_BASE_URL,
    api_key=API_TOKEN
)

# User model
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(20), nullable=False)
    model = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Initialize database
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
    except Exception as e:
        print(f"Database init error: {e}")

def get_available_models():
    """Fetch available models from the API"""
    try:
        models = client.models.list()
        return [model.id for model in models.data]
    except Exception as e:
        print(f"Error fetching models: {e}")
        return ['gpt-3.5-turbo', 'gpt-4', 'claude-3-opus', 'claude-3-sonnet']

def send_message_to_api(messages, model='claude-3-opus'):
    """Send messages to the API"""
    try:
        api_messages = [{'role': msg['role'], 'content': msg['content']} for msg in messages]
        
        response = client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=0.7,
            max_tokens=2000
        )
        
        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content
        
        return "Извините, произошла ошибка при получении ответа."
    except Exception as e:
        return f"Ошибка: {str(e)}"

# HTML template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Chat - VerSEL</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        
        .login-wrapper {
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }
        
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        
        .login-container h2 {
            text-align: center;
            margin-bottom: 30px;
            color: #333;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #555;
        }
        
        .form-group input {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
        }
        
        .error-message {
            color: #e74c3c;
            text-align: center;
            margin-bottom: 15px;
            padding: 10px;
            background: #fde8e8;
            border-radius: 5px;
        }
        
        .btn {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        
        .btn:hover {
            transform: translateY(-2px);
        }
        
        .chat-container {
            width: 100%;
            height: 100vh;
            display: flex;
            flex-direction: column;
            background: white;
        }
        
        .chat-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .chat-header h2 {
            margin: 0;
            font-size: 20px;
        }
        
        .header-controls {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .model-select {
            padding: 8px;
            border-radius: 5px;
            border: none;
            font-size: 14px;
            background: white;
        }
        
        .logout-btn {
            background: rgba(255,255,255,0.2);
            color: white;
            border: 1px solid rgba(255,255,255,0.3);
            padding: 8px 15px;
            border-radius: 5px;
            cursor: pointer;
            text-decoration: none;
            font-size: 14px;
        }
        
        .logout-btn:hover {
            background: rgba(255,255,255,0.3);
        }
        
        .chat-messages {
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            background: #f5f5f5;
        }
        
        .message {
            margin-bottom: 15px;
            max-width: 70%;
            padding: 12px 16px;
            border-radius: 15px;
            word-wrap: break-word;
            animation: fadeIn 0.3s;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .message.user {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            margin-left: auto;
            border-bottom-right-radius: 5px;
        }
        
        .message.assistant {
            background: white;
            color: #333;
            border-bottom-left-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        .message-info {
            font-size: 11px;
            opacity: 0.7;
            margin-bottom: 5px;
        }
        
        .chat-input-container {
            padding: 20px;
            background: white;
            border-top: 1px solid #eee;
            display: flex;
            gap: 10px;
        }
        
        .chat-input {
            flex: 1;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 25px;
            font-size: 16px;
            outline: none;
        }
        
        .chat-input:focus {
            border-color: #667eea;
        }
        
        .send-btn {
            padding: 12px 25px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 25px;
            font-size: 16px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        
        .send-btn:hover:not(:disabled) {
            transform: scale(1.05);
        }
        
        .send-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        
        .typing-indicator {
            display: inline-block;
            padding: 10px 16px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            margin-bottom: 15px;
        }
        
        .typing-indicator span {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #667eea;
            margin: 0 2px;
            animation: typing 1s infinite;
        }
        
        .typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
        .typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
        
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
        
        @media (max-width: 768px) {
            .message {
                max-width: 85%;
            }
            .chat-header {
                flex-direction: column;
                text-align: center;
            }
        }
    </style>
</head>
<body>
    {% if current_user.is_authenticated %}
    <div class="chat-container">
        <div class="chat-header">
            <h2>🤖 AI Chat</h2>
            <div class="header-controls">
                <select class="model-select" id="modelSelect" onchange="changeModel()">
                    {% for model in models %}
                    <option value="{{ model }}" {% if model == current_model %}selected{% endif %}>{{ model }}</option>
                    {% endfor %}
                </select>
                <a href="{{ url_for('logout') }}" class="logout-btn">Выйти</a>
            </div>
        </div>
        
        <div class="chat-messages" id="chatMessages">
            {% for message in messages %}
            <div class="message {{ message.role }}">
                <div class="message-info">
                    {{ 'Вы' if message.role == 'user' else 'AI' }} • {{ message.model }}
                </div>
                {{ message.content | replace('\n', '<br>') | safe }}
            </div>
            {% endfor %}
        </div>
        
        <div class="chat-input-container">
            <input type="text" class="chat-input" id="messageInput" 
                   placeholder="Введите сообщение..." 
                   onkeypress="if(event.key==='Enter') sendMessage()">
            <button class="send-btn" id="sendButton" onclick="sendMessage()">➤</button>
        </div>
    </div>
    
    <script>
        let currentModel = '{{ current_model }}';
        
        function changeModel() {
            currentModel = document.getElementById('modelSelect').value;
        }
        
        function addMessage(content, role, model) {
            const container = document.getElementById('chatMessages');
            const div = document.createElement('div');
            div.className = 'message ' + role;
            
            const info = document.createElement('div');
            info.className = 'message-info';
            info.textContent = (role === 'user' ? 'Вы' : 'AI') + ' • ' + (model || currentModel);
            
            const text = document.createElement('div');
            text.innerHTML = content.replace(/\\n/g, '<br>');
            
            div.appendChild(info);
            div.appendChild(text);
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
        
        function showTyping() {
            const container = document.getElementById('chatMessages');
            const typing = document.createElement('div');
            typing.id = 'typingIndicator';
            typing.className = 'typing-indicator';
            typing.innerHTML = '<span></span><span></span><span></span>';
            container.appendChild(typing);
            container.scrollTop = container.scrollHeight;
        }
        
        function hideTyping() {
            const typing = document.getElementById('typingIndicator');
            if (typing) typing.remove();
        }
        
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const btn = document.getElementById('sendButton');
            const message = input.value.trim();
            
            if (!message) return;
            
            addMessage(message, 'user', currentModel);
            input.value = '';
            btn.disabled = true;
            showTyping();
            
            try {
                const res = await fetch('/send_message', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        message: message,
                        model: currentModel
                    })
                });
                
                const data = await res.json();
                hideTyping();
                
                if (data.success) {
                    addMessage(data.response, 'assistant', data.model);
                } else {
                    addMessage('Ошибка: ' + (data.error || 'Неизвестная ошибка'), 'assistant');
                }
            } catch (error) {
                hideTyping();
                addMessage('Ошибка соединения: ' + error.message, 'assistant');
            } finally {
                btn.disabled = false;
                input.focus();
            }
        }
    </script>
    {% else %}
    <div class="login-wrapper">
        <div class="login-container">
            <h2>🔐 Вход в систему</h2>
            {% if error %}
            <div class="error-message">{{ error }}</div>
            {% endif %}
            <form method="POST" action="{{ url_for('login') }}">
                <div class="form-group">
                    <label for="username">Логин</label>
                    <input type="text" id="username" name="username" required autofocus>
                </div>
                <div class="form-group">
                    <label for="password">Пароль</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <button type="submit" class="btn">Войти</button>
            </form>
        </div>
    </div>
    {% endif %}
</body>
</html>
'''

@app.route('/')
@login_required
def index():
    try:
        messages = ChatMessage.query.filter_by(user_id=current_user.id)\
            .order_by(ChatMessage.timestamp).all()
    except:
        messages = []
    
    models = get_available_models()
    current_model = session.get('current_model', 'claude-3-opus')
    
    return render_template_string(HTML_TEMPLATE, 
                                messages=messages, 
                                models=models, 
                                current_model=current_model)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user)
                session['current_model'] = 'claude-3-opus'
                return redirect(url_for('index'))
            else:
                error = 'Неверный логин или пароль'
        except Exception as e:
            error = 'Ошибка сервера. Попробуйте позже.'
            print(f"Login error: {e}")
    
    return render_template_string(HTML_TEMPLATE, error=error, models=[], current_model='')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    try:
        data = request.json
        message_text = data.get('message')
        model = data.get('model', 'claude-3-opus')
        
        session['current_model'] = model
        
        # Save user message
        user_message = ChatMessage(
            user_id=current_user.id,
            content=message_text,
            role='user',
            model=model
        )
        db.session.add(user_message)
        db.session.commit()
        
        # Get recent messages for context
        recent_messages = ChatMessage.query\
            .filter_by(user_id=current_user.id)\
            .order_by(ChatMessage.timestamp.desc())\
            .limit(20).all()
        
        context_messages = [{'role': msg.role, 'content': msg.content} 
                          for msg in reversed(recent_messages)]
        
        # Get AI response
        ai_response = send_message_to_api(context_messages, model)
        
        # Save AI response
        assistant_message = ChatMessage(
            user_id=current_user.id,
            content=ai_response,
            role='assistant',
            model=model
        )
        db.session.add(assistant_message)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'response': ai_response,
            'model': model
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

# For Vercel
app = app

if __name__ == '__main__':
    app.run(debug=True)
