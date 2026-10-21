"""
Manual check of the live OpenSky tool (get_recent_traffic): live call, cache hit,
stale-cache fallback, unavailable message and unknown code.

Uses real API credits (~240 per airport for a fresh 7-day window).

Run from the repo root:
    python server/scripts/test_live.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Make `app` importable: this file is <repo>/server/scripts/test_live.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.tools import get_recent_traffic  # noqa: E402
from app.agent.tools.live import CACHE_DIR  # noqa: E402

BAD_BASE_URL = "https://invalid.opensky.example/api"  # simulates an API outage
NO_CACHE_CODE = "BTV"


def show(title: str, result: dict, seconds: float) -> None:
    print(f"\n{'=' * 80}\n{title}  ({seconds:.1f}s)\n{'=' * 80}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def call(title: str, *args, **kwargs) -> dict:
    t0 = time.perf_counter()
    result = get_recent_traffic(*args, **kwargs)
    show(title, result, time.perf_counter() - t0)
    return result


def summary(label: str, r: dict) -> None:
    if not r["ok"]:
        print(f"{label:<28} ok=False  {r['error']}")
        return
    print(
        f"{label:<28} {r['cache_status']:<7} {r['window']['begin_utc']} -> {r['window']['end_utc']}  "
        f"dep={r['recent']['departures_display']} airline={r['recent']['airline_departures_display']} "
        f"diff={r['comparison_2025']['pct_difference_display']}"
    )


def main() -> None:
    os.environ.pop("OPENSKY_BASE_URL", None)
    results = {}

    # 1. Live calls (or cached, if run earlier today)
    results["SFO 7d"] = call("1. SFO, last 7 days", "SFO")
    results["BOS 7d"] = call("2. BOS, last 7 days", "BOS")

    # 2. Same request again: must come from the cache
    results["SFO 7d again"] = call("3. SFO again (expect cache_status 'cached')", "SFO")

    # 3. Simulated outage
    os.environ["OPENSKY_BASE_URL"] = BAD_BASE_URL
    # A different window (3 days) so the fresh 7-day cache doesn't answer it.
    results["SFO 3d, API down"] = call("4a. SFO, 3 days, API down (expect stale 7-day cache)", "SFO", days=3)
    for p in CACHE_DIR.glob(f"{NO_CACHE_CODE}_*.json"):
        p.unlink()
    results[f"{NO_CACHE_CODE} 7d, API down"] = call(
        f"4b. {NO_CACHE_CODE}, API down, no cache (expect 'live data unavailable')", NO_CACHE_CODE
    )
    os.environ.pop("OPENSKY_BASE_URL")

    # 4. Bad input
    results["XXX"] = call("5. Unknown code XXX", "XXX")
    results["SFO days=30"] = call("6. Invalid days (30)", "SFO", days=30)

    print(f"\n{'=' * 80}\nSummary\n{'=' * 80}")
    for label, r in results.items():
        summary(label, r)


if __name__ == "__main__":
    main()
