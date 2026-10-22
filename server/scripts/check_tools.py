"""
Manual check of the agent data tools: calls each tool and prints the JSON.

Run from the repo root:
    python server/scripts/check_tools.py
"""

import json
import sys
from pathlib import Path

# Make `app` importable: this file is <repo>/server/scripts/check_tools.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import tools  # noqa: E402

QUERIES = ["LAX", "SNA", "SFO", "BOS", "ANC", "HVN", "Los Angeles", "New England", "XXX"]
CODES = ["LAX", "SNA", "SFO", "BOS", "ANC", "HVN"]


def show(title: str, result: dict) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    json.dumps(result, allow_nan=False)  # fails if anything is not strict JSON


# --- 1. find_airport on every query
for q in QUERIES:
    show(f"find_airport({q!r})", tools.find_airport(q))

# --- 2. get_airport_profile: test codes, a resolved city, an unranked airport, an invalid code
for c in CODES:
    show(f"get_airport_profile({c!r})", tools.get_airport_profile(c))
la = tools.find_airport("Los Angeles")["match"]["code"]
show(f"get_airport_profile({la!r})  # via find_airport('Los Angeles')", tools.get_airport_profile(la))
show("get_airport_profile('YNG')  # unranked, 1 of 4 components", tools.get_airport_profile("YNG"))
show("get_airport_profile('XXX')", tools.get_airport_profile("XXX"))

# --- 3. rank_airports
show("rank_airports('New England')", tools.rank_airports("New England"))
show("rank_airports(['CA'], hub='L', top_n=3)", tools.rank_airports(["CA"], hub="L", top_n=3))
show("rank_airports(hub='medium', top_n=3)  # national", tools.rank_airports(hub="medium", top_n=3))
show("rank_airports(['XX'])", tools.rank_airports(["XX"]))
show("rank_airports(hub='huge')", tools.rank_airports(hub="huge"))
show("rank_airports(top_n=0)", tools.rank_airports(top_n=0))

# --- 4. compare_airports
show("compare_airports(['SFO', 'LAX'])  # same hub", tools.compare_airports(["SFO", "LAX"]))
show("compare_airports(['BOS', 'SNA', 'HVN'])  # mixed hubs", tools.compare_airports(["BOS", "SNA", "HVN"]))
show("compare_airports(['LAX', 'XXX'])", tools.compare_airports(["LAX", "XXX"]))
show("compare_airports(['LAX'])", tools.compare_airports(["LAX"]))

# --- 5. long_haul_share
for c in CODES:
    show(f"long_haul_share({c!r})", tools.long_haul_share(c))
show("long_haul_share('ANC', include_cargo=True)", tools.long_haul_share("ANC", include_cargo=True))
show("long_haul_share('ANC', 2500)", tools.long_haul_share("ANC", 2500))
show("long_haul_share('XXX')", tools.long_haul_share("XXX"))
show("long_haul_share('LAX', -5)", tools.long_haul_share("LAX", -5))
show("long_haul_share('LAX', 'far')", tools.long_haul_share("LAX", "far"))

# PASS/FAIL sanity checks live in server/tests/test_tools.py (run: pytest, from server/).
