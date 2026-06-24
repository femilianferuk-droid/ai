import os
import hashlib
import uuid
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from openai import OpenAI

# ==================== CONFIG ====================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production-32-chars-long!!')

# PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc'
    '@node1.pghost.ru:15808/bothost_db_27588d84c00c'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_timeout': 30,
    'pool_size': 5,
    'max_overflow': 10,
}

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# OpenAI-compatible client
client = OpenAI(
    base_url="https://capi.aerolink.lat/",
    api_key=os.environ.get('OPENAI_API_KEY', 'aero_live_yEEr83dI6tcp7744ZdCXAb7IirUUVM_uVGbor8IeXAk')
)

DEFAULT_MODEL = "claude opus 4.8"

# ==================== MODELS ====================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), default='New Chat')
    model = db.Column(db.String(100), default=DEFAULT_MODEL)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.String(36), db.ForeignKey('conversations.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def init_db():
    """Initialize database - only called once during first request or manually"""
    with app.app_context():
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                db.session.add(User(username='admin', password_hash=hash_password('admin123')))
                db.session.commit()
                print("Database initialized successfully")
        except Exception as e:
            print(f"Database initialization error: {e}")
            db.session.rollback()
            raise

# ==================== DATABASE INITIALIZATION ====================
# Ensure tables exist on first request
@app.before_request
def before_request():
    try:
        db.create_all()
    except:
        pass

# ==================== HTML ====================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Chat</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; height: 100vh; overflow: hidden; }
        
        /* Login */
        .login-wrap { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); }
        .login-box { background: #1e1e2e; padding: 40px; border-radius: 16px; width: 100%; max-width: 400px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
        .login-box h2 { margin-bottom: 24px; text-align: center; color: #fff; font-size: 24px; }
        .input-group { margin-bottom: 20px; }
        .input-group label { display: block; margin-bottom: 8px; color: #a0a0b0; font-size: 14px; }
        .input-group input { width: 100%; padding: 12px 16px; border: 1px solid #334; border-radius: 10px; background: #16162a; color: #fff; font-size: 15px; outline: none; }
        .input-group input:focus { border-color: #6366f1; }
        .btn { width: 100%; padding: 12px; border: none; border-radius: 10px; background: #4f46e5; color: white; font-size: 16px; cursor: pointer; transition: .2s; font-weight: 500; }
        .btn:hover { background: #4338ca; }
        .error { color: #ef4444; margin-top: 12px; text-align: center; font-size: 14px; }
        
        /* App */
        .app { display: flex; height: 100vh; }
        .sidebar { width: 300px; background: #111; border-right: 1px solid #222; display: flex; flex-direction: column; }
        .sidebar-header { padding: 20px; border-bottom: 1px solid #222; }
        .sidebar-header h3 { color: #fff; font-size: 18px; margin-bottom: 12px; }
        .new-chat-btn { width: 100%; padding: 10px; border: 1px solid #333; border-radius: 10px; background: transparent; color: #fff; cursor: pointer; transition: .2s; font-size: 14px; }
        .new-chat-btn:hover { background: #1a1a1a; }
        .conversations { flex: 1; overflow-y: auto; padding: 12px; }
        .conv-item { padding: 12px; border-radius: 10px; cursor: pointer; margin-bottom: 6px; color: #9ca3af; font-size: 14px; transition: .2s; display: flex; justify-content: space-between; align-items: center; }
        .conv-item:hover, .conv-item.active { background: #1f1f1f; color: #fff; }
        .conv-item .del { opacity: 0; color: #ef4444; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
        .conv-item:hover .del { opacity: 1; }
        .conv-item .del:hover { background: #ef444420; }
        .user-info { padding: 16px; border-top: 1px solid #222; font-size: 14px; color: #9ca3af; display: flex; justify-content: space-between; align-items: center; }
        .logout-btn { color: #ef4444; text-decoration: none; font-size: 13px; }
        
        .main { flex: 1; display: flex; flex-direction: column; background: #0a0a0a; }
        .chat-header { padding: 16px 24px; border-bottom: 1px solid #222; display: flex; justify-content: space-between; align-items: center; }
        .chat-header h4 { color: #fff; font-weight: 500; }
        .model-select { padding: 8px 12px; border-radius: 8px; background: #111; border: 1px solid #333; color: #fff; font-size: 14px; outline: none; cursor: pointer; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 24px; }
        .message { max-width: 800px; margin: 0 auto 24px; display: flex; gap: 14px; animation: fadeIn .3s ease; }
        .message.user { flex-direction: row-reverse; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .avatar { width: 32px; height: 32px; border-radius: 50%; background: #4f46e5; display: flex; align-items: center; justify-content: center; font-size: 14px; color: white; flex-shrink: 0; }
        .message.user .avatar { background: #10b981; }
        .bubble { background: #161b22; padding: 16px 20px; border-radius: 16px; line-height: 1.6; font-size: 15px; color: #e6edf3; white-space: pre-wrap; word-wrap: break-word; }
        .message.user .bubble { background: #1e3a5f; }
        .chat-input-container { padding: 20px 24px; border-top: 1px solid #222; }
        .chat-input-wrap { max-width: 800px; margin: 0 auto; position: relative; }
        .chat-input { width: 100%; padding: 16px 56px 16px 20px; border: 1px solid #333; border-radius: 16px; background: #111; color: #fff; font-size: 15px; resize: none; outline: none; min-height: 56px; max-height: 200px; font-family: inherit; }
        .chat-input:focus { border-color: #4f46e5; }
        .send-btn { position: absolute; right: 10px; bottom: 10px; width: 36px; height: 36px; border-radius: 10px; background: #4f46e5; border: none; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: .2s; }
        .send-btn:hover { background: #4338ca; }
        .send-btn:disabled { opacity: .4; cursor: not-allowed; }
        
        .typing { display: flex; gap: 4px; padding: 4px 0; }
        .typing-dot { width: 8px; height: 8px; background: #666; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out; }
        .typing-dot:nth-child(2) { animation-delay: .2s; }
        .typing-dot:nth-child(3) { animation-delay: .4s; }
        @keyframes bounce { 0%, 80%, 100% { transform: translateY(0); } 40% { transform: translateY(-6px); } }
        
        .empty { text-align: center; padding: 80px 20px; color: #555; }
        .empty h1 { font-size: 56px; margin-bottom: 16px; }
        .empty p { font-size: 16px; }
        
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #444; }
    </style>
</head>
<body>
    {% if not current_user.is_authenticated %}
    <div class="login-wrap">
        <div class="login-box">
            <h2>🔐 AI Chat</h2>
            <form id="loginForm">
                <div class="input-group">
                    <label>Username</label>
                    <input type="text" id="username" required value="admin" autocomplete="username">
                </div>
                <div class="input-group">
                    <label>Password</label>
                    <input type="password" id="password" required value="admin123" autocomplete="current-password">
                </div>
                <button type="submit" class="btn">Sign In</button>
                <div class="error" id="loginError"></div>
            </form>
        </div>
    </div>
    {% else %}
    <div class="app">
        <div class="sidebar">
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
                <h4 id="chatTitle">New Chat</h4>
                <select class="model-select" id="modelSelect"></select>
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="empty">
                    <h1>🤖</h1>
                    <p>Start a conversation with AI</p>
                </div>
            </div>
            <div class="chat-input-container">
                <div class="chat-input-wrap">
                    <textarea class="chat-input" id="chatInput" placeholder="Message..." rows="1"></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>
    </div>
    {% endif %}
    
    <script>
        let currentConvId = null;
        
        {% if current_user.is_authenticated %}
        document.addEventListener('DOMContentLoaded', () => {
            loadConversations();
            loadModels();
            const input = document.getElementById('chatInput');
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
            });
            input.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 200) + 'px';
            });
        });
        {% endif %}
        
        document.getElementById('loginForm')?.addEventListener('submit', async e => {
            e.preventDefault();
            const res = await fetch('/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    username: document.getElementById('username').value,
                    password: document.getElementById('password').value
                })
            });
            const data = await res.json();
            if (data.success) location.reload();
            else document.getElementById('loginError').textContent = data.error || 'Invalid credentials';
        });
        
        async function loadModels() {
            try {
                const res = await fetch('/models');
                const data = await res.json();
                const sel = document.getElementById('modelSelect');
                sel.innerHTML = '';
                (data.models || []).forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.id;
                    opt.textContent = m.id;
                    if (m.id === '{{ default_model }}') opt.selected = true;
                    sel.appendChild(opt);
                });
            } catch(e) { console.error('Failed to load models', e); }
        }
        
        async function loadConversations() {
            const res = await fetch('/conversations');
            const data = await res.json();
            const container = document.getElementById('conversations');
            container.innerHTML = '';
            (data.conversations || []).forEach(c => {
                const div = document.createElement('div');
                div.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
                div.innerHTML = `<span>${escapeHtml(c.title)}</span><span class="del" onclick="deleteConv('${c.id}', event)">×</span>`;
                div.onclick = () => loadChat(c.id);
                container.appendChild(div);
            });
        }
        
        async function newChat() {
            currentConvId = null;
            document.getElementById('chatTitle').textContent = 'New Chat';
            document.getElementById('chatMessages').innerHTML = `<div class="empty"><h1>🤖</h1><p>Start a conversation with AI</p></div>`;
            loadConversations();
        }
        
        async function loadChat(convId) {
            currentConvId = convId;
            const res = await fetch('/conversations/' + convId);
            const data = await res.json();
            document.getElementById('chatTitle').textContent = escapeHtml(data.conversation.title);
            if (data.conversation.model) document.getElementById('modelSelect').value = data.conversation.model;
            
            const container = document.getElementById('chatMessages');
            container.innerHTML = '';
            (data.messages || []).forEach(m => appendMessage(m.role, m.content));
            loadConversations();
        }
        
        function appendMessage(role, content) {
            const container = document.getElementById('chatMessages');
            container.querySelector('.empty')?.remove();
            const div = document.createElement('div');
            div.className = 'message ' + role;
            const avatar = role === 'user' ? '👤' : '🤖';
            div.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble">${escapeHtml(content)}</div>`;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
        
        function showTyping() {
            const container = document.getElementById('chatMessages');
            container.querySelector('.empty')?.remove();
            const div = document.createElement('div');
            div.className = 'message assistant';
            div.id = 'typing';
            div.innerHTML = `<div class="avatar">🤖</div><div class="bubble"><div class="typing"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
        
        function hideTyping() { document.getElementById('typing')?.remove(); }
        
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const btn = document.getElementById('sendBtn');
            const text = input.value.trim();
            if (!text) return;
            
            input.value = ''; input.style.height = 'auto';
            appendMessage('user', text);
            btn.disabled = true; showTyping();
            
            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        message: text,
                        conversation_id: currentConvId,
                        model: document.getElementById('modelSelect').value
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
            }
        }
        
        async function deleteConv(id, e) {
            e.stopPropagation();
            if (!confirm('Delete this conversation?')) return;
            await fetch('/conversations/' + id, {method: 'DELETE'});
            if (currentConvId === id) newChat();
            else loadConversations();
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>
'''

# ==================== ROUTES ====================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, default_model=DEFAULT_MODEL)

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    try:
        user = User.query.filter_by(username=username).first()
        if user and user.password_hash == hash_password(password):
            login_user(user)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Invalid username or password'})
    except Exception as e:
        # If table doesn't exist yet, create it and try again
        try:
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                db.session.add(User(username='admin', password_hash=hash_password('admin123')))
                db.session.commit()
            user = User.query.filter_by(username=username).first()
            if user and user.password_hash == hash_password(password):
                login_user(user)
                return jsonify({'success': True})
        except:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')

@app.route('/models')
@login_required
def get_models():
    try:
        resp = client.models.list()
        models = [{'id': m.id} for m in resp.data]
        return jsonify({'models': models})
    except Exception:
        return jsonify({'models': [
            {'id': DEFAULT_MODEL},
            {'id': 'claude-sonnet-4'},
            {'id': 'gpt-4o'},
            {'id': 'gpt-4o-mini'}
        ]})

@app.route('/conversations')
@login_required
def list_conversations():
    try:
        convs = Conversation.query.filter_by(user_id=current_user.id)\
                  .order_by(Conversation.updated_at.desc()).all()
        return jsonify({'conversations': [
            {'id': c.id, 'title': c.title, 'model': c.model, 'updated_at': c.updated_at.isoformat()}
            for c in convs
        ]})
    except Exception as e:
        return jsonify({'error': str(e), 'conversations': []})

@app.route('/conversations/<conv_id>')
@login_required
def get_conversation(conv_id):
    try:
        conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first_or_404()
        msgs = Message.query.filter_by(conversation_id=conv_id).order_by(Message.created_at).all()
        return jsonify({
            'conversation': {'id': conv.id, 'title': conv.title, 'model': conv.model},
            'messages': [{'role': m.role, 'content': m.content} for m in msgs]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/conversations/<conv_id>', methods=['DELETE'])
@login_required
def delete_conversation(conv_id):
    try:
        conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first_or_404()
        Message.query.filter_by(conversation_id=conv_id).delete()
        db.session.delete(conv)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json() or {}
    user_text = data.get('message', '').strip()
    conv_id = data.get('conversation_id')
    model = data.get('model', DEFAULT_MODEL)
    
    if not user_text:
        return jsonify({'error': 'Empty message'}), 400
    
    try:
        # Get or create conversation
        if conv_id:
            conv = Conversation.query.filter_by(id=conv_id, user_id=current_user.id).first()
            if not conv:
                return jsonify({'error': 'Conversation not found'}), 404
            conv.model = model
            conv.updated_at = datetime.utcnow()
        else:
            title = (user_text[:50] + '...') if len(user_text) > 50 else user_text
            conv = Conversation(user_id=current_user.id, title=title, model=model)
            db.session.add(conv)
            db.session.flush()
            conv_id = conv.id
        
        # Save user message
        db.session.add(Message(conversation_id=conv_id, role='user', content=user_text))
        
        # Build history
        history = Message.query.filter_by(conversation_id=conv_id).order_by(Message.created_at).all()
        messages = [{'role': m.role, 'content': m.content} for m in history]
        if len(messages) == 1:
            messages.insert(0, {'role': 'system', 'content': 'You are a helpful AI assistant.'})
        
        # Get AI response
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=4000
        )
        answer = response.choices[0].message.content
        
        # Save assistant message
        db.session.add(Message(conversation_id=conv_id, role='assistant', content=answer))
        db.session.commit()
        
        return jsonify({
            'response': answer,
            'conversation_id': conv_id,
            'title': conv.title
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Chat error: {str(e)}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

# ==================== HEALTH CHECK ====================
@app.route('/health')
def health():
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

# ==================== INIT & RUN ====================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 3000)))
