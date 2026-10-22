"""
Grounding check: every number in an answer must appear in the tool results.

The agent must quote numbers from tool output, never compute or recall them
(CLAUDE.md, prompt rule 1). This check extracts the numbers from an answer and
looks each one up in the given tool inputs and results, as a raw value or inside
a display string ("82.3%", "21,021,153", "8–10").

Used at runtime by run_agent (one correction retry, D28) and by the evals (D27).

Matching is on the numeric value, ignoring sign, thousands separators and "%":
"declined 0.3%" matches "-0.3%", and "11%" matches "11.0%". A reformatted number
("21.0M" for 21,021,153, or "2.77%" for 0.0277) does not match and is reported.

Not checked (not data):
  - years (1990-2039) and year ranges ("2024-25", "CAGR 19-25");
  - ranks: ordinals with an optional range and "of N" ("9th of 30", "23rd-42nd");
  - numbered-list markers ("1. ");
  - numbers that appear in the user's messages;
  - METHOD_CONSTANTS: methodology values stated in the system prompt.
"""

import json
import re
from decimal import Decimal, InvalidOperation

# Methodology values from the system prompt (weights, growth blend, neutral value,
# Primary threshold, number of airports, long-haul threshold, delay minutes,
# minimum components), which the agent may restate without a tool call.
METHOD_CONSTANTS = {"35", "25", "15", "70", "30", "0.5", "10000", "402", "3000", "3", "4"}

YEAR = re.compile(r"^(19[9]\d|20[0-3]\d)$")
YEAR_RANGE = re.compile(r"\b(19|20)\d{2}\s*[–—-]\s*\d{2}(\d{2})?\b(?![.,]?\d|%)")
# Short forms of the data years 2019, 2024, 2025 ("CAGR 19-25", "24-25"). Kept to
# these pairs so rank ranges like "15–25" are still checked.
SHORT_YEAR_RANGE = re.compile(r"\b(19|24)\s*[–—/-]\s*(24|25)\b(?![.,]?\d|%)")
RANK = re.compile(
    r"\b\d+(st|nd|rd|th)\b(\s*[–—-]\s*\d+(st|nd|rd|th)?\b)?(\s+of\s+\d[\d,]*)?",
    re.IGNORECASE,
)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)

# A number in prose: optional sign, digits with thousands commas, decimals, "%".
# Not preceded by a letter, digit, dot or hyphen, so "D15", "T-100", "CY2025"
# and the parts of "2026-10-14" are not read as numbers.
ANSWER_NUMBER = re.compile(r"(?<![\w.\-−/])[+\-−]?\d[\d,]*(?:\.\d+)?%?")
# Inside tool output any digit run counts (dates, "8–10", "3,000 miles").
TOOL_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def canonical(token: str) -> str | None:
    """'+1,234.50%' -> '1234.5'. None if not a number."""
    s = token.strip().rstrip(",").lstrip("+-−").rstrip("%").replace(",", "")
    try:
        return format(Decimal(s).normalize(), "f")
    except InvalidOperation:
        return None


def answer_numbers(text: str) -> list[str]:
    """Numbers in an answer or question, minus years, ranks and list markers."""
    text = YEAR_RANGE.sub(" ", text)
    text = SHORT_YEAR_RANGE.sub(" ", text)
    text = RANK.sub(" ", text)
    text = LIST_MARKER.sub(" ", text)
    found = []
    for m in ANSWER_NUMBER.finditer(text):
        token = m.group(0).rstrip(",")
        if YEAR.match(token):
            continue
        found.append(token)
    return found


def _walk(value, out: set[str]) -> None:
    """Collect every number in a JSON-like value, raw or inside strings."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        c = canonical(repr(abs(value)))
        if c is not None:
            out.add(c)
    elif isinstance(value, str):
        for m in TOOL_NUMBER.finditer(value):
            c = canonical(m.group(0))
            if c is not None:
                out.add(c)
    elif isinstance(value, dict):
        for v in value.values():
            _walk(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, out)


def tool_numbers(tool_calls: list[dict]) -> set[str]:
    pool: set[str] = set()
    for call in tool_calls:
        _walk(call["input"], pool)
        _walk(call["result"], pool)
    return pool


def ungrounded_numbers(answer: str, tool_calls: list[dict], questions: list[str]) -> list[str]:
    """Numbers in `answer` not found in the tool calls, the questions or METHOD_CONSTANTS.

    Returns the tokens as written in the answer, without duplicates, in order.
    """
    allowed = tool_numbers(tool_calls) | METHOD_CONSTANTS
    for q in questions:
        allowed |= {canonical(t) for t in answer_numbers(q)}
    missing = []
    for token in answer_numbers(answer):
        if canonical(token) not in allowed and token not in missing:
            missing.append(token)
    return missing


if __name__ == "__main__":  # quick manual check, from server/: python -m app.agent.grounding
    calls = [{"input": {"code": "BOS"}, "result": json.loads(
        '{"load_factor": 0.8166, "load_factor_display": "81.7%", "cagr_19_25_display": "-2.7%",'
        ' "rank_range_sensitivity": "8–10", "window": {"start": "2026-10-14"}}')}]
    answer = ("1. Boston is 9th of 30 large hubs (range 8–10). Load factor 81.7%, CAGR "
              "2019-2025 declined 2.7%; per D15 and T-100 data, 2024–25 saw 21.0M passengers "
              "since 14 October.")
    print(answer_numbers(answer))
    print(ungrounded_numbers(answer, calls, ["How is BOS?"]))
