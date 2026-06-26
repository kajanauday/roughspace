"""
grafana.py — Push evaluation records to Azure Monitor Log Analytics
             (HTTP Data Collector API).

Required env vars (or override the defaults below):
  AZURE_LOG_ANALYTICS_WORKSPACE_ID
  AZURE_LOG_ANALYTICS_WORKSPACE_KEY
"""
import base64
import hashlib
import hmac
import json
import os
import requests
from datetime import datetime, timezone

from dotenv import load_dotenv

from db import get_db

load_dotenv()

WORKSPACE_ID  = os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID")
WORKSPACE_KEY = os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_KEY")
LOG_TYPE      = "TravelPlannerEval"


def _build_signature(date: str, content_length: int) -> str:
    string_to_hash = (
        f"POST\n{content_length}\napplication/json\n"
        f"x-ms-date:{date}\n/api/logs"
    )
    decoded_key  = base64.b64decode(WORKSPACE_KEY)
    encoded_hash = base64.b64encode(
        hmac.new(decoded_key, string_to_hash.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return f"SharedKey {WORKSPACE_ID}:{encoded_hash}"


def push_records(records: list[dict]) -> None:
    """Push a list of evaluation records to Azure Monitor Log Analytics."""
    if not records:
        print("  [Grafana] No records to push.")
        return

    body       = json.dumps(records)
    body_bytes = body.encode("utf-8")
    date       = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    headers = {
        "Content-Type":         "application/json",
        "Authorization":        _build_signature(date, len(body_bytes)),
        "Log-Type":             LOG_TYPE,
        "x-ms-date":            date,
        "time-generated-field": "created_at",
    }
    url  = f"https://{WORKSPACE_ID}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01"
    resp = requests.post(url, data=body_bytes, headers=headers)

    if resp.status_code == 200:
        print(f"  [Grafana] Pushed {len(records)} record(s) → Azure Monitor ({LOG_TYPE}_CL)")
    else:
        print(f"  [Grafana] HTTP {resp.status_code}: {resp.text}")


def fetch_all_records() -> list[dict]:
    """Fetch all evaluation records from PostgreSQL for a bulk push."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, destination, start_date, end_date, budget, currency,
               num_people, journey_type, confidence_score, accuracy,
               precision_score, recall, hallucination, human_feedback,
               verdict, created_at
        FROM evaluations
        ORDER BY created_at DESC
    """)
    cols = [desc[0] for desc in cur.description]
    rows = []
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        for k, v in record.items():
            if hasattr(v, "isoformat"):
                record[k] = v.isoformat()
        rows.append(record)
    cur.close()
    conn.close()
    return rows


if __name__ == "__main__":
    records = fetch_all_records()
    print(f"  Fetched {len(records)} record(s) from PostgreSQL.")
    push_records(records)
