"""
run_pipeline.py — Non-interactive end-to-end pipeline run.

Skips the interactive Agent-1 conversation and injects a sample trip,
then runs Agent-2 (plan generation), Judge (scoring), DB insert, and
Azure Monitor push — all traced in Langfuse.

Usage:
    python run_pipeline.py
"""
from dotenv import load_dotenv
load_dotenv()

from langfuse import observe
from agents import agent2_create_plan, judge_evaluate, langfuse
from db import init_db, save_to_db
from grafana import push_records

# ── Sample trip (replaces interactive Agent-1) ────────────────────────────────

SAMPLE_TRIP = {
    "complete": True,
    "destination": "Goa, India",
    "start_date": "2026-08-01",
    "end_date": "2026-08-06",
    "budget": 50000,
    "currency": "INR",
    "num_people": 2,
    "journey_type": "leisure",
    "special_requirements": "Beach-side stay, seafood lover, avoid crowded tourist spots",
}

SAMPLE_FEEDBACK = {
    "rating": 8,
    "score": 80,
    "comments": "Great itinerary overall, very well structured.",
}


@observe(name="travel-planner-run")
def run():
    print("\n" + "=" * 60)
    print("    MULTI-AGENT TRAVEL PLANNER  (non-interactive run)")
    print("=" * 60)

    print("\n[Agent-1] Using sample trip data:")
    for k, v in SAMPLE_TRIP.items():
        if k != "complete":
            print(f"  {k}: {v}")

    plan   = agent2_create_plan(SAMPLE_TRIP)
    scores = judge_evaluate(SAMPLE_TRIP, plan, SAMPLE_FEEDBACK)

    print("\n" + "=" * 60)
    print("  EVALUATION RESULTS")
    print("=" * 60)
    metrics = [
        ("Confidence Score", "confidence_score"),
        ("Accuracy",         "accuracy"),
        ("Precision",        "precision"),
        ("Recall",           "recall"),
        ("Hallucination",    "hallucination"),
        ("Human Feedback",   "human_feedback"),
    ]
    for label, key in metrics:
        val = scores.get(key, "N/A")
        bar = "█" * (val // 10) if isinstance(val, int) else ""
        print(f"  {label:<20} {str(val):>6}/100  {bar}")
    if "summary" in scores:
        print(f"\n  Verdict: {scores['summary']}")
    print("=" * 60 + "\n")

    return {"trip_data": SAMPLE_TRIP, "plan": plan, "scores": scores}


def main():
    init_db()
    result = run()

    record = save_to_db(result["trip_data"], result["scores"])
    push_records([record])
    langfuse.flush()

    print("\n✓ Done — record saved to DB and pushed to Azure Monitor.")
    print(f"  DB row id : {record['id']}")
    print(f"  Langfuse  : https://cloud.langfuse.com")


if __name__ == "__main__":
    main()
