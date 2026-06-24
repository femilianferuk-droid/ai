import os
import psycopg2
from urllib.parse import urlparse
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
import openai

DATABASE_URL = "postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c"
OPENAI_API_TOKEN = "aero_live_yEEr83dI6tcp7744ZdCXAb7IirUUVM_uVGbor8IeXAk"
OPENAI_BASE_URL = "https://capi.aerolink.lat/"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "vercel-flask-secret-key-2024")

client = openai.OpenAI(
    api_key=OPENAI_API_TOKEN,
    base_url=OPENAI_BASE_URL
)

AVAILABLE_MODELS = [
    "claude-opus-4.8",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4",
    "claude-sonnet-4",
    "claude-haiku-3.5",
    "gemini-pro-1.5",
    "deepseek-chat",
    "llama-3.1-70b",
    "mistral-large",
]

DEFAULT_MODEL = "claude-opus-4.8"
_db_initialized = False


def get_db_connection():
    parsed = urlparse(DATABASE_URL)
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        database=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password
    )


def init_db():
    global _db_initialized
    if _db_initialized:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                user VARCHAR(50),
                session_id VARCHAR(100),
                title VARCHAR(255) DEFAULT 'New chat',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                chat_session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role VARCHAR(20),
                content TEXT,
                model VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        _db_initialized = True
    except Exception:
        pass


def save_message(chat_session_id, role, content, model):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (chat_session_id, role, content, model) VALUES (%s, %s, %s, %s)",
        (chat_session_id, role, content, model)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_chat_messages(chat_session_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content, model FROM messages WHERE chat_session_id = %s ORDER BY id ASC",
        (chat_session_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"role": r[0], "content": r[1], "model": r[2]} for r in rows]


def get_user_sessions(username):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, created_at FROM chat_sessions WHERE user = %s ORDER BY created_at DESC",
        (username,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "title": r[1], "created_at": r[2].isoformat() if r[2] else ""} for r in rows]


def create_session(username):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_sessions (user, session_id) VALUES (%s, %s) RETURNING id",
        (username, username)
    )
    sid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return sid


def delete_chat_session(chat_session_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE chat_session_id = %s", (chat_session_id,))
    cur.execute("DELETE FROM chat_sessions WHERE id = %s", (chat_session_id,))
    conn.commit()
    cur.close()
    conn.close()


# ─── HTML ──────────────────────────────────────────────────────────────────────

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Chat — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f1a;color:#e0e0e0;display:flex;align-items:center;justify-content:center;height:100vh}
.login-box{background:#1a1a2e;border:1px solid #2d2d44;border-radius:12px;padding:40px;width:360px}
h1{font-size:24px;margin-bottom:8px;color:#a78bfa;text-align:center}
p.sub{text-align:center;color:#888;margin-bottom:28px;font-size:14px}
.error{background:#2d1a1a;border:1px solid #7a3030;color:#ff8080;padding:10px;border-radius:6px;margin-bottom:16px;font-size:14px;text-align:center}
label{display:block;margin-bottom:6px;font-size:13px;color:#aaa}
input{width:100%;padding:10px 14px;border:1px solid #2d2d44;border-radius:8px;background:#0f0f1a;color:#e0e0e0;font-size:14px;outline:none;margin-bottom:16px}
input:focus{border-color:#a78bfa}
button{width:100%;padding:12px;background:#7c3aed;color:white;border:none;border-radius:8px;font-size:15px;cursor:pointer;font-weight:600}
button:hover{background:#6d28d9}
</style>
</head>
<body>
<div class="login-box">
<h1>AI Chat</h1>
<p class="sub">Sign in to continue</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="POST">
<label>Username</label>
<input type="text" name="username" required placeholder="admin">
<label>Password</label>
<input type="password" name="password" required placeholder="******">
<button type="submit">Login</button>
</form>
</div>
</body>
</html>"""

CHAT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Chat</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0f0f1a;--sidebar:#13131f;--border:#1e1e30;--accent:#7c3aed;--hover:#1a1a2e;--text:#e0e0e0;--muted:#6b6b8a;--user:#2d1f7a;--ai:#1a1a2e}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}
.sidebar{width:260px;background:var(--sidebar);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.sidebar-header{padding:16px;border-bottom:1px solid var(--border)}
.sidebar-header h2{font-size:16px;color:#a78bfa}
.new-chat-btn{display:block;margin:12px;padding:10px;background:var(--accent);color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;text-align:center;text-decoration:none}
.new-chat-btn:hover{background:#6d28d9}
.sessions-list{flex:1;overflow-y:auto;padding:8px}
.session-item{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:2px;color:var(--muted);font-size:13px;text-decoration:none}
.session-item:hover,.session-item.active{background:var(--hover);color:var(--text)}
.session-item.active{border-left:3px solid var(--accent)}
.session-item a{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:inherit;text-decoration:none}
.delete-btn{background:none;border:none;color:#555;cursor:pointer;font-size:16px;padding:0 4px;opacity:0;transition:opacity .2s}
.session-item:hover .delete-btn{opacity:1}
.delete-btn:hover{color:#ff6060}
.sidebar-footer{padding:12px;border-top:1px solid var(--border)}
.logout-btn{display:block;width:100%;padding:8px;background:none;border:1px solid var(--border);color:var(--muted);border-radius:6px;cursor:pointer;font-size:13px;text-align:center}
.logout-btn:hover{border-color:var(--accent);color:var(--text)}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-header{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;background:var(--sidebar)}
.chat-header select{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:13px;cursor:pointer;outline:none}
.chat-header select:focus{border-color:var(--accent)}
.model-label{font-size:13px;color:var(--muted)}
.chat-header span{font-size:12px;color:#444;margin-left:auto}
.messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:16px}
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--muted);gap:12px;font-size:15px}
.msg{max-width:720px;width:100%;padding:14px 18px;border-radius:12px;line-height:1.6;font-size:14px;white-space:pre-wrap;word-break:break-word}
.msg.user{background:var(--user);align-self:flex-end;border-bottom-right-radius:4px}
.msg.ai{background:var(--ai);border:1px solid var(--border);align-self:flex-start;border-bottom-left-radius:4px}
.msg.ai code{background:#0d0d1a;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:13px}
.msg.ai pre{background:#0d0d1a;padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0}
.msg.ai pre code{background:none;padding:0}
.typing-indicator{display:flex;gap:4px;padding:14px 18px;border-radius:12px;background:var(--ai);border:1px solid var(--border);align-self:flex-start;max-width:720px;width:100%}
.typing-dot{width:8px;height:8px;background:var(--muted);border-radius:50%;animation:bounce 1.4s infinite}
.typing-dot:nth-child(2){animation-delay:.2s}.typing-dot:nth-child(3){animation-delay:.4s}
@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
.input-area{padding:16px 20px;border-top:1px solid var(--border);background:var(--sidebar)}
.input-row{display:flex;gap:10px;align-items:flex-end;max-width:760px;margin:0 auto}
.input-row textarea{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px 14px;color:var(--text);font-size:14px;font-family:inherit;resize:none;outline:none;min-height:48px;max-height:160px}
.input-row textarea:focus{border-color:var(--accent)}
.send-btn{padding:12px 18px;background:var(--accent);color:white;border:none;border-radius:10px;cursor:pointer;font-size:14px;font-weight:600;white-space:nowrap}
.send-btn:hover{background:#6d28d9}
.send-btn:disabled{background:#3b2a70;cursor:not-allowed}
@media(max-width:640px){.sidebar{width:0;display:none}.sidebar.open{width:260px;display:flex}}
</style>
</head>
<body>
<div class="sidebar" id="sidebar">
<div class="sidebar-header"><h2>AI Chat</h2></div>
<a href="/chat/new" class="new-chat-btn">+ New chat</a>
<div class="sessions-list">
{% for s in sessions %}
<div class="session-item {% if s.id == current_session_id %}active{% endif %}">
<a href="/chat/{{ s.id }}">{{ s.title }}</a>
<button class="delete-btn" onclick="deleteSession({{ s.id }})">✕</button>
</div>
{% endfor %}
</div>
<div class="sidebar-footer">
<form method="POST" action="/logout">
<button type="submit" class="logout-btn">Logout</button>
</form>
</div>
</div>

<div class="main">
<div class="chat-header">
<span class="model-label">Model:</span>
<select id="modelSelect" onchange="changeModel(this.value)">
{% for m in models %}
<option value="{{ m }}" {% if m == current_model %}selected{% endif %}>{{ m }}</option>
{% endfor %}
</select>
<span>{{ username }}</span>
</div>

<div class="messages" id="messages">
{% if not messages %}
<div class="empty-state">🤖<div>Ask anything — I'm here to help</div></div>
{% else %}
{% for msg in messages %}
<div class="msg {{ msg.role }}">{{ msg.content }}</div>
{% endfor %}
{% endif %}
</div>

<div class="input-area">
<div class="input-row">
<textarea id="msgInput" rows="1" placeholder="Type your message..." onkeydown="handleKey(event)" autofocus></textarea>
<button class="send-btn" id="sendBtn" onclick="sendMessage()">Send</button>
</div>
</div>
</div>

<script>
const sessionId = {{ current_session_id }};
let currentModel = "{{ current_model }}";

function scrollBottom(){
  const el=document.getElementById('messages');
  el.scrollTop=el.scrollHeight;
}

function handleKey(e){
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}
}

async function sendMessage(){
  const input=document.getElementById('msgInput');
  const content=input.value.trim();
  if(!content)return;
  const btn=document.getElementById('sendBtn');
  btn.disabled=true;
  input.value='';

  const msgs=document.getElementById('messages');
  const empty=msgs.querySelector('.empty-state');
  if(empty)empty.remove();

  const userDiv=document.createElement('div');
  userDiv.className='msg user';
  userDiv.textContent=content;
  msgs.appendChild(userDiv);
  scrollBottom();

  const typing=document.createElement('div');
  typing.className='typing-indicator';
  typing.innerHTML='<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  msgs.appendChild(typing);
  scrollBottom();

  try{
    const res=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:sessionId,content,model:currentModel})
    });
    const data=await res.json();
    typing.remove();
    const aiDiv=document.createElement('div');
    aiDiv.className='msg ai';
    aiDiv.textContent=data.error?'Error: '+data.error:data.content;
    msgs.appendChild(aiDiv);
  }catch(e){
    typing.remove();
    const errDiv=document.createElement('div');
    errDiv.className='msg ai';
    errDiv.textContent='Network error. Please try again.';
    msgs.appendChild(errDiv);
  }
  btn.disabled=false;
  input.focus();
  scrollBottom();
}

function changeModel(model){currentModel=model;}

async function deleteSession(id){
  if(!confirm('Delete this chat?'))return;
  await fetch('/api/session/'+id,{method:'DELETE'});
  window.location.href='/chat/new';
}

document.getElementById('msgInput').addEventListener('input',function(){
  this.style.height='auto';
  this.style.height=Math.min(this.scrollHeight,160)+'px';
});
</script>
</body>
</html>"""


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == "admin" and password == "admin123":
            session["user"] = username
            return redirect(url_for("chat_new"))
        return render_template_string(LOGIN_PAGE, error="Invalid credentials")
    return render_template_string(LOGIN_PAGE)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/chat/new")
def chat_new():
    if "user" not in session:
        return redirect(url_for("login"))
    init_db()
    username = session["user"]
    sid = create_session(username)
    return redirect(url_for("chat_view", session_id=sid))


@app.route("/chat/<int:session_id>")
def chat_view(session_id):
    if "user" not in session:
        return redirect(url_for("login"))
    init_db()
    username = session["user"]
    sessions = get_user_sessions(username)
    messages = get_chat_messages(session_id)
    current_model = DEFAULT_MODEL
    if messages:
        current_model = messages[-1].get("model", DEFAULT_MODEL)
    return render_template_string(
        CHAT_PAGE,
        username=username,
        sessions=sessions,
        messages=messages,
        current_session_id=session_id,
        current_model=current_model,
        models=AVAILABLE_MODELS
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    session_id = data.get("session_id")
    content = data.get("content", "").strip()
    model = data.get("model", DEFAULT_MODEL)
    if not content:
        return jsonify({"error": "Empty message"})
    try:
        history = get_chat_messages(session_id)
        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": content})
        resp = client.chat.completions.create(model=model, messages=messages)
        reply = resp.choices[0].message.content
        save_message(session_id, "user", content, model)
        save_message(session_id, "assistant", reply, model)
        return jsonify({"content": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/model", methods=["POST"])
def api_model():
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"ok": True})


@app.route("/api/session/<int:session_id>", methods=["DELETE"])
def api_delete_session(session_session_id):
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    delete_chat_session(session_session_id)
    return jsonify({"ok": True})


def handler(environ, start_response):
    return app(environ, start_response)
