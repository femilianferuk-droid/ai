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
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

DATABASE_URL = "postgresql://bothost_db_27588d84c00c:97p2HBIA8y0-PsF83FgAAN6zr_w_aC0nmSK7FAV-tXc@node1.pghost.ru:15808/bothost_db_27588d84c00c"
API_URL = "https://gpt-agent.cc/v1"
API_KEY = "sk-txA1lHYWAWWKKSMnfjkZNo2gRgvjfUtKq7PZWgkA0WMDIxOB"

client = OpenAI(api_key=API_KEY, base_url=API_URL)

MODELS = {
    "claude-sonnet-4.6": "claude-sonnet-4.6",
    "minimax-M2.7": "minimax-M2.7",
    "KIMI-2.6": "KIMI-2.6",
    "DEEPSEEK-V4-FLASH": "DEEPSEEK-V4-FLASH"
}

def log(msg, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}", file=sys.stderr, flush=True)

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON chats(chat_id)")
        conn.commit()
        conn.close()
        log("DB ready")
    except Exception as e:
        log(f"DB init error: {e}", "ERROR")

init_db()

@app.route('/')
def index():
    return HTML

@app.route('/api/chat/new', methods=['POST'])
def new_chat():
    return jsonify({'chat_id': str(uuid.uuid4())})

@app.route('/api/chat/list', methods=['GET'])
def chat_list():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT chat_id, MAX(title) as title, MIN(timestamp) as created, COUNT(*) as cnt
            FROM chats GROUP BY chat_id ORDER BY created DESC
        """)
        chats = [dict(c) for c in cur.fetchall()]
        conn.close()
        return jsonify(chats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/send', methods=['POST'])
def send_message():
    try:
        data = request.json
        msg = data.get('message', '')
        model = data.get('model', 'claude-sonnet-4.6')
        chat_id = data.get('chat_id', 'default')
        
        if not msg:
            return jsonify({'error': 'Empty message'}), 400
        
        log(f"📨 {msg[:50]}... | {model}")
        
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT COUNT(*) as cnt FROM chats WHERE chat_id = %s", (chat_id,))
        cnt = cur.fetchone()['cnt']
        
        title = msg[:50] if cnt == 0 else None
        cur.execute(
            "INSERT INTO chats (chat_id, title, model, role, content) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, title or 'Новый чат', model, 'user', msg)
        )
        if cnt == 0:
            cur.execute("UPDATE chats SET title = %s WHERE chat_id = %s", (msg[:50], chat_id))
        
        cur.execute("SELECT role, content FROM chats WHERE chat_id = %s ORDER BY timestamp ASC LIMIT 20", (chat_id,))
        history = [{"role": m['role'], "content": m['content']} for m in cur.fetchall()]
        
        messages = [{"role": "system", "content": "Ты полезный AI-агент. Отвечай на русском языке."}] + history
        
        start = time.time()
        
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=0.7, max_tokens=2000)
            ai_msg = resp.choices[0].message.content
            elapsed = time.time() - start
            log(f"✅ {elapsed:.2f}s | {len(ai_msg)} chars")
            
            cur.execute("INSERT INTO chats (chat_id, model, role, content) VALUES (%s, %s, %s, %s)",
                       (chat_id, model, 'assistant', ai_msg))
            conn.commit()
            conn.close()
            
            return jsonify({'response': ai_msg, 'model': model, 'chat_id': chat_id,
                          'response_time': round(elapsed, 2), 'timestamp': datetime.now().isoformat()})
        except Exception as e:
            log(f"❌ {e}", "ERROR")
            try:
                resp = client.chat.completions.create(model=model, messages=history, temperature=0.5, max_tokens=1000)
                ai_msg = resp.choices[0].message.content
                elapsed = time.time() - start
                
                cur.execute("INSERT INTO chats (chat_id, model, role, content) VALUES (%s, %s, %s, %s)",
                           (chat_id, model, 'assistant', ai_msg))
                conn.commit()
                conn.close()
                
                return jsonify({'response': ai_msg, 'model': model, 'chat_id': chat_id,
                              'response_time': round(elapsed, 2), 'timestamp': datetime.now().isoformat()})
            except Exception as e2:
                log(f"❌ Retry: {e2}", "ERROR")
                conn.close()
                return jsonify({'error': 'API error', 'details': f'{e}\n\nПопробуйте другую модель'}), 500
    except Exception as e:
        log(f"💥 {traceback.format_exc()}", "ERROR")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/history/<chat_id>', methods=['GET'])
def get_history(chat_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM chats WHERE chat_id = %s ORDER BY timestamp ASC", (chat_id,))
        msgs = [dict(m) for m in cur.fetchall()]
        conn.close()
        return jsonify(msgs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/delete/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM chats WHERE chat_id = %s", (chat_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test', methods=['GET'])
def test_api():
    results = []
    for name, mid in MODELS.items():
        try:
            start = time.time()
            resp = client.chat.completions.create(model=mid, messages=[{"role": "user", "content": "Привет"}], max_tokens=50)
            results.append({"model": name, "status": "✅ OK", "time": round(time.time()-start, 2),
                          "response": resp.choices[0].message.content[:100]})
        except Exception as e:
            results.append({"model": name, "status": "❌ ERROR", "error": str(e)[:200]})
    return jsonify(results)

HTML = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Агент</title>
    <style>
        :root{--bg:#0f0f0f;--bg2:#1a1a1a;--bg3:#2a2a2a;--text:#fff;--text2:#a0a0a0;--accent:#3b82f6;--border:#333;--umsg:#1e3a5f;--aimsg:#1a1a2e;--err:#3a1a1a}
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);height:100dvh;overflow:hidden;-webkit-tap-highlight-color:transparent}
        .app{display:flex;height:100dvh}
        .overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:90;opacity:0;transition:opacity 0.3s}
        .overlay.active{opacity:1}
        .sidebar{width:300px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;transition:transform 0.3s;z-index:100}
        .sidebar-header{padding:16px;border-bottom:1px solid var(--border)}
        .sidebar-logo{font-size:18px;font-weight:700;margin-bottom:12px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
        .btn{width:100%;padding:12px;margin-bottom:8px;border:none;border-radius:10px;font-size:15px;cursor:pointer;transition:all 0.2s}
        .btn-new{background:var(--accent);color:#fff}
        .btn-test{background:var(--bg3);color:var(--text2);border:1px solid var(--border)}
        .btn:active{transform:scale(0.98)}
        .chats-list{flex:1;overflow-y:auto;padding:8px;-webkit-overflow-scrolling:touch}
        .chat-item{padding:12px;margin-bottom:4px;border-radius:10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
        .chat-item:active{background:var(--bg3)}
        .chat-item.active{background:var(--bg3);border-left:3px solid var(--accent)}
        .chat-item-content{flex:1;min-width:0}
        .chat-item-title{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}
        .chat-item-meta{font-size:12px;color:var(--text2)}
        .chat-item-del{background:none;border:none;color:#ef4444;cursor:pointer;padding:8px;border-radius:6px;font-size:14px;opacity:0;transition:all 0.2s}
        .chat-item:hover .chat-item-del,.chat-item:active .chat-item-del{opacity:1}
        .main{flex:1;display:flex;flex-direction:column;min-width:0}
        .header{padding:12px 16px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
        .menu-btn{width:40px;height:40px;background:var(--bg3);border:1px solid var(--border);border-radius:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;padding:8px;flex-shrink:0}
        .menu-btn span{display:block;width:18px;height:2px;background:var(--text);border-radius:2px}
        .header-info{flex:1;min-width:0}
        .header-title{font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .model-select{padding:8px 12px;background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:8px;font-size:13px;cursor:pointer;flex-shrink:0;max-width:160px}
        .messages{flex:1;overflow-y:auto;padding:16px;-webkit-overflow-scrolling:touch}
        .message{max-width:750px;margin:0 auto 20px;display:flex;gap:10px}
        .message.user{flex-direction:row-reverse}
        .msg-avatar{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
        .user .msg-avatar{background:var(--accent)}
        .assistant .msg-avatar{background:#8b5cf6}
        .msg-body{flex:1;min-width:0}
        .msg-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:8px}
        .msg-role{font-size:13px;font-weight:600}
        .msg-meta{font-size:11px;color:var(--text2);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
        .msg-content{padding:12px 16px;border-radius:12px;line-height:1.5;font-size:14px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word}
        .user .msg-content{background:var(--umsg)}
        .assistant .msg-content{background:var(--aimsg)}
        .msg-content.error{background:var(--err);border:1px solid #ef4444}
        .input-area{padding:12px 16px;padding-bottom:max(12px,env(safe-area-inset-bottom));max-width:750px;width:100%;margin:0 auto}
        .input-wrap{display:flex;gap:8px;background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:4px;transition:border-color 0.2s}
        .input-wrap:focus-within{border-color:var(--accent)}
        #msgInput{flex:1;background:transparent;border:none;color:var(--text);font-size:16px;outline:none;resize:none;padding:10px 12px;max-height:120px;min-height:44px;font-family:inherit}
        #msgInput::placeholder{color:#666}
        .send-btn{padding:10px 16px;background:var(--accent);color:#fff;border:none;border-radius:10px;cursor:pointer;font-size:16px;font-weight:500;transition:all 0.2s;white-space:nowrap;flex-shrink:0;min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center}
        .send-btn:active{transform:scale(0.95)}
        .send-btn:disabled{opacity:0.5;cursor:not-allowed}
        .typing{display:flex;gap:4px;padding:4px 0}
        .typing-dot{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:typing 1.4s infinite}
        .typing-dot:nth-child(2){animation-delay:0.2s}
        .typing-dot:nth-child(3){animation-delay:0.4s}
        @keyframes typing{0%,60%,100%{opacity:0.3}30%{opacity:1}}
        .empty{text-align:center;padding:60px 20px;color:var(--text2)}
        .empty-icon{font-size:48px;margin-bottom:16px}
        @media(max-width:768px){
            .sidebar{position:fixed;left:0;top:0;bottom:0;transform:translateX(-100%);width:85%;max-width:320px;z-index:100}
            .sidebar.open{transform:translateX(0);box-shadow:4px 0 20px rgba(0,0,0,0.3)}
            .overlay{display:block;pointer-events:none}
            .overlay.active{pointer-events:all}
            .message{max-width:100%}
            .header{padding:8px 12px}
            .model-select{font-size:12px;padding:6px 8px}
            .input-area{padding:8px 12px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
            .send-btn-text{display:none}
        }
        @media(min-width:769px){.menu-btn{display:none}.overlay{display:none!important}}
    </style>
</head>
<body>
    <div class="overlay" id="overlay" onclick="closeSidebar()"></div>
    <div class="app">
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-logo">🤖 AI Агент</div>
                <button class="btn btn-new" onclick="newChat()">✨ Новый чат</button>
                <button class="btn btn-test" onclick="testAPI()">🔧 Тест API</button>
            </div>
            <div class="chats-list" id="chatsList"></div>
        </div>
        <div class="main">
            <div class="header">
                <button class="menu-btn" onclick="toggleSidebar()"><span></span><span></span><span></span></button>
                <div class="header-info"><div class="header-title" id="headerTitle">AI Агент</div></div>
                <select class="model-select" id="modelSelect" onchange="updateModel()">
                    <option value="claude-sonnet-4.6">Claude 4.6</option>
                    <option value="minimax-M2.7">MiniMax 2.7</option>
                    <option value="KIMI-2.6">KIMI 2.6</option>
                    <option value="DEEPSEEK-V4-FLASH">DeepSeek V4</option>
                </select>
            </div>
            <div class="messages" id="messagesContainer">
                <div class="empty"><div class="empty-icon">🚀</div><div>Создайте новый чат</div></div>
            </div>
            <div class="input-area">
                <div class="input-wrap">
                    <textarea id="msgInput" placeholder="Введите сообщение..." rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMsg()"><span class="send-btn-text">Отправить</span><span>↑</span></button>
                </div>
            </div>
        </div>
    </div>
    <script>
        let chatId=null,processing=false,model='claude-sonnet-4.6';
        async function testAPI(){
            document.getElementById('messagesContainer').innerHTML='<div class="empty"><div class="empty-icon">🔧</div><div>Тестирую...</div></div>';
            const r=await fetch('/api/test'),d=await r.json();
            let h='<div style="max-width:750px;margin:0 auto;"><h3 style="margin-bottom:16px;">Результаты:</h3>';
            d.forEach(x=>{h+=`<div style="background:var(--bg2);padding:12px;border-radius:8px;margin-bottom:8px;"><strong>${x.status} ${x.model}</strong><br>${x.time?'⏱ '+x.time+'с<br>':''}${x.response?'💬 '+x.response+'<br>':''}${x.error?'❌ '+x.error:''}</div>`});
            document.getElementById('messagesContainer').innerHTML=h+'</div>'
        }
        function toggleSidebar(){document.getElementById('sidebar').classList.toggle('open');document.getElementById('overlay').classList.toggle('active')}
        function closeSidebar(){document.getElementById('sidebar').classList.remove('open');document.getElementById('overlay').classList.remove('active')}
        async function init(){await loadChats();if(!chatId)await newChat()}
        async function newChat(){
            const r=await fetch('/api/chat/new',{method:'POST'});
            chatId=(await r.json()).chat_id;await loadChats();
            document.getElementById('messagesContainer').innerHTML='<div class="empty"><div class="empty-icon">💬</div><div>Новый чат</div></div>';
            document.getElementById('headerTitle').textContent='Новый чат';closeSidebar()
        }
        async function loadChats(){
            const r=await fetch('/api/chat/list'),chats=await r.json(),c=document.getElementById('chatsList');
            c.innerHTML='';
            chats.forEach(ch=>{
                const d=document.createElement('div');
                d.className=`chat-item ${ch.chat_id===chatId?'active':''}`;
                d.onclick=()=>switchChat(ch.chat_id);
                d.innerHTML=`<div class="chat-item-content"><div class="chat-item-title">${ch.title||'Новый чат'}</div><div class="chat-item-meta">${new Date(ch.created).toLocaleDateString('ru-RU')} • ${ch.cnt} сообщ.</div></div><button class="chat-item-del" onclick="delChat(event,'${ch.chat_id}')">✕</button>`;
                c.appendChild(d)
            })
        }
        async function switchChat(id){
            chatId=id;await loadChats();closeSidebar();
            const r=await fetch(`/api/chat/history/${id}`),msgs=await r.json(),c=document.getElementById('messagesContainer');
            c.innerHTML='';
            if(msgs.length===0)c.innerHTML='<div class="empty"><div class="empty-icon">💬</div><div>Чат пуст</div></div>';
            else msgs.forEach(m=>addMsg(m.role,m.content,m.model,m.timestamp));
            document.getElementById('headerTitle').textContent=msgs.length>0?msgs[0].content.substring(0,30)+'...':'Новый чат'
        }
        async function delChat(e,id){e.stopPropagation();if(!confirm('Удалить?'))return;await fetch(`/api/chat/delete/${id}`,{method:'DELETE'});if(chatId===id)await newChat();else await loadChats()}
        function updateModel(){model=document.getElementById('modelSelect').value}
        function addMsg(role,content,mdl,ts,rt){
            const c=document.getElementById('messagesContainer'),empty=c.querySelector('.empty');
            if(empty)empty.remove();
            const d=document.createElement('div');d.className=`message ${role}`;
            d.innerHTML=`<div class="msg-avatar">${role==='user'?'👤':'🤖'}</div><div class="msg-body"><div class="msg-header"><span class="msg-role">${role==='user'?'Вы':'AI Агент'}</span><div class="msg-meta"><span>${mdl}</span><span>${new Date(ts).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}</span>${rt?`<span>⏱ ${rt}с</span>`:''}</div></div><div class="msg-content ${content.includes('❌')?'error':''}">${esc(content)}</div></div>`;
            c.appendChild(d);c.scrollTop=c.scrollHeight
        }
        function addTyping(){
            const c=document.getElementById('messagesContainer'),empty=c.querySelector('.empty');
            if(empty)empty.remove();
            const d=document.createElement('div');d.className='message assistant';d.id='typing';
            d.innerHTML='<div class="msg-avatar">🤖</div><div class="msg-body"><div class="msg-header"><span class="msg-role">AI Агент</span></div><div class="msg-content"><div class="typing"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div></div>';
            c.appendChild(d);c.scrollTop=c.scrollHeight
        }
        function esc(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}
        async function sendMsg(){
            if(processing)return;
            const input=document.getElementById('msgInput'),msg=input.value.trim();
            if(!msg||!chatId)return;
            input.value='';input.style.height='auto';processing=true;
            document.getElementById('sendBtn').disabled=true;
            addMsg('user',msg,model,new Date().toISOString());addTyping();
            try{
                const r=await fetch('/api/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,model,chat_id:chatId})}),d=await r.json();
                document.getElementById('typing')?.remove();
                if(d.response){addMsg('assistant',d.response,d.model,d.timestamp,d.response_time);await loadChats()}
                else addMsg('assistant',d.details||`❌ ${d.error}`,model,new Date().toISOString())
            }catch(e){document.getElementById('typing')?.remove();addMsg('assistant',`❌ ${e.message}`,model,new Date().toISOString())}
            processing=false;document.getElementById('sendBtn').disabled=false
        }
        function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg()}}
        function autoResize(t){t.style.height='auto';t.style.height=Math.min(t.scrollHeight,120)+'px'}
        document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSidebar()});
        window.onload=init
    </script>
</body>
</html>
'''

# For Vercel
def handler(request, context):
    return app(request.environ, lambda status, headers: None)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
