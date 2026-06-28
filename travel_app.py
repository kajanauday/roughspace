"""
travel_app.py — Chat-based Travel Planner
Flow: Chat with Agent-1 → Itinerary (Agent-2) → Star feedback → Judge metrics → DB + Grafana + Langfuse
Run: uvicorn travel_app:app --host 0.0.0.0 --port 8000
"""
import html as html_lib
import json
import re
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langfuse import observe

from agents import agent2_create_plan, judge_evaluate, langfuse, client, AZURE_DEPLOYMENT, AGENT1_SYSTEM, _build_metrics
from db import init_db, save_to_db, save_llm_metrics, get_db
from grafana import push_records
from langfuse_db import sync_langfuse_to_db
import threading

_sessions: dict = {}   # sid → {history, trip_data, plan, llm_metrics}


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = ""


# ── App startup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API: evaluations for Grafana Infinity plugin ──────────────────────────────

@app.get("/api/evaluations")
def get_evaluations():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, destination, start_date, end_date, budget, currency, num_people,
               journey_type, confidence_score, accuracy, precision_score, recall,
               hallucination, human_feedback, verdict, created_at
        FROM evaluations
        ORDER BY created_at DESC
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); conn.close()
    from decimal import Decimal
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif isinstance(v, Decimal):
                r[k] = float(v)
    return JSONResponse(rows)


# ── API: read-only SQL proxy for Grafana ──────────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    refId: Optional[str] = "A"

@app.post("/api/query")
def run_query(req: QueryRequest):
    """Read-only SQL proxy — Grafana Infinity plugin can POST queries here."""
    q = req.query.strip().rstrip(";")
    # Reject any non-SELECT statements
    if not q.upper().startswith("SELECT"):
        return JSONResponse({"error": "Only SELECT queries are allowed"}, status_code=400)
    # Block destructive keywords (word-boundary match to avoid false positives like created_at)
    import re as _re
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    upper_q = q.upper()
    for kw in forbidden:
        if _re.search(r'\b' + kw + r'\b', upper_q):
            return JSONResponse({"error": f"Keyword {kw} not permitted"}, status_code=400)
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(q)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        cur.close(); conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)
    cur.close(); conn.close()
    from decimal import Decimal
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif isinstance(v, Decimal):
                r[k] = float(v)
    return JSONResponse(rows)


# ── Shared CSS ────────────────────────────────────────────────────────────────

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #f1f5f9; min-height: 100vh; }
.topbar {
  background: #0d1b3e; color: #fff; padding: 14px 24px;
  display: flex; align-items: center; gap: 10px; font-size: 1rem; font-weight: 600;
}
.topbar span { font-size: 1.3rem; }
.container { max-width: 820px; margin: 0 auto; padding: 24px 16px; }
h2 { font-size: 1.1rem; color: #0d1b3e; margin-bottom: 16px; }
button { cursor: pointer; }
a { color: #3b82f6; text-decoration: none; }
a:hover { text-decoration: underline; }
"""

CHAT_CSS = BASE_CSS + """
.chat-wrap {
  display: flex; flex-direction: column;
  height: calc(100vh - 56px);
}
.messages {
  flex: 1; overflow-y: auto; padding: 20px 16px; display: flex;
  flex-direction: column; gap: 12px;
}
.bubble { max-width: 72%; padding: 12px 16px; border-radius: 16px; font-size: 0.92rem; line-height: 1.55; word-break: break-word; }
.bubble.agent { background: #fff; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px; align-self: flex-start; color: #1e293b; }
.bubble.user  { background: #1d4ed8; color: #fff; border-bottom-right-radius: 4px; align-self: flex-end; }
.typing { display: flex; gap: 4px; align-items: center; padding: 14px 16px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; animation: bounce 1.2s infinite; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }
.input-bar {
  background: #fff; border-top: 1px solid #e2e8f0;
  padding: 14px 16px; display: flex; gap: 10px;
}
.input-bar input {
  flex: 1; padding: 11px 14px; border: 1px solid #d1d5db;
  border-radius: 10px; font-size: 0.92rem; outline: none; font-family: inherit;
}
.input-bar input:focus { border-color: #3b82f6; }
.input-bar button {
  background: #1d4ed8; color: #fff; border: none;
  padding: 11px 22px; border-radius: 10px; font-size: 0.92rem; font-weight: 600;
}
.input-bar button:hover { background: #1e40af; }
.input-bar button:disabled { background: #93c5fd; cursor: not-allowed; }
"""

ITINERARY_CSS = BASE_CSS + """
.card { background: #fff; border-radius: 14px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 28px 32px; margin-bottom: 20px; }
.itinerary h1 { font-size: 1.3rem; color: #0d1b3e; margin: 18px 0 6px; }
.itinerary h2 { font-size: 1.1rem; color: #1a3a6b; margin: 16px 0 6px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.itinerary h3 { font-size: 0.97rem; color: #374151; margin: 14px 0 4px; font-weight: 700; }
.itinerary p  { font-size: 0.9rem; color: #374151; line-height: 1.65; margin-bottom: 6px; }
.itinerary ul { margin: 6px 0 10px 20px; }
.itinerary li { font-size: 0.9rem; color: #374151; line-height: 1.6; margin-bottom: 3px; }
.itinerary strong { color: #0d1b3e; }
.itinerary hr { border: none; border-top: 1px solid #e5e7eb; margin: 16px 0; }
.itinerary table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 0.88rem; }
.itinerary th { background: #f1f5f9; padding: 8px 12px; text-align: left; border: 1px solid #e2e8f0; color: #374151; }
.itinerary td { padding: 8px 12px; border: 1px solid #e2e8f0; color: #374151; }
.stars { display: flex; gap: 6px; font-size: 2rem; cursor: pointer; margin: 10px 0 16px; }
.star { color: #d1d5db; transition: color 0.15s; }
.star.lit { color: #f59e0b; }
textarea { width: 100%; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 0.9rem; font-family: inherit; outline: none; resize: vertical; }
textarea:focus { border-color: #3b82f6; }
.submit-btn {
  margin-top: 16px; width: 100%; padding: 13px; background: #1d4ed8;
  color: #fff; border: none; border-radius: 10px; font-size: 0.95rem; font-weight: 700;
}
.submit-btn:hover { background: #1e40af; }
.submit-btn:disabled { background: #93c5fd; cursor: not-allowed; }
.loader { display: none; text-align: center; padding: 40px 20px; }
.spinner { width: 36px; height: 36px; border: 4px solid #e2e8f0; border-top-color: #1d4ed8; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 14px; }
@keyframes spin { to { transform: rotate(360deg); } }
"""

METRICS_CSS = BASE_CSS + """
.card { background: #fff; border-radius: 14px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 28px 32px; margin-bottom: 20px; }
.steps { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
.step { font-size: 0.78rem; background: #d1fae5; color: #065f46; padding: 4px 12px; border-radius: 20px; }
.metric { margin-bottom: 18px; }
.metric-top { display: flex; justify-content: space-between; margin-bottom: 6px; }
.metric-name { font-size: 0.88rem; font-weight: 600; color: #374151; }
.metric-val { font-size: 0.95rem; font-weight: 700; }
.bar-bg { background: #e5e7eb; border-radius: 99px; height: 13px; }
.bar-fill { height: 13px; border-radius: 99px; }
.verdict { background: #f0f9ff; border-left: 4px solid #0ea5e9; padding: 14px 18px; border-radius: 6px; font-size: 0.9rem; color: #0c4a6e; line-height: 1.5; margin-top: 4px; }
.footer { font-size: 0.8rem; color: #9ca3af; margin-top: 16px; display: flex; gap: 16px; }
"""


# ── Markdown → HTML converter ─────────────────────────────────────────────────

def _inline(text: str) -> str:
    text = html_lib.escape(text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
    return text

def md_to_html(md: str) -> str:
    lines = md.split('\n')
    out = []
    in_ul = False
    in_table = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append('</ul>')
            in_ul = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append('</table>')
            in_table = False

    for line in lines:
        stripped = line.strip()

        # Table rows
        if stripped.startswith('|'):
            close_ul()
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(re.match(r'^[-:]+$', c) for c in cells if c):
                # separator row — skip
                continue
            if not in_table:
                out.append('<table>')
                in_table = True
                tag = 'th'
            else:
                tag = 'td'
            out.append('<tr>' + ''.join(f'<{tag}>{_inline(c)}</{tag}>' for c in cells) + '</tr>')
            continue
        else:
            close_table()

        if stripped.startswith('### '):
            close_ul()
            out.append(f'<h3>{_inline(stripped[4:])}</h3>')
        elif stripped.startswith('## '):
            close_ul()
            out.append(f'<h2>{_inline(stripped[3:])}</h2>')
        elif stripped.startswith('# '):
            close_ul()
            out.append(f'<h1>{_inline(stripped[2:])}</h1>')
        elif stripped.startswith('- ') or stripped.startswith('• '):
            if not in_ul:
                out.append('<ul>')
                in_ul = True
            out.append(f'<li>{_inline(stripped[2:])}</li>')
        elif re.match(r'^[-*_]{3,}$', stripped):
            close_ul()
            out.append('<hr/>')
        elif stripped == '':
            close_ul()
            out.append('<br/>')
        else:
            close_ul()
            out.append(f'<p>{_inline(stripped)}</p>')

    close_ul()
    close_table()
    return '\n'.join(out)


# ── Agent-1 chat (one LLM turn) ───────────────────────────────────────────────

import time as _time

@observe(name="agent1-chat-turn")
def agent1_turn(history: list):
    """Returns (message_content, metrics_dict)."""
    messages = [{"role": "system", "content": AGENT1_SYSTEM}] + history
    t0 = _time.time()
    resp = client.chat.completions.create(
        model=AZURE_DEPLOYMENT, messages=messages, name="agent1-chat",
    )
    latency_ms = int((_time.time() - t0) * 1000)
    output = resp.choices[0].message.content
    return output, _build_metrics("agent1", resp.usage, latency_ms, messages, output)


# ── API: chat ─────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    sid = req.session_id

    if not sid:
        sid = str(uuid.uuid4())
        _sessions[sid] = {"history": [], "trip_data": None, "plan": None, "llm_metrics": []}
        _sessions[sid]["history"].append({"role": "user", "content": "Hi, I want to plan a trip."})
    else:
        if sid not in _sessions:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if req.message.strip():
            _sessions[sid]["history"].append({"role": "user", "content": req.message.strip()})

    raw, turn_metrics = agent1_turn(_sessions[sid]["history"])
    _sessions[sid]["history"].append({"role": "assistant", "content": raw})
    _sessions[sid]["llm_metrics"].append(turn_metrics)

    # Check for completion JSON block
    complete = False
    match = re.search(r'```json\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if data.get("complete"):
                complete = True
                _sessions[sid]["trip_data"] = data
        except Exception:
            pass

    # Strip JSON block from display message
    display = re.sub(r'```json.*?```', '', raw, flags=re.DOTALL).strip()
    if not display:
        display = "Perfect, I have everything I need! Generating your itinerary now…"

    return JSONResponse({"session_id": sid, "message": display, "complete": complete})




# ── Route: Chat page ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def chat_page():
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Travel Planner</title>
<style>{CHAT_CSS}</style>
</head><body>
<div class="topbar"><span>✈</span> Travel Planner — Chat with AI</div>
<div class="chat-wrap">
  <div class="messages" id="msgs"></div>
  <div class="input-bar">
    <input id="inp" type="text" placeholder="Type your answer and press Enter…" autocomplete="off" disabled/>
    <button id="sendBtn" disabled onclick="sendMsg()">Send</button>
  </div>
</div>

<script>
let sid = null;

async function call(message) {{
  const res = await fetch('/api/chat', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{session_id: sid, message}})
  }});
  return res.json();
}}

function addBubble(text, who) {{
  const msgs = document.getElementById('msgs');
  const div = document.createElement('div');
  div.className = 'bubble ' + who;
  div.innerText = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}}

function showTyping() {{
  const msgs = document.getElementById('msgs');
  const t = document.createElement('div');
  t.className = 'bubble agent typing'; t.id = 'typing';
  t.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
  msgs.appendChild(t);
  msgs.scrollTop = msgs.scrollHeight;
}}

function removeTyping() {{
  const t = document.getElementById('typing');
  if (t) t.remove();
}}

function setInputEnabled(on) {{
  document.getElementById('inp').disabled = !on;
  document.getElementById('sendBtn').disabled = !on;
  if (on) document.getElementById('inp').focus();
}}

async function sendMsg() {{
  const inp = document.getElementById('inp');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  setInputEnabled(false);
  addBubble(text, 'user');
  showTyping();
  const data = await call(text);
  removeTyping();
  addBubble(data.message, 'agent');
  if (data.complete) {{
    setTimeout(() => window.location.href = '/itinerary/' + data.session_id, 1000);
  }} else {{
    setInputEnabled(true);
  }}
}}

document.getElementById('inp').addEventListener('keydown', function(e) {{
  if (e.key === 'Enter') sendMsg();
}});

// Auto-start
(async () => {{
  showTyping();
  const data = await call('');
  sid = data.session_id;
  removeTyping();
  addBubble(data.message, 'agent');
  setInputEnabled(true);
}})();
</script>
</body></html>
""")


# ── Route: Itinerary + feedback ───────────────────────────────────────────────

@app.get("/itinerary/{sid}", response_class=HTMLResponse)
def itinerary_page(sid: str):
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Your Itinerary</title>
<style>{ITINERARY_CSS}</style>
</head><body>
<div class="topbar"><span>✈</span> Your Travel Itinerary</div>
<div class="container">

  <div id="loadingDiv" class="card">
    <div class="loader" style="display:block">
      <div class="spinner"></div>
      <p style="color:#6b7280;font-size:0.9rem">Agent-2 is building your itinerary…</p>
    </div>
  </div>

  <div id="planDiv" style="display:none">
    <div class="card">
      <div class="itinerary" id="planContent"></div>
    </div>

    <div class="card">
      <h2>How was this plan?</h2>
      <form id="fbForm" method="post" action="/feedback/{sid}">
        <div class="stars" id="stars">
          <span class="star" data-v="1">&#9733;</span>
          <span class="star" data-v="2">&#9733;</span>
          <span class="star" data-v="3">&#9733;</span>
          <span class="star" data-v="4">&#9733;</span>
          <span class="star" data-v="5">&#9733;</span>
        </div>
        <input type="hidden" name="rating" id="ratingVal"/>
        <p id="starErr" style="color:#991b1b;font-size:0.8rem;display:none;margin-bottom:10px">Please select a star rating.</p>
        <label style="font-size:0.85rem;font-weight:600;color:#374151;display:block;margin-bottom:6px">Comments (optional)</label>
        <textarea name="comments" rows="3" placeholder="Share your thoughts on the itinerary…"></textarea>
        <button type="submit" class="submit-btn" id="fbBtn">Submit &amp; Get Evaluation</button>
      </form>
    </div>
  </div>

</div>
<script>
(async () => {{
  const res = await fetch('/api/generate/{sid}', {{method:'POST'}});
  const data = await res.json();
  document.getElementById('loadingDiv').style.display = 'none';
  document.getElementById('planContent').innerHTML = data.plan_html;
  document.getElementById('planDiv').style.display = 'block';
}})();

// Star rating
let rating = 0;
document.querySelectorAll('.star').forEach(s => {{
  s.addEventListener('mouseover', function() {{
    document.querySelectorAll('.star').forEach(x => x.classList.remove('lit'));
    for (let i = 0; i < this.dataset.v; i++) document.querySelectorAll('.star')[i].classList.add('lit');
  }});
  s.addEventListener('click', function() {{
    rating = parseInt(this.dataset.v);
    document.getElementById('ratingVal').value = rating;
    document.getElementById('starErr').style.display = 'none';
  }});
}});
document.getElementById('stars').addEventListener('mouseleave', function() {{
  document.querySelectorAll('.star').forEach((x, i) => {{
    x.classList.toggle('lit', i < rating);
  }});
}});
document.getElementById('fbForm').addEventListener('submit', function(e) {{
  if (!rating) {{ e.preventDefault(); document.getElementById('starErr').style.display = 'block'; return; }}
  document.getElementById('fbBtn').disabled = true;
  document.getElementById('fbBtn').textContent = 'Evaluating…';
}});
</script>
</body></html>
""")


# ── API: generate — returns HTML for the plan ────────────────────────────────

@app.post("/api/generate/{sid}")
def generate_api(sid: str):
    session = _sessions.get(sid)
    if not session or not session.get("trip_data"):
        return JSONResponse({"error": "session not found"}, status_code=404)
    if not session.get("plan"):
        plan, agent2_metrics = agent2_create_plan(session["trip_data"])
        session["plan"] = plan
        session["llm_metrics"].append(agent2_metrics)
    return JSONResponse({"plan_html": md_to_html(session["plan"])})


# ── Route: Feedback → Judge → Metrics ────────────────────────────────────────

@app.post("/feedback/{sid}", response_class=HTMLResponse)
def feedback_page(sid: str, rating: int = Form(...), comments: str = Form("")):
    session = _sessions.get(sid)
    if not session:
        return HTMLResponse('<div style="padding:40px;font-family:sans-serif">Session expired. <a href="/">Start over</a></div>')

    feedback = {"rating": rating * 2, "score": rating * 20, "comments": comments}
    scores, judge_metrics = judge_evaluate(session["trip_data"], session["plan"], feedback)
    record   = save_to_db(session["trip_data"], scores)
    all_llm_metrics = session.get("llm_metrics", []) + [judge_metrics]
    save_llm_metrics(record["id"], all_llm_metrics)
    push_records([record])
    langfuse.flush()
    # Pull Langfuse traces into DB in background (doesn't delay the response)
    threading.Thread(target=sync_langfuse_to_db, args=(record["id"],), daemon=True).start()
    _sessions.pop(sid, None)

    metrics = [
        ("Confidence Score", "confidence_score"),
        ("Accuracy",         "accuracy"),
        ("Precision",        "precision"),
        ("Recall",           "recall"),
        ("Hallucination",    "hallucination"),
        ("Human Rating",     "human_feedback"),
    ]

    def bar_color(v):
        return "#22c55e" if v >= 80 else "#f59e0b" if v >= 60 else "#ef4444"

    bars = ""
    for label, key in metrics:
        v = int(scores.get(key, 0)) if isinstance(scores.get(key), (int, float)) else 0
        c = bar_color(v)
        bars += f"""
<div class="metric">
  <div class="metric-top">
    <span class="metric-name">{label}</span>
    <span class="metric-val" style="color:{c}">{v}<small style="color:#9ca3af;font-size:0.72rem">/100</small></span>
  </div>
  <div class="bar-bg"><div class="bar-fill" style="width:{v}%;background:{c}"></div></div>
</div>"""

    verdict = html_lib.escape(scores.get("summary", ""))
    db_id   = record.get("id", "?")
    dest    = html_lib.escape(session["trip_data"].get("destination", ""))

    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Evaluation Results</title>
<style>{METRICS_CSS}</style>
</head><body>
<div class="topbar"><span>&#10003;</span> Evaluation Complete</div>
<div class="container">
  <div class="card">
    <h2>{dest}</h2>
    <div class="steps">
      <span class="step">&#10003; Agent-1 validated</span>
      <span class="step">&#10003; Agent-2 planned</span>
      <span class="step">&#10003; Judge scored</span>
      <span class="step">&#10003; Saved to DB #{db_id}</span>
      <span class="step">&#10003; Azure Monitor</span>
      <span class="step">&#10003; Langfuse</span>
    </div>
    {bars}
    <div class="verdict">&#128203; <strong>Verdict:</strong> {verdict}</div>
    <div class="footer">
      <a href="https://cloud.langfuse.com" target="_blank">&#128279; View Langfuse traces</a>
      <a href="/">&#43; Plan another trip</a>
    </div>
  </div>
</div>
</body></html>
""")
