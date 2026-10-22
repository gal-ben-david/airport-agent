"""
Run the evaluation cases through the agent, print a results table and save it
to docs/eval-results.md.

Each case passes if the expected tools were called, all its checks pass and every
number in the answer is grounded in tool output (grounding.py). No LLM judge.

Needs ANTHROPIC_API_KEY (and OpenSky credentials for the live case) in .env.
Run from server/:
    python -m evals.run_evals                         # all cases
    python -m evals.run_evals new_england hvn_flags   # only these (plus any case they follow)
"""

import sys
import time
from collections import Counter
from contextlib import nullcontext
from datetime import datetime

from app.agent.agent import MODEL, run_agent
from app.agent.grounding import answer_numbers, ungrounded_numbers
from app.core.scoring import REPO_ROOT

from .cases import CASES, Case, Check, offending_superlatives

RESULTS_MD = REPO_ROOT / "docs" / "eval-results.md"


def _expected_tool_checks(case: Case) -> list[Check]:
    checks = []
    for expected in case.expected_tools:
        names = expected if isinstance(expected, tuple) else (expected,)
        checks.append(Check(
            f"calls {' or '.join(names)}",
            lambda r, names=names: any(c["name"] in names for c in r["tool_calls"]),
        ))
    return checks


def _select(ids: list[str]) -> list[Case]:
    """Requested cases plus the cases they follow, in CASES order."""
    by_id = {c.id: c for c in CASES}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        sys.exit(f"Unknown case id(s): {', '.join(unknown)}. Known: {', '.join(by_id)}")
    wanted = set(ids or by_id)
    for case_id in list(wanted):
        while by_id[case_id].after:
            case_id = by_id[case_id].after
            wanted.add(case_id)
    return [c for c in CASES if c.id in wanted]


def run_case(case: Case, parent: dict | None) -> dict:
    """Ask the case question (after the parent conversation and setup turns, if any)
    and evaluate the final answer. The case fixture, if any, wraps all turns."""
    history = list(parent["messages"]) if parent else []
    prior_calls = list(parent["all_tool_calls"]) if parent else []

    started = time.time()
    try:
        with case.fixture() if case.fixture else nullcontext():
            for turn in case.setup:
                history.append({"role": "user", "content": turn})
                setup_result = run_agent(history)
                history.append({"role": "assistant", "content": setup_result["answer"]})
                prior_calls += setup_result["tool_calls"]
            history.append({"role": "user", "content": case.question})
            result = run_agent(history)
    except Exception as e:  # API or config error: report it, keep going
        return {"id": case.id, "question": case.question, "error": f"{type(e).__name__}: {e}",
                "passed": False, "failed": ["run error"], "soft_failed": [], "tool_calls": [],
                "answer": "", "ungrounded": [], "n_numbers": 0, "guardrail": None,
                "seconds": time.time() - started, "messages": history, "all_tool_calls": prior_calls}

    # Numbers may come from any tool result in the conversation; earlier answers
    # were grounded in the earlier turns' tool results.
    all_calls = prior_calls + result["tool_calls"]
    questions = [m["content"] for m in history if m["role"] == "user"]
    ungrounded = ungrounded_numbers(result["answer"], all_calls, questions)

    checks = _expected_tool_checks(case) + case.checks
    failed = [c.name for c in checks if c.required and not c.fn(result)]
    soft_failed = [c.name for c in checks if not c.required and not c.fn(result)]
    if ungrounded:
        failed.append("grounding")

    return {
        "id": case.id,
        "question": case.question,
        "after": case.after,
        "error": None,
        "answer": result["answer"],
        "tool_calls": result["tool_calls"],
        "passed": not failed,
        "failed": failed,
        "soft_failed": soft_failed,
        "superlatives": offending_superlatives(result["answer"]),
        "ungrounded": ungrounded,
        "n_numbers": len(answer_numbers(result["answer"])),
        "guardrail": result["grounding"],  # run_agent's own check and retry (D28)
        "seconds": time.time() - started,
        "messages": history + [{"role": "assistant", "content": result["answer"]}],
        "all_tool_calls": all_calls,
    }


def _tools_used(r: dict) -> str:
    counts = Counter(c["name"] for c in r["tool_calls"])
    return ", ".join(f"{n} x{k}" if k > 1 else n for n, k in counts.items()) or "none"


def _failed_text(r: dict) -> str:
    """Failed checks, then soft checks that failed (shown, but the case can still pass)."""
    return "; ".join(r["failed"] + r.get("soft_failed", []))


def _retry_text(r: dict) -> str:
    """What the runtime guardrail did: '–', or the numbers that triggered a retry."""
    g = r["guardrail"]
    if not g or not g["retried"]:
        return "–"
    outcome = "fixed" if g["verified"] else f"still ungrounded: {', '.join(g['ungrounded'])}"
    return f"retried ({', '.join(g['ungrounded_before_retry'])}) -> {outcome}"


def _summary(results: list[dict]) -> dict:
    n = len(results)
    answered = [r for r in results if not r["error"]]
    return {
        "cases_passed": sum(r["passed"] for r in results),
        "cases": n,
        "grounded": sum(not r["ungrounded"] for r in answered),
        "answered": len(answered),
        "retries": sum(bool(r["guardrail"] and r["guardrail"]["retried"]) for r in answered),
    }


def _pct(a: int, b: int) -> str:
    return f"{a / b:.0%}" if b else "n/a"


def print_report(results: list[dict]) -> None:
    w = max(len(r["id"]) for r in results)
    print(f"\n{'case':<{w}}  result  {'tool calls':<45}  failed checks")
    print("-" * (w + 100))
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"{r['id']:<{w}}  {status:<6}  {_tools_used(r):<45}  {_failed_text(r)}")

    print("\nGrounding (numbers in the answer not found in tool results):")
    for r in results:
        if r["error"]:
            print(f"  {r['id']:<{w}}  run error: {r['error']}")
        else:
            status = (f"UNGROUNDED: {', '.join(r['ungrounded'])}  (of {r['n_numbers']} numbers)"
                      if r["ungrounded"] else f"ok ({r['n_numbers']} numbers)")
            print(f"  {r['id']:<{w}}  {status:<40}  guardrail: {_retry_text(r)}")

    s = _summary(results)
    print(f"\nCases passed: {s['cases_passed']}/{s['cases']} ({_pct(s['cases_passed'], s['cases'])})")
    print(f"Fully grounded answers: {s['grounded']}/{s['answered']} ({_pct(s['grounded'], s['answered'])})")
    print(f"Guardrail retries: {s['retries']}/{s['answered']}")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_markdown(results: list[dict]) -> None:
    s = _summary(results)
    lines = [
        "# Evaluation results",
        "",
        f"Run: {datetime.now():%Y-%m-%d %H:%M} · model `{MODEL}` · "
        "generated by `server/evals/run_evals.py` (cases in `server/evals/cases.py`).",
        "",
        f"**Cases passed: {s['cases_passed']}/{s['cases']} ({_pct(s['cases_passed'], s['cases'])})** · "
        f"Fully grounded answers: {s['grounded']}/{s['answered']} ({_pct(s['grounded'], s['answered'])}) · "
        f"Guardrail retries: {s['retries']}/{s['answered']}",
        "",
        "A case passes when the expected tools were called, every check passes and every "
        "number in the answer is grounded in tool output. All checks are deterministic "
        "(string, regex and tool-call checks); there is no LLM judge. LLM answers vary "
        "between runs, so a single run is a sample, not a guarantee.",
        "",
        "## Results",
        "",
        "| Case | Result | Failed checks | Tool calls |",
        "|---|---|---|---|",
    ]
    for r in results:
        case = r["id"] + (f" (after {r['after']})" if r.get("after") else "")
        lines.append(f"| {case} | {'PASS' if r['passed'] else '**FAIL**'} | "
                     f"{_cell(_failed_text(r)) or '–'} | {_cell(_tools_used(r))} |")

    lines += [
        "",
        "## Grounding",
        "",
        "Every number in each answer is looked up in the tool inputs and results of the "
        "conversation (raw or display value). Years, ranks (\"9th of 30\"), numbers from "
        "the question and methodology constants from the system prompt are not checked "
        "(see `server/app/agent/grounding.py`). \"Guardrail\" shows when run_agent's runtime "
        "check found ungrounded numbers in the first draft and asked for one correction (D28).",
        "",
        "| Case | Numbers checked | Ungrounded (final answer) | Guardrail |",
        "|---|---|---|---|",
    ]
    for r in results:
        detail = f"run error: {r['error']}" if r["error"] else (", ".join(r["ungrounded"]) or "–")
        lines.append(f"| {r['id']} | {r['n_numbers']} | {_cell(detail)} | {_cell(_retry_text(r))} |")

    lines += ["", "## Answers", ""]
    for r in results:
        lines += [
            f"<details><summary><b>{r['id']}</b>: {'PASS' if r['passed'] else 'FAIL'} — "
            f"{_cell(r['question'])}</summary>",
            "",
            f"Tools: {_tools_used(r)} · {r['seconds']:.0f}s",
            "",
        ]
        if r.get("superlatives"):
            lines += [f"Cross-group superlatives found: {', '.join(map(repr, r['superlatives']))}", ""]
        lines += [r["error"] or r["answer"], "", "</details>", ""]

    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")


def main(ids: list[str]) -> None:
    cases = _select(ids)
    print(f"Model: {MODEL} · {len(cases)} cases")
    done: dict[str, dict] = {}
    for case in cases:
        print(f"  running {case.id} ...", flush=True)
        done[case.id] = run_case(case, done.get(case.after) if case.after else None)
    results = list(done.values())
    print_report(results)
    write_markdown(results)
    print(f"\nSaved to {RESULTS_MD.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
