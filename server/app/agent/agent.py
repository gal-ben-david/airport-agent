"""
The airport investment agent: Claude with native tool use over the data tools.

run_agent(messages) takes the chat history and returns the answer plus every tool
call made for the latest user message. The LLM decides which tools to call and
writes the answer; all numbers come from the deterministic tools (CLAUDE.md).
A grounding guardrail checks every number in the final answer against the tool
results and asks the model for one correction if any is missing (D28).
"""

import json
import os

import anthropic
from dotenv import load_dotenv

from app.core.scoring import REPO_ROOT

from .grounding import ungrounded_numbers
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"        # thinking depth; medium suits multi-step tool use
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 6       # per user message

load_dotenv(REPO_ROOT / ".env")

REFUSAL_ANSWER = (
    "I can't help with that request. I can answer questions about US airports as "
    "terminal expansion candidates: growth, seat demand, congestion, rankings and "
    "route mix."
)
TRUNCATED_NOTE = "\n\n[The answer was cut off because it reached the length limit.]"
BUDGET_ERROR = (
    f"Tool budget reached ({MAX_TOOL_CALLS} calls per question). Answer with the "
    "results you already have, and say what you could not check."
)
# Sent once when the final answer has numbers not found in this turn's tool results (D28).
GROUNDING_CORRECTION = (
    "Your answer contains numbers that do not appear in the tool results for this "
    "question: {numbers}. Rewrite the complete answer using only values from the tool "
    "results, quoting the *_display values exactly as given. For each listed number, "
    "replace it with the exact tool value, call a tool to get it, or remove it. Do not "
    "round, convert or compute numbers. Do not mention this correction."
)

# ---------------------------------------------------------------------------
# Tool schemas: what the model sees. Descriptions say when to use each tool.
# Value checks (ranges, known codes) are done by the tools themselves.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "find_airport",
        "description": (
            "Resolve an airport code, city or airport name to an FAA Primary airport. "
            "Use this first whenever the user names an airport or place in words "
            "(e.g. 'LA', 'Santa Ana', 'Bradley'). Returns the match with match_type and "
            "a note when the mapping is an assumption, or several candidates when "
            "ambiguous. Not for regions: use rank_airports for those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Airport code, city or name."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_airport_profile",
        "description": (
            "Full profile of one airport: enplanements 2019/2024/2025, growth, load factor, "
            "NAS delay rate, context metrics (delay and cancel rate, long-haul and cargo "
            "share), investment score with its 4 component percentiles (higher = stronger "
            "signal, e.g. more congested), rank within its "
            "hub group, weight sensitivity, data completeness and confidence, and flags. "
            "Use for questions about one airport's metrics, score or congestion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "FAA or IATA code, e.g. 'BOS'."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "rank_airports",
        "description": (
            "Top ranked airports by investment score, grouped by FAA hub size, with each "
            "airport's national rank within its hub group, its 4 component percentiles and "
            "the metrics behind them. Use for 'best candidates' and "
            "regional questions. Pass states as the region name 'New England' (the only "
            "predefined region) or as a list of two-letter state codes for any other "
            "region (e.g. ['CA', 'OR', 'WA']); omit for the whole US. Also lists unranked "
            "airports in the region."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "states": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "'New England', a state code, or a list of state codes.",
                },
                "hub": {
                    "type": "string",
                    "enum": ["L", "M", "S", "N"],
                    "description": "Only this hub group: L large, M medium, S small, N nonhub.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Airports per hub group, 1-50 (default 5).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "compare_airports",
        "description": (
            "Side-by-side profiles of 2-5 airports (same fields as get_airport_profile). "
            "Use when the user compares airports. The result says whether they are in the "
            "same hub group; scores of different groups are not comparable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-5 FAA or IATA codes, e.g. ['LAX', 'SNA'].",
                },
            },
            "required": ["codes"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "long_haul_share",
        "description": (
            "Share of an airport's departing flights that are long haul (segment distance "
            ">= threshold miles), by flights and by passengers on scheduled passenger "
            "flights, plus the all-flights share including cargo and the cargo share as "
            "context. Also returns long-haul departure counts by class group (scheduled "
            "passenger, charter, cargo) and a table of the shares at 2,500 / 3,000 / 4,000 "
            "miles. Default threshold 3,000 miles; other thresholds "
            "(100-10,000) are allowed. Use for long-haul or route-mix questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "FAA or IATA code."},
                "threshold_miles": {
                    "type": "number",
                    "description": "Long-haul threshold in miles (default 3000).",
                },
                "include_cargo": {
                    "type": "boolean",
                    "description": "Headline share includes cargo flights (default false).",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_recent_traffic",
        "description": (
            "Live recent activity from the OpenSky Network API (ADS-B tracking): arrivals, "
            "departures and airline departures over the last N full UTC days, with daily "
            "averages, compared with the 2025 T-100 average departures per day. Returns the "
            "exact time window, cache status (live, cached or stale) and caveats. Context "
            "only, not part of the investment score. Use for questions about recent or "
            "current activity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "FAA or IATA code."},
                "days": {
                    "type": "integer",
                    "description": "Number of full past days, 1-7 (default 7).",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).")
        _client = anthropic.Anthropic()
    return _client


def _execute(name: str, args: dict) -> dict:
    """Run one tool. Any failure becomes an ok=false result, never an exception."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool '{name}'."}
    try:
        return fn(**args)
    except TypeError as e:  # wrong or missing arguments
        return {"ok": False, "error": f"Invalid arguments for {name}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{name} failed: {type(e).__name__}: {e}"}


def run_agent(messages: list[dict]) -> dict:
    """Answer the latest user message, using the conversation history.

    messages: [{"role": "user" | "assistant", "content": str}, ...], ending with
              the user message to answer.
    Returns {"answer": str,
             "tool_calls": [{"name", "input", "result", "is_error", "executed"}, ...],
             "grounding": {"verified", "ungrounded", "retried", "ungrounded_before_retry"}}
    for the latest user message.
    """
    client = _get_client()
    # Earlier turns are passed as text only; follow-ups re-fetch numbers via tools.
    convo = [{"role": m["role"], "content": m["content"]} for m in messages]
    questions = [m["content"] for m in messages
                 if m["role"] == "user" and isinstance(m["content"], str)]
    tool_calls: list[dict] = []
    executed = 0
    first_ungrounded: list[str] | None = None  # set when the correction retry was used

    def finish(answer: str) -> dict:
        # Grounded against this turn's tool calls only: earlier tool results are
        # not carried forward (D24), so follow-ups must re-fetch their numbers.
        ungrounded = ungrounded_numbers(answer, tool_calls, questions)
        return {
            "answer": answer,
            "tool_calls": tool_calls,
            "grounding": {
                "verified": not ungrounded,
                "ungrounded": ungrounded,
                "retried": first_ungrounded is not None,
                "ungrounded_before_retry": first_ungrounded or [],
            },
        }

    while True:
        budget_reached = executed >= MAX_TOOL_CALLS
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            # Once the budget is used up, the model must answer without more tools.
            tool_choice={"type": "none"} if budget_reached else {"type": "auto"},
            output_config={"effort": EFFORT},
            messages=convo,
        )

        if response.stop_reason == "refusal":
            return finish(REFUSAL_ANSWER)

        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                answer += TRUNCATED_NOTE
            ungrounded = ungrounded_numbers(answer, tool_calls, questions)
            if not ungrounded or first_ungrounded is not None:
                return finish(answer)
            # One correction retry. The loop continues, so the model can also call a
            # tool for a missing number (within the tool budget).
            first_ungrounded = ungrounded
            convo.append({"role": "assistant", "content": response.content})
            convo.append({"role": "user",
                          "content": GROUNDING_CORRECTION.format(numbers=", ".join(ungrounded))})
            continue

        # Keep the full assistant turn (thinking and tool_use blocks) unchanged.
        convo.append({"role": "assistant", "content": response.content})

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if executed < MAX_TOOL_CALLS:
                result = _execute(block.name, block.input)
                executed += 1
                ran = True
            else:
                result = {"ok": False, "error": BUDGET_ERROR}
                ran = False
            is_error = not result.get("ok", False)
            tool_calls.append({
                "name": block.name,
                "input": block.input,
                "result": result,
                "is_error": is_error,
                "executed": ran,
            })
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
                "is_error": is_error,
            })

        # All results for this step go back in one message.
        convo.append({"role": "user", "content": results})
