"""
db.py — PostgreSQL persistence for Travel Planner.
Runs on GCP VM alongside PostgreSQL. Azure Managed Grafana connects to VM's public IP directly.
"""

import os
import random

import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_CONFIG = {
    "host":     os.getenv("PG_HOST",     "localhost"),
    "port":     int(os.getenv("PG_PORT", "5432")),
    "dbname":   os.getenv("PG_DBNAME",   "traveldb"),
    "user":     os.getenv("PG_USER",     "postgres"),
    "password": os.getenv("PG_PASSWORD", ""),
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS evaluations (
    id               SERIAL PRIMARY KEY,
    destination      TEXT,
    start_date       DATE,
    end_date         DATE,
    budget           NUMERIC,
    currency         TEXT,
    num_people       INTEGER,
    journey_type     TEXT,
    confidence_score INTEGER,
    accuracy         INTEGER,
    precision_score  INTEGER,
    recall           INTEGER,
    hallucination    INTEGER,
    human_feedback   INTEGER,
    verdict          TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
)
"""

INSERT_SQL = """
INSERT INTO evaluations
  (destination, start_date, end_date, budget, currency, num_people, journey_type,
   confidence_score, accuracy, precision_score, recall, hallucination, human_feedback, verdict)
VALUES
  (%(destination)s, %(start_date)s, %(end_date)s, %(budget)s, %(currency)s,
   %(num_people)s, %(journey_type)s, %(confidence_score)s, %(accuracy)s,
   %(precision_score)s, %(recall)s, %(hallucination)s, %(human_feedback)s, %(verdict)s)
RETURNING id
"""


def get_db():
    return psycopg2.connect(**PG_CONFIG)


def init_db():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    cur.close()
    conn.close()
    print("  [DB] Table ready.")


def save_to_db(trip_data: dict, scores: dict):
    row = {
        "destination":      trip_data.get("destination", ""),
        "start_date":       trip_data.get("start_date"),
        "end_date":         trip_data.get("end_date"),
        "budget":           trip_data.get("budget", 0),
        "currency":         trip_data.get("currency", "INR"),
        "num_people":       trip_data.get("num_people", 1),
        "journey_type":     trip_data.get("journey_type", ""),
        "confidence_score": scores.get("confidence_score", 0),
        "accuracy":         scores.get("accuracy", 0),
        "precision_score":  scores.get("precision", 0),
        "recall":           scores.get("recall", 0),
        "hallucination":    scores.get("hallucination", 0),
        "human_feedback":   scores.get("human_feedback", 0),
        "verdict":          scores.get("summary", ""),
    }
    conn   = get_db()
    cur    = conn.cursor()
    cur.execute(INSERT_SQL, row)
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    print(f"  [DB] Saved evaluation (id={row_id})")
    row["id"] = row_id
    return row


def seed_sample_data():
    """Insert 3 sample records for dashboard testing."""
    samples = [
        {
            "destination": "Paris, France", "start_date": "2026-07-10",
            "end_date": "2026-07-17", "budget": 3000, "currency": "USD",
            "num_people": 2, "journey_type": "leisure",
            "confidence_score": 85, "accuracy": 90, "precision_score": 85,
            "recall": 80, "hallucination": 80, "human_feedback": 80,
            "verdict": "The plan is solid but has minor areas needing improvement.",
        },
        {
            "destination": "Bali, Indonesia", "start_date": "2026-08-05",
            "end_date": "2026-08-12", "budget": 2000, "currency": "USD",
            "num_people": 4, "journey_type": "adventure",
            "confidence_score": random.randint(70, 95),
            "accuracy":         random.randint(70, 95),
            "precision_score":  random.randint(70, 95),
            "recall":           random.randint(65, 90),
            "hallucination":    random.randint(75, 95),
            "human_feedback":   random.randint(60, 100),
            "verdict": "Good coverage of activities; transport details could be more specific.",
        },
        {
            "destination": "Rajasthan, India", "start_date": "2026-09-01",
            "end_date": "2026-09-07", "budget": 1200, "currency": "USD",
            "num_people": 3, "journey_type": "family",
            "confidence_score": random.randint(70, 95),
            "accuracy":         random.randint(70, 95),
            "precision_score":  random.randint(70, 95),
            "recall":           random.randint(65, 90),
            "hallucination":    random.randint(75, 95),
            "human_feedback":   random.randint(60, 100),
            "verdict": "Itinerary well-matched to family needs; budget slightly underestimated.",
        },
    ]
    conn = get_db()
    cur  = conn.cursor()
    for s in samples:
        cur.execute(INSERT_SQL, s)
    conn.commit()
    cur.close()
    conn.close()
    print(f"  [DB] Seeded {len(samples)} sample records.")
