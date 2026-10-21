"""
Deterministic data tools for the airport investment agent.

Pure functions over data/processed/airport_scores.csv (and the raw T-100 file
for long-haul thresholds other than 3,000 miles), plus one live tool over the
OpenSky API for recent activity (live.py, D7). No LLM here: every number the
agent reports comes from these functions (CLAUDE.md, key principles).

Response format and shared helpers: see common.py.
"""

from .airports import find_airport, get_airport_profile
from .live import get_recent_traffic
from .long_haul import long_haul_share
from .rankings import compare_airports, rank_airports

# Tool name -> function, for the agent loop to dispatch on.
TOOLS = {
    "find_airport": find_airport,
    "get_airport_profile": get_airport_profile,
    "rank_airports": rank_airports,
    "compare_airports": compare_airports,
    "long_haul_share": long_haul_share,
    "get_recent_traffic": get_recent_traffic,
}

__all__ = [*TOOLS, "TOOLS"]
