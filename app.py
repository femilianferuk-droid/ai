import os
import hashlib
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from openai import OpenAI

# ==================== КОНФИГУРАЦИЯ ====================
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production-32-chars-long!!')
    
    DB_HOST = 'node1.pghost.ru'
    DB_PORT = '15808'
    DB_NAME = 'bothost_db_27588d84c00c'
    DB_USER = 'bothost_db_27588d84c00c'
    DB_PASSWORD = '97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc'
    
    SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    OPENAI_API_KEY = 'aero_live_yEEr83dI6tcp7744ZdCXAb7IirUUVM_uVGbor8IeXAk'
    OPENAI_BASE_URL = 'https://capi.aerolink.lat/'
    DEFAULT_MODEL = 'claude-opus-4.8'
    
    DEFAULT_USERNAME = 'admin'
    DEFAULT_PASSWORD = 'admin123'

# ==================== ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ ====================
app = Flask(__name__)
app.config.from_object(Config)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 1,
    'max_overflow': 0,
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'connect_args': {'connect_timeout': 5}
}

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

client = OpenAI(
    base_url=Config.OPENAI_BASE_URL,
    api_key=Config.OPENAI_API_KEY,
    timeout=60.0,
    max_retries=2
)

# ==================== МОДЕЛИ БАЗЫ ДАННЫХ ====================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(200), default='New Chat')
    model = db.Column(db.String(100), default=Config.DEFAULT_MODEL)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.String(36), db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    file_name = db.Column(db.String(255))
    file_type = db.Column(db.String(50))
    file_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except:
        return None

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    with app.app_context():
        try:
            db.create_all()
            user = db.session.execute(
                db.select(User).filter_by(username=Config.DEFAULT_USERNAME)
            ).scalar_one_or_none()
            
            if not user:
                db.session.add(User(
                    username=Config.DEFAULT_USERNAME,
                    password_hash=hash_password(Config.DEFAULT_PASSWORD)
                ))
                db.session.commit()
                print("Default user created")
        except Exception as e:
            print(f"DB init error: {e}")
            db.session.rollback()

@app.before_request
def before_request():
    try:
        db.create_all()
    except:
        pass

# ==================== HTML ШАБЛОН ====================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Chat</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; height: 100vh; overflow: hidden; }
        
        /* Login */
        .login-wrap { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); padding: 20px; }
        .login-box { background: #1e1e2e; padding: 30px; border-radius: 16px; width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
        .login-box h2 { margin-bottom: 24px; text-align: center; color: #fff; font-size: 24px; }
        .input-group { margin-bottom: 20px; }
        .input-group label { display: block; margin-bottom: 8px; color: #a0a0b0; font-size: 14px; }
        .input-group input { width: 100%; padding: 12px 16px; border: 1px solid #334; border-radius: 10px; background: #16162a; color: #fff; font-size: 15px; outline: none; }
        .input-group input:focus { border-color: #6366f1; }
        .btn { width: 100%; padding: 12px; border: none; border-radius: 10px; background: #4f46e5; color: white; font-size: 16px; cursor: pointer; transition: .2s; font-weight: 500; }
        .btn:hover { background: #4338ca; }
        .error { color: #ef4444; margin-top: 12px; text-align: center; font-size: 14px; }
        
        /* App Layout */
        .app { display: flex; height: 100vh; position: relative; }
        
        /* Sidebar */
        .sidebar { width: 300px; min-width: 300px; background: #111; border-right: 1px solid #222; display: flex; flex-direction: column; transition: transform 0.3s ease; z-index: 100; }
        .sidebar-header { padding: 20px; border-bottom: 1px solid #222; }
        .sidebar-header h3 { color: #fff; font-size: 18px; margin-bottom: 12px; }
        .new-chat-btn { width: 100%; padding: 10px; border: 1px solid #333; border-radius: 10px; background: transparent; color: #fff; cursor: pointer; transition: .2s; font-size: 14px; }
        .new-chat-btn:hover { background: #1a1a1a; }
        .conversations { flex: 1; overflow-y: auto; padding: 12px; }
        .conv-item { padding: 12px; border-radius: 10px; cursor: pointer; margin-bottom: 6px; color: #9ca3af; font-size: 14px; transition: .2s; display: flex; justify-content: space-between; align-items: center; }
        .conv-item:hover, .conv-item.active { background: #1f1f1f; color: #fff; }
        .conv-item .conv-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; margin-right: 8px; }
        .conv-item .del { opacity: 0; color: #ef4444; cursor: pointer; padding: 2px 6px; border-radius: 4px; font-size: 18px; flex-shrink: 0; }
        .conv-item:hover .del { opacity: 1; }
        .user-info { padding: 16px; border-top: 1px solid #222; font-size: 14px; color: #9ca3af; display: flex; justify-content: space-between; align-items: center; }
        .logout-btn { color: #ef4444; text-decoration: none; font-size: 13px; }
        
        /* Main Chat */
        .main { flex: 1; display: flex; flex-direction: column; background: #0a0a0a; min-width: 0; }
        .chat-header { padding: 12px 16px; border-bottom: 1px solid #222; display: flex; align-items: center; gap: 12px; }
        .menu-toggle { display: none; background: none; border: none; color: #fff; font-size: 24px; cursor: pointer; padding: 4px 8px; }
        .chat-header h4 { color: #fff; font-weight: 500; font-size: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        
        .chat-messages { flex: 1; overflow-y: auto; padding: 16px; }
        .message { max-width: 800px; margin: 0 auto 20px; display: flex; gap: 10px; animation: fadeIn .3s ease; }
        .message.user { flex-direction: row-reverse; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .avatar { width: 30px; height: 30px; border-radius: 50%; background: #4f46e5; display: flex; align-items: center; justify-content: center; font-size: 14px; color: white; flex-shrink: 0; }
        .message.user .avatar { background: #10b981; }
        .message-content { flex: 1; min-width: 0; }
        .bubble { background: #161b22; padding: 12px 16px; border-radius: 16px; line-height: 1.6; font-size: 14px; color: #e6edf3; white-space: pre-wrap; word-wrap: break-word; }
        .message.user .bubble { background: #1e3a5f; }
        .file-attachment { margin-top: 8px; }
        .file-attachment img { max-width: 100%; max-height: 300px; border-radius: 8px; cursor: pointer; }
        .file-link { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; background: #1a1a2e; border-radius: 8px; color: #6366f1; text-decoration: none; font-size: 13px; margin-top: 8px; }
        
        /* Input Area */
        .chat-input-container { padding: 12px 16px; border-top: 1px solid #222; background: #0a0a0a; }
        .chat-input-wrap { max-width: 800px; margin: 0 auto; }
        .file-preview { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
        .file-preview-item { position: relative; background: #1a1a1a; border-radius: 8px; padding: 4px; display: flex; align-items: center; gap: 8px; }
        .file-preview-item img { width: 40px; height: 40px; object-fit: cover; border-radius: 4px; }
        .file-preview-item span { font-size: 12px; color: #999; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .remove-file { background: #ef4444; border: none; color: white; width: 20px; height: 20px; border-radius: 50%; cursor: pointer; font-size: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        
        .input-row { display: flex; gap: 8px; align-items: flex-end; }
        .input-buttons { display: flex; gap: 4px; }
        .toolbar-btn { background: #1a1a1a; border: 1px solid #333; color: #999; width: 40px; height: 40px; border-radius: 10px; cursor: pointer; font-size: 18px; transition: .2s; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .toolbar-btn:hover { background: #2a2a2a; color: #fff; }
        .chat-input-wrapper { flex: 1; position: relative; }
        .chat-input { width: 100%; padding: 10px 40px 10px 14px; border: 1px solid #333; border-radius: 12px; background: #111; color: #fff; font-size: 14px; resize: none; outline: none; min-height: 40px; max-height: 120px; font-family: inherit; line-height: 1.4; }
        .chat-input:focus { border-color: #4f46e5; }
        .send-btn { position: absolute; right: 4px; top: 50%; transform: translateY(-50%); width: 32px; height: 32px; border-radius: 8px; background: #4f46e5; border: none; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: .2s; }
        .send-btn:hover { background: #4338ca; }
        .send-btn:disabled { opacity: .4; cursor: not-allowed; }
        
        /* Overlay */
        .sidebar-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 99; }
        
        /* Mobile */
        @media (max-width: 768px) {
            .sidebar { position: fixed; left: 0; top: 0; height: 100%; transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .sidebar-overlay.active { display: block; }
            .menu-toggle { display: block; }
            .chat-header h4 { max-width: 200px; font-size: 14px; }
            .message { max-width: 100%; }
            .chat-messages { padding: 12px; }
            .chat-input-container { padding: 8px 12px; }
        }
        
        .typing { display: flex; gap: 4px; padding: 4px 0; }
        .typing-dot { width: 6px; height: 6px; background: #666; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out; }
        .typing-dot:nth-child(2) { animation-delay: .2s; }
        .typing-dot:nth-child(3) { animation-delay: .4s; }
        @keyframes bounce { 0%, 80%, 100% { transform: translateY(0); } 40% { transform: translateY(-6px); } }
        
        .empty { text-align: center; padding: 60px 20px; color: #555; }
        .empty h1 { font-size: 48px; margin-bottom: 16px; }
        .empty p { font-size: 14px; }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
    </style>
</head>
<body>
    {% if not current_user.is_authenticated %}
    <div class="login-wrap">
        <div class="login-box">
            <h2>🔐 AI Chat</h2>
            <form id="loginForm" onsubmit="return false;">
                <div class="input-group">
                    <label>Username</label>
                    <input type="text" id="username" required autocomplete="username">
                </div>
                <div class="input-group">
                    <label>Password</label>
                    <input type="password" id="password" required autocomplete="current-password">
                </div>
                <button type="submit" class="btn" onclick="login()">Sign In</button>
                <div class="error" id="loginError"></div>
            </form>
        </div>
    </div>
    {% else %}
    <div class="app">
        <div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>
        
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h3>💬 AI Chat</h3>
                <button class="new-chat-btn" onclick="newChat()">+ New Chat</button>
            </div>
            <div class="conversations" id="conversations"></div>
            <div class="user-info">
                <span>👤 {{ current_user.username }}</span>
                <a href="/logout" class="logout-btn">Logout</a>
            </div>
        </div>
        
        <div class="main">
            <div class="chat-header">
                <button class="menu-toggle" onclick="toggleSidebar()">☰</button>
                <h4 id="chatTitle">New Chat</h4>
            </div>
            
            <div class="chat-messages" id="chatMessages">
                <div class="empty">
                    <h1>🤖</h1>
                    <p>Start a conversation with AI</p>
                </div>
            </div>
            
            <div class="chat-input-container">
                <div class="chat-input-wrap">
                    <div class="file-preview" id="filePreview"></div>
                    <div class="input-row">
                        <div class="input-buttons">
                            <input type="file" id="fileInput" accept="image/*,.pdf,.txt,.doc,.docx" multiple style="display:none" onchange="handleFiles(this.files)">
                            <button class="toolbar-btn" onclick="document.getElementById('fileInput').click()" title="Attach file">📎</button>
                            <input type="file" id="imageInput" accept="image/*" capture="camera" multiple style="display:none" onchange="handleFiles(this.files)">
                            <button class="toolbar-btn" onclick="document.getElementById('imageInput').click()" title="Attach image">🖼️</button>
                        </div>
                        <div class="chat-input-wrapper">
                            <textarea class="chat-input" id="chatInput" placeholder="Message..." rows="1"></textarea>
                            <button class="send-btn" id="sendBtn" onclick="sendMessage()">➤</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    {% endif %}
    
    <script>
        let currentConvId = null;
        let attachedFiles = [];
        
        {% if current_user.is_authenticated %}
        document.addEventListener('DOMContentLoaded', () => {
            loadConversations();
            
            const input = document.getElementById('chatInput');
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { 
                    e.preventDefault(); 
                    sendMessage(); 
                }
            });
            input.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
            });
        });
        {% endif %}
        
        function login() {
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            
            fetch('/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username, password})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) location.reload();
                else document.getElementById('loginError').textContent = data.error || 'Invalid credentials';
            })
            .catch(e => {
                document.getElementById('loginError').textContent = 'Network error';
            });
        }
        
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('sidebarOverlay').classList.toggle('active');
        }
        
        function handleFiles(files) {
            for (let file of files) {
                if (file.size > 16 * 1024 * 1024) {
                    alert('File too large: ' + file.name);
                    continue;
                }
                
                const reader = new FileReader();
                reader.onload = function(e) {
                    attachedFiles.push({
                        name: file.name,
                        type: file.type,
                        data: e.target.result,
                        size: file.size
                    });
                    updateFilePreview();
                };
                reader.readAsDataURL(file);
            }
            document.getElementById('fileInput').value = '';
            document.getElementById('imageInput').value = '';
        }
        
        function updateFilePreview() {
            const preview = document.getElementById('filePreview');
            preview.innerHTML = '';
            attachedFiles.forEach((file, index) => {
                const div = document.createElement('div');
                div.className = 'file-preview-item';
                if (file.type.startsWith('image/')) {
                    div.innerHTML = `<img src="${file.data}" alt="${file.name}"><span>${file.name}</span>`;
                } else {
                    div.innerHTML = `<span>📄 ${file.name}</span>`;
                }
                const removeBtn = document.createElement('button');
                removeBtn.className = 'remove-file';
                removeBtn.textContent = '×';
                removeBtn.onclick = () => {
                    attachedFiles.splice(index, 1);
                    updateFilePreview();
                };
                div.appendChild(removeBtn);
                preview.appendChild(div);
            });
        }
        
        async function loadConversations() {
            try {
                const res = await fetch('/conversations');
                const data = await res.json();
                const container = document.getElementById('conversations');
                container.innerHTML = '';
                (data.conversations || []).forEach(c => {
                    const div = document.createElement('div');
                    div.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
                    div.innerHTML = `<span class="conv-title">${escapeHtml(c.title)}</span><span class="del">×</span>`;
                    div.querySelector('.conv-title').onclick = () => {
                        loadChat(c.id);
                        if (window.innerWidth <= 768) toggleSidebar();
                    };
                    div.querySelector('.del').onclick = (e) => {
                        e.stopPropagation();
                        deleteConv(c.id);
                    };
                    container.appendChild(div);
                });
            } catch(e) {
                console.error('Error loading conversations:', e);
            }
        }
        
        async function newChat() {
            currentConvId = null;
            attachedFiles = [];
            updateFilePreview();
            document.getElementById('chatTitle').textContent = 'New Chat';
            document.getElementById('chatMessages').innerHTML = `<div class="empty"><h1>🤖</h1><p>Start a conversation with AI</p></div>`;
            loadConversations();
            if (window.innerWidth <= 768) toggleSidebar();
        }
        
        async function loadChat(convId) {
            currentConvId = convId;
            attachedFiles = [];
            updateFilePreview();
            
            try {
                const res = await fetch('/conversations/' + convId);
                const data = await res.json();
                document.getElementById('chatTitle').textContent = escapeHtml(data.conversation.title);
                
                const container = document.getElementById('chatMessages');
                container.innerHTML = '';
                (data.messages || []).forEach(m => appendMessage(m.role, m.content, m.file_name, m.file_type, m.file_data));
                loadConversations();
            } catch(e) {
                console.error('Error loading chat:', e);
            }
        }
        
        function appendMessage(role, content, fileName, fileType, fileData) {
            const container = document.getElementById('chatMessages');
            container.querySelector('.empty')?.remove();
            const div = document.createElement('div');
            div.className = 'message ' + role;
            const avatar = role === 'user' ? '👤' : '🤖';
            
            let attachmentHtml = '';
            if (fileName && fileData) {
                if (fileType && fileType.startsWith('image/')) {
                    attachmentHtml = `<div class="file-attachment"><img src="${fileData}" alt="${escapeHtml(fileName)}" onclick="window.open(this.src)"></div>`;
                } else {
                    attachmentHtml = `<a class="file-link" href="${fileData}" download="${escapeHtml(fileName)}">📄 ${escapeHtml(fileName)}</a>`;
                }
            }
            
            div.innerHTML = `
                <div class="avatar">${avatar}</div>
                <div class="message-content">
                    ${content ? `<div class="bubble">${escapeHtml(content)}</div>` : ''}
                    ${attachmentHtml}
                </div>
            `;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
        
        function showTyping() {
            const container = document.getElementById('chatMessages');
            container.querySelector('.empty')?.remove();
            const div = document.createElement('div');
            div.className = 'message assistant';
            div.id = 'typing-indicator';
            div.innerHTML = `<div class="avatar">🤖</div><div class="message-content"><div class="bubble"><div class="typing"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div></div>`;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
        
        function hideTyping() {
            const typing = document.getElementById('typing-indicator');
            if (typing) typing.remove();
        }
        
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const btn = document.getElementById('sendBtn');
            const text = input.value.trim();
            
            if (!text && attachedFiles.length === 0) return;
            
            input.value = '';
            input.style.height = 'auto';
            
            if (text) appendMessage('user', text);
            attachedFiles.forEach(f => appendMessage('user', '', f.name, f.type, f.data));
            
            const filesToSend = [...attachedFiles];
            attachedFiles = [];
            updateFilePreview();
            
            btn.disabled = true;
            showTyping();
            
            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        message: text,
                        conversation_id: currentConvId,
                        files: filesToSend.map(f => ({
                            name: f.name,
                            type: f.type,
                            data: f.data
                        }))
                    })
                });
                
                const data = await res.json();
                
                if (data.error) {
                    appendMessage('assistant', 'Error: ' + data.error);
                } else {
                    if (data.conversation_id) currentConvId = data.conversation_id;
                    appendMessage('assistant', data.response);
                    document.getElementById('chatTitle').textContent = escapeHtml(data.title || 'Chat');
                    loadConversations();
                }
            } catch(e) {
                appendMessage('assistant', 'Network error: ' + e.message);
            } finally {
                hideTyping();
                btn.disabled = false;
                document.getElementById('chatInput').focus();
            }
        }
        
        async function deleteConv(id) {
            if (!confirm('Delete this conversation?')) return;
            try {
                await fetch('/conversations/' + id, {method: 'DELETE'});
                if (currentConvId === id) newChat();
                else loadConversations();
            } catch(e) {
                console.error('Error deleting conversation:', e);
            }
        }
        
        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>
'''

# ==================== МАРШРУТЫ ====================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password required'}), 400
        
        db.create_all()
        
        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()
        
        if not user and username == Config.DEFAULT_USERNAME:
            user = User(
                username=Config.DEFAULT_USERNAME,
                password_hash=hash_password(Config.DEFAULT_PASSWORD)
            )
            db.session.add(user)
            db.session.commit()
        
        if user and user.password_hash == hash_password(password):
            login_user(user)
            return jsonify({'success': True})
        
        return jsonify({'success': False, 'error': 'Invalid credentials'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')

@app.route('/conversations')
@login_required
def list_conversations():
    try:
        conversations = db.session.execute(
            db.select(Conversation)
            .filter_by(user_id=current_user.id)
            .order_by(Conversation.updated_at.desc())
        ).scalars().all()
        
        return jsonify({'conversations': [{
            'id': c.id,
            'title': c.title,
            'model': c.model,
            'updated_at': c.updated_at.isoformat()
        } for c in conversations]})
    except Exception as e:
        return jsonify({'conversations': [], 'error': str(e)})

@app.route('/conversations/<conv_id>')
@login_required
def get_conversation(conv_id):
    try:
        conversation = db.session.get(Conversation, conv_id)
        
        if not conversation or conversation.user_id != current_user.id:
            return jsonify({'error': 'Conversation not found'}), 404
        
        messages = db.session.execute(
            db.select(Message)
            .filter_by(conversation_id=conv_id)
            .order_by(Message.created_at)
        ).scalars().all()
        
        return jsonify({
            'conversation': {
                'id': conversation.id,
                'title': conversation.title,
                'model': conversation.model
            },
            'messages': [{
                'role': m.role,
                'content': m.content,
                'file_name': m.file_name,
                'file_type': m.file_type,
                'file_data': m.file_data
            } for m in messages]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/conversations/<conv_id>', methods=['DELETE'])
@login_required
def delete_conversation(conv_id):
    try:
        conversation = db.session.get(Conversation, conv_id)
        
        if not conversation or conversation.user_id != current_user.id:
            return jsonify({'error': 'Conversation not found'}), 404
        
        db.session.delete(conversation)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    try:
        data = request.get_json() or {}
        user_message = data.get('message', '').strip()
        conversation_id = data.get('conversation_id')
        files = data.get('files', [])
        
        if not user_message and not files:
            return jsonify({'error': 'Message or file is required'}), 400
        
        if conversation_id:
            conversation = db.session.get(Conversation, conversation_id)
            if not conversation or conversation.user_id != current_user.id:
                return jsonify({'error': 'Conversation not found'}), 404
            conversation.updated_at = datetime.utcnow()
        else:
            title = user_message[:50] + ('...' if len(user_message) > 50 else 'New Chat') if user_message else 'File upload'
            conversation = Conversation(
                user_id=current_user.id,
                title=title,
                model=Config.DEFAULT_MODEL
            )
            db.session.add(conversation)
            db.session.flush()
            conversation_id = conversation.id
        
        for file_info in files:
            file_msg = Message(
                conversation_id=conversation_id,
                role='user',
                content='',
                file_name=file_info.get('name'),
                file_type=file_info.get('type'),
                file_data=file_info.get('data')
            )
            db.session.add(file_msg)
        
        if user_message:
            user_msg = Message(
                conversation_id=conversation_id,
                role='user',
                content=user_message
            )
            db.session.add(user_msg)
        
        db.session.flush()
        
        messages = db.session.execute(
            db.select(Message)
            .filter_by(conversation_id=conversation_id)
            .order_by(Message.created_at)
        ).scalars().all()
        
        ai_messages = []
        for msg in messages:
            if msg.content:
                ai_messages.append({'role': msg.role, 'content': msg.content})
            elif msg.file_name:
                file_desc = f"[User attached file: {msg.file_name}]"
                if msg.role == 'user':
                    if not ai_messages or ai_messages[-1]['role'] != 'user':
                        ai_messages.append({'role': 'user', 'content': file_desc})
                    else:
                        ai_messages[-1]['content'] += '\n' + file_desc
        
        if not ai_messages:
            ai_messages = [{'role': 'user', 'content': 'Hello'}]
        
        ai_messages.insert(0, {
            'role': 'system',
            'content': 'You are a helpful AI assistant.'
        })
        
        response = client.chat.completions.create(
            model=Config.DEFAULT_MODEL,
            messages=ai_messages,
            temperature=0.7,
            max_tokens=4000
        )
        
        ai_response = response.choices[0].message.content
        
        assistant_msg = Message(
            conversation_id=conversation_id,
            role='assistant',
            content=ai_response
        )
        db.session.add(assistant_msg)
        db.session.commit()
        
        return jsonify({
            'response': ai_response,
            'conversation_id': conversation_id,
            'title': conversation.title
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Chat error: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
