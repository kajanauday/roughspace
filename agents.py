"""
agents.py — All LLM agents for the Travel Planner.

Langfuse (langfuse.openai drop-in) auto-captures every call:
  - prompt / completion tokens
  - total cost (configure model pricing in Langfuse dashboard → Settings → Models)
  - latency, model name, input/output content

Required env vars:
  LANGFUSE_PUBLIC_KEY   — from your Langfuse project settings
  LANGFUSE_SECRET_KEY   — from your Langfuse project settings
  LANGFUSE_HOST         — defaults to https://cloud.langfuse.com

Optional (override hardcoded defaults):
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_ENDPOINT
"""
import json
import os
import re
import time

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()
from langfuse import observe
from langfuse.openai import AzureOpenAI

# ── Azure OpenAI config ───────────────────────────────────────────────────────

AZURE_API_KEY     = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
AZURE_DEPLOYMENT  = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

# ── Langfuse client — reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from env ─

langfuse = Langfuse()

# ── OpenAI client — drop-in replacement; every create() call is auto-traced ──

client = AzureOpenAI(
    api_key=AZURE_API_KEY,
    azure_endpoint=AZURE_ENDPOINT,
    api_version=AZURE_API_VERSION,
)

# ── System prompts ────────────────────────────────────────────────────────────

AGENT1_SYSTEM = """You are a travel planning intake agent. Gather these details through friendly conversation:
  1. Destination / place to visit
  2. Travel dates (start date, end date)
  3. Total budget and currency
  4. Number of people
  5. Journey type (adventure / leisure / family / honeymoon / business / pilgrimage …)
  6. Any special requirements or preferences

Rules:
- Ask questions naturally; group related ones together.
- Validate feasibility as you go (dates must be in the future, budget realistic for the destination, etc.).
- If anything seems infeasible, politely flag it and ask the user to update it.
- Keep asking until ALL six fields are confirmed and feasible.

When all details are complete and feasible, append this block at the end of your final message:
```json
{
  "complete": true,
  "destination": "",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "budget": 0,
  "currency": "USD",
  "num_people": 1,
  "journey_type": "",
  "special_requirements": ""
}
```
Until then keep "complete": false (or omit the block entirely)."""

AGENT2_SYSTEM = """You are an expert travel planner. Given structured trip requirements, produce a detailed,
day-by-day itinerary.

For EACH day include:
  • Morning / Afternoon / Evening segments with approximate times
  • Specific attractions / activities with brief why-visit notes
  • Recommended meals (restaurant name or food type, estimated cost per person)
  • Transport between locations (bus / car / bike / train / flight — choose what fits the budget)
  • Daily cost estimate (per person and total for the group)

End with a BUDGET SUMMARY table showing total estimated spend vs the stated budget.
Be practical, specific, and ensure the plan fits within the total budget for all people."""

JUDGE_SYSTEM = """You are an AI output evaluator for travel plans. Given the trip requirements, the generated plan,
and human feedback, return ONLY a JSON object — no prose:

{
  "confidence_score": 0-100,
  "accuracy":         0-100,
  "precision":        0-100,
  "recall":           0-100,
  "hallucination":    0-100,
  "human_feedback":   0-100,
  "summary":          "one-sentence verdict"
}

Scoring guide:
  confidence_score  — overall confidence the plan satisfies all requirements
  accuracy          — how well dates / destination / budget are reflected
  precision         — specificity and actionability of the recommendations
  recall            — fraction of stated requirements covered in the plan
  hallucination     — 100 = fully grounded, 0 = many invented/wrong facts
  human_feedback    — convert the user's 1-10 rating to 0-100 (rating × 10)"""


# ── GPT-4o pricing (Azure, per token) ────────────────────────────────────────
_COST_INPUT  = 2.50  / 1_000_000   # $ per input token
_COST_OUTPUT = 10.00 / 1_000_000   # $ per output token

def _build_metrics(agent_name: str, usage, latency_ms: int,
                   input_messages: list = None, output_text: str = "") -> dict:
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    cost = round(pt * _COST_INPUT + ct * _COST_OUTPUT, 8)
    # Serialise the messages list to a readable string
    input_text = ""
    if input_messages:
        parts = []
        for m in input_messages:
            role = m.get("role", "")
            content = m.get("content", "")
            parts.append(f"[{role.upper()}]\n{content}")
        input_text = "\n\n".join(parts)
    return {
        "agent_name":         agent_name,
        "model":              AZURE_DEPLOYMENT,
        "prompt_tokens":      pt,
        "completion_tokens":  ct,
        "total_tokens":       pt + ct,
        "latency_ms":         latency_ms,
        "cost_usd":           cost,
        "input_text":         input_text,
        "output_text":        output_text,
    }


# ── Agents ────────────────────────────────────────────────────────────────────

@observe(name="agent1-collect-inputs")
def agent1_collect_inputs() -> dict:
    """Interactively gathers trip requirements; returns validated trip dict."""
    print("\n" + "=" * 60)
    print("  AGENT-1 — Trip Requirements Collector")
    print("=" * 60 + "\n")

    history = [{"role": "user", "content": "Hi, I want to plan a trip."}]

    agent_msg = client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=[{"role": "system", "content": AGENT1_SYSTEM}] + history,
        name="agent1-init",
    ).choices[0].message.content
    history.append({"role": "assistant", "content": agent_msg})
    print(f"Agent-1: {agent_msg}\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue

        history.append({"role": "user", "content": user_input})
        agent_msg = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[{"role": "system", "content": AGENT1_SYSTEM}] + history,
            name="agent1-response",
        ).choices[0].message.content
        history.append({"role": "assistant", "content": agent_msg})
        print(f"\nAgent-1: {agent_msg}\n")

        if '"complete": true' in agent_msg:
            match = re.search(r'```json\s*(\{.*?\})\s*```', agent_msg, re.DOTALL)
            if match:
                return json.loads(match.group(1))


@observe(name="agent2-create-plan")
def agent2_create_plan(trip_data: dict):
    """Generates a detailed day-by-day travel itinerary. Returns (plan, metrics)."""
    prompt = f"Create a detailed travel plan for this trip:\n{json.dumps(trip_data, indent=2)}"
    messages = [
        {"role": "system", "content": AGENT2_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    t0 = time.time()
    resp = client.chat.completions.create(
        model=AZURE_DEPLOYMENT, max_tokens=4096, messages=messages, name="agent2-generate-plan",
    )
    latency_ms = int((time.time() - t0) * 1000)
    plan = resp.choices[0].message.content
    metrics = _build_metrics("agent2", resp.usage, latency_ms, messages, plan)
    return plan, metrics


def get_human_feedback() -> dict:
    """Collects a 1-10 rating and comments from the user (no LLM call)."""
    print("\n" + "=" * 60)
    print("  HUMAN FEEDBACK")
    print("=" * 60)
    print("Please rate the travel plan above.\n")

    while True:
        try:
            rating = int(input("Rating (1-10): ").strip())
            if 1 <= rating <= 10:
                break
            print("Enter a number between 1 and 10.")
        except ValueError:
            print("Enter a valid number.")

    comments = input("Comments     : ").strip()
    return {"rating": rating, "score": rating * 10, "comments": comments}


@observe(name="judge-evaluate")
def judge_evaluate(trip_data: dict, plan: str, feedback: dict):
    """Scores the travel plan on 6 metrics. Returns (scores, metrics)."""
    user_prompt = (
        f"Trip Requirements:\n{json.dumps(trip_data, indent=2)}\n\n"
        f"Generated Plan:\n{plan}\n\n"
        f"Human Feedback:\nRating: {feedback['rating']}/10\nComments: {feedback['comments']}"
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user",   "content": user_prompt},
    ]
    t0 = time.time()
    resp = client.chat.completions.create(
        model=AZURE_DEPLOYMENT, max_tokens=512, messages=messages, name="judge-score",
    )
    latency_ms = int((time.time() - t0) * 1000)
    raw = resp.choices[0].message.content
    metrics = _build_metrics("judge", resp.usage, latency_ms, messages, raw)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    scores = json.loads(match.group()) if match else {}
    scores["human_feedback"] = feedback["score"]
    return scores, metrics
