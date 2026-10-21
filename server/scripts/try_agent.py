"""
Run the assignment questions (and a follow-up) through the agent and print the
answers with the tools called.

Needs ANTHROPIC_API_KEY in .env. Run from the repo root:
    python server/scripts/try_agent.py
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


print(f"Model: {MODEL}")
for q in ASSIGNMENT_QUESTIONS:
    result = run_agent([{"role": "user", "content": q["text"]}])
    show(q["text"], result)

    for f in (f for f in FOLLOW_UPS if f["after"] == q["id"]):
        history = [
            {"role": "user", "content": q["text"]},
            {"role": "assistant", "content": result["answer"]},
            {"role": "user", "content": f["text"]},
        ]
        show(f"{f['text']}  (follow-up to: {q['text']})", run_agent(history))
