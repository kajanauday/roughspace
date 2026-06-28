"""
orchestrator.py — Coordinates the full Travel Planner pipeline.

Public API:
    run_planner_agents()                  -> dict   (trip_data, plan, scores)
    insert_current_run_metrics(output)    -> dict   (saved DB record)

Usage:
    python orchestrator.py

Or from another module:
    from orchestrator import run_planner_agents, insert_current_run_metrics
    result = run_planner_agents()
    insert_current_run_metrics(result)
"""
from langfuse import observe

from agents import (
    agent1_collect_inputs,
    agent2_create_plan,
    get_human_feedback,
    judge_evaluate,
    langfuse,
)
from db import init_db, save_to_db
from grafana import push_records


# ── Private helpers ───────────────────────────────────────────────────────────

def _print_evaluation(scores: dict) -> None:
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


# ── Public API ────────────────────────────────────────────────────────────────

@observe(name="travel-planner-run")
def run_planner_agents() -> dict:
    """
    Run the full agentic pipeline (Agent-1 → Agent-2 → Human feedback → Judge).

    Returns:
        {
            "trip_data": {...},   # validated trip requirements
            "plan":      "...",   # full itinerary text
            "scores":    {...},   # 6 evaluation metrics (0-100 each)
        }

    All LLM calls are traced in Langfuse under a single 'travel-planner-run' trace,
    capturing tokens, cost, and latency per agent span.
    """
    print("\n" + "=" * 60)
    print("    MULTI-AGENT TRAVEL PLANNER")
    print("=" * 60)

    trip_data      = agent1_collect_inputs()
    plan           = agent2_create_plan(trip_data)
    human_feedback = get_human_feedback()
    scores         = judge_evaluate(trip_data, plan, human_feedback)

    _print_evaluation(scores)

    return {"trip_data": trip_data, "plan": plan, "scores": scores}


def insert_current_run_metrics(prev_step_output: dict) -> dict:
    """
    Persist metrics from run_planner_agents() to PostgreSQL and push to Azure Monitor.

    Args:
        prev_step_output: dict returned by run_planner_agents()

    Returns:
        The saved DB record dict (includes the new row id).
    """
    record = save_to_db(prev_step_output["trip_data"], prev_step_output["scores"])
    push_records([record])
    return record


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    init_db()
    result = run_planner_agents()
    insert_current_run_metrics(result)
    langfuse.flush()


if __name__ == "__main__":
    main()
