#!/usr/bin/env python3
"""
feedback_app.py — Lightweight feedback form served via FastAPI.
Run: uvicorn feedback_app:app --host 0.0.0.0 --port 8000
Then open: http://<VM-IP>:8000
"""

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from db import get_db

app = FastAPI()

HTML_FORM = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Travel Planner — Feedback</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      background: #f0f4f9;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }}
    .card {{
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.10);
      padding: 32px 36px;
      width: 100%;
      max-width: 480px;
    }}
    h2 {{ font-size: 1.3rem; color: #0d1b3e; margin-bottom: 4px; }}
    .sub {{ font-size: 0.85rem; color: #6b7280; margin-bottom: 24px; }}
    label {{ font-size: 0.82rem; font-weight: 600; color: #374151; display: block; margin-bottom: 6px; }}
    select, textarea {{
      width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
      border-radius: 8px; font-size: 0.88rem; color: #111;
      margin-bottom: 18px; outline: none; font-family: inherit;
    }}
    select:focus, textarea:focus {{ border-color: #3b82f6; }}
    .rating-row {{ display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }}
    .rating-btn {{
      width: 40px; height: 40px; border-radius: 8px;
      border: 1.5px solid #d1d5db; background: #fff;
      font-size: 0.88rem; font-weight: 600; cursor: pointer;
      color: #374151; transition: all 0.15s;
    }}
    .rating-btn:hover {{ background: #eff6ff; border-color: #3b82f6; color: #1d4ed8; }}
    .rating-btn.selected {{ background: #1d4ed8; color: #fff; border-color: #1d4ed8; }}
    input[type=hidden] {{ display: none; }}
    button[type=submit] {{
      width: 100%; padding: 12px; background: #1d4ed8; color: #fff;
      border: none; border-radius: 8px; font-size: 0.95rem;
      font-weight: 700; cursor: pointer; margin-top: 4px;
    }}
    button[type=submit]:hover {{ background: #1e40af; }}
    .rating-error {{ color: #991b1b; font-size: 0.8rem; margin-top: -12px; margin-bottom: 12px; display: none; }}
    .msg {{ text-align: center; padding: 14px; border-radius: 8px; margin-bottom: 18px; font-size: 0.88rem; }}
    .msg.ok  {{ background: #d1fae5; color: #065f46; }}
    .msg.err {{ background: #fee2e2; color: #991b1b; }}
  </style>
</head>
<body>
<div class="card">
  <h2>&#127775; Human Feedback</h2>
  <p class="sub">Rate the travel plan generated for a specific run</p>

  {message}

  <form method="post" action="/feedback">
    <label>Select Run</label>
    <select name="run_id" required>
      <option value="">— choose a run —</option>
      {run_options}
    </select>

    <label>Rating (1 – 10)</label>
    <div class="rating-row" id="ratingRow">
      {rating_buttons}
    </div>
    <input type="hidden" name="rating" id="ratingVal" value=""/>
    <div class="rating-error" id="ratingError">Please select a rating before submitting.</div>

    <label>Comments</label>
    <textarea name="comments" rows="3" placeholder="What was good or needs improvement?"></textarea>

    <button type="submit">Submit Feedback</button>
  </form>
</div>

<script>
  document.querySelectorAll('.rating-btn').forEach(btn => {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault();
      document.querySelectorAll('.rating-btn').forEach(b => b.classList.remove('selected'));
      this.classList.add('selected');
      document.getElementById('ratingVal').value = this.dataset.val;
      document.getElementById('ratingError').style.display = 'none';
    }});
  }});

  document.querySelector('form').addEventListener('submit', function(e) {{
    if (!document.getElementById('ratingVal').value) {{
      e.preventDefault();
      document.getElementById('ratingError').style.display = 'block';
    }}
  }});
</script>
</body>
</html>
"""


def fetch_runs() -> list[dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, destination, journey_type, created_at::date, human_feedback
        FROM evaluations ORDER BY created_at DESC LIMIT 30
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


@app.get("/", response_class=HTMLResponse)
def form():
    runs = fetch_runs()
    options = "".join(
        f'<option value="{r["id"]}">'
        f'#{r["id"]} — {r["destination"]} ({r["journey_type"]}) | {r["created_at"]} '
        f'| current: {r["human_feedback"]}/100'
        f'</option>'
        for r in runs
    )
    buttons = "".join(
        f'<button class="rating-btn" data-val="{i}" onclick="return false">{i}</button>'
        for i in range(1, 11)
    )
    return HTML_FORM.format(run_options=options, rating_buttons=buttons, message="")


@app.post("/feedback", response_class=HTMLResponse)
def submit(run_id: int = Form(...), rating: int = Form(...), comments: str = Form("")):
    try:
        human_feedback = rating * 10
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE evaluations SET human_feedback = %s WHERE id = %s",
            (human_feedback, run_id)
        )
        conn.commit()
        cur.close(); conn.close()
        msg = f'<div class="msg ok">✓ Feedback saved — Run #{run_id} rated {rating}/10</div>'
    except Exception as e:
        msg = f'<div class="msg err">✗ Error: {e}</div>'

    runs = fetch_runs()
    options = "".join(
        f'<option value="{r["id"]}">'
        f'#{r["id"]} — {r["destination"]} ({r["journey_type"]}) | {r["created_at"]} '
        f'| current: {r["human_feedback"]}/100'
        f'</option>'
        for r in runs
    )
    buttons = "".join(
        f'<button class="rating-btn" data-val="{i}" onclick="return false">{i}</button>'
        for i in range(1, 11)
    )
    return HTML_FORM.format(run_options=options, rating_buttons=buttons, message=msg)
