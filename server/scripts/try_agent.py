"""
Run the assignment questions (and a follow-up) through the agent and print the
answers with the tools called.

Needs ANTHROPIC_API_KEY in .env. Run from the repo root:
    python server/scripts/try_agent.py                    # all questions and follow-ups
    python server/scripts/try_agent.py new_england sfo_unmet_demand   # only these ids
"""

import json
import sys
from pathlib import Path

# Make `app` importable: this file is <repo>/server/scripts/try_agent.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.agent import MODEL, run_agent  # noqa: E402
from app.agent.questions import ASSIGNMENT_QUESTIONS, FOLLOW_UPS  # noqa: E402


def show(question: str, result: dict) -> None:
    print(f"\n{'=' * 80}\nQ: {question}\n{'=' * 80}")
    print(result["answer"])
    print(f"\n--- Tools called ({len(result['tool_calls'])}) ---")
    for i, call in enumerate(result["tool_calls"], 1):
        status = "skipped (budget)" if not call["executed"] else ("ERROR" if call["is_error"] else "ok")
        print(f"{i}. {call['name']}({json.dumps(call['input'], ensure_ascii=False)}) -> {status}")
        if call["is_error"]:
            print(f"   {call['result'].get('error')}")


selected = set(sys.argv[1:])  # question / follow-up ids; empty = all


def wanted(item_id: str) -> bool:
    return not selected or item_id in selected


print(f"Model: {MODEL}")
for q in ASSIGNMENT_QUESTIONS:
    follow_ups = [f for f in FOLLOW_UPS if f["after"] == q["id"] and wanted(f["id"])]
    if not wanted(q["id"]) and not follow_ups:
        continue
    result = run_agent([{"role": "user", "content": q["text"]}])
    if wanted(q["id"]):
        show(q["text"], result)

    for f in follow_ups:
        history = [
            {"role": "user", "content": q["text"]},
            {"role": "assistant", "content": result["answer"]},
            {"role": "user", "content": f["text"]},
        ]
        show(f"{f['text']}  (follow-up to: {q['text']})", run_agent(history))
