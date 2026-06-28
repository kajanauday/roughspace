"""
langfuse_db.py — Fetches traces from Langfuse API and stores them in PostgreSQL.

Called after langfuse.flush() so every agent run's traces land in your DB.
"""
import json
import os
import time
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST       = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

AUTH = (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)


def _get(path: str, **params):
    for attempt in range(4):
        resp = requests.get(f"{LANGFUSE_HOST}{path}", auth=AUTH, params=params, timeout=15)
        if resp.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"  [Langfuse→DB] Rate limited, retrying in {wait}s…")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise Exception("Langfuse API rate limit exceeded after retries")


def sync_langfuse_to_db(evaluation_id: int, since_seconds: int = 90):
    """
    Fetch traces created in the last `since_seconds` seconds from Langfuse,
    extract every GENERATION observation, and upsert into langfuse_traces.
    """
    # Give Langfuse a moment to finish processing after flush()
    time.sleep(4)

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=since_seconds)

    # Fetch recent traces
    data = _get("/api/public/traces", limit=50)
    traces = data.get("data", [])

    # Keep only traces newer than cutoff
    recent = []
    for t in traces:
        ts = datetime.datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
        if ts >= cutoff:
            recent.append(t)

    if not recent:
        print("  [Langfuse→DB] No recent traces found.")
        return

    from db import get_db
    conn = get_db()
    cur  = conn.cursor()
    saved = 0

    for trace in recent:
        trace_id = trace["id"]

        # Fetch full trace with observations (small delay to avoid rate limiting)
        time.sleep(0.5)
        detail = _get(f"/api/public/traces/{trace_id}")

        for obs in detail.get("observations", []):
            if obs.get("type") != "GENERATION":
                continue

            # Latency from timestamps
            start = datetime.datetime.fromisoformat(obs["startTime"].replace("Z", "+00:00"))
            end   = datetime.datetime.fromisoformat(obs["endTime"].replace("Z",   "+00:00"))
            latency_ms = int((end - start).total_seconds() * 1000)

            usage = obs.get("usage", {})

            # Input: list of message dicts → readable string
            raw_input = obs.get("input", [])
            if isinstance(raw_input, list):
                parts = [f"[{m.get('role','').upper()}]\n{m.get('content','')}" for m in raw_input]
                input_text = "\n\n".join(parts)
            else:
                input_text = str(raw_input)

            # Output: dict with role+content, or plain string
            raw_output = obs.get("output", "")
            if isinstance(raw_output, dict):
                output_text = raw_output.get("content", str(raw_output))
            else:
                output_text = str(raw_output)

            cost = obs.get("calculatedTotalCost") or 0

            cur.execute("""
                INSERT INTO langfuse_traces
                  (evaluation_id, trace_id, span_name, model,
                   input_tokens, output_tokens, total_tokens,
                   latency_ms, cost_usd, input_text, output_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trace_id, span_name) DO UPDATE SET
                  evaluation_id  = EXCLUDED.evaluation_id,
                  input_tokens   = EXCLUDED.input_tokens,
                  output_tokens  = EXCLUDED.output_tokens,
                  total_tokens   = EXCLUDED.total_tokens,
                  latency_ms     = EXCLUDED.latency_ms,
                  cost_usd       = EXCLUDED.cost_usd,
                  input_text     = EXCLUDED.input_text,
                  output_text    = EXCLUDED.output_text
            """, (
                evaluation_id,
                trace_id,
                obs.get("name", ""),
                obs.get("model", ""),
                usage.get("input", 0),
                usage.get("output", 0),
                usage.get("total", 0),
                latency_ms,
                cost,
                input_text,
                output_text,
            ))
            saved += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"  [Langfuse→DB] Synced {saved} generation(s) for eval id={evaluation_id}")
