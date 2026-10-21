"""
Live recent-traffic tool (D7): arrivals and departures from the OpenSky Network
REST API, compared with the 2025 T-100 baseline.

Context only, never part of the investment score. Results are cached on disk
(data/cache/opensky/) for 24 hours; if the API fails, the last cached result for
the airport is returned marked stale. The tool never raises.

OpenSky API facts (verified against the docs and live calls, Oct 2026):
  - Auth: OAuth2 client credentials only (Bearer token, valid 30 min). Anonymous
    calls to /flights/* are refused (HTTP 403).
  - /flights/arrival and /flights/departure: max 2-day window per call, filled
    by a nightly batch (only past days), HTTP 404 = no flights.
  - Each call costs ~30 credits (4,000 per day on a standard account).
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv

from app.core.scoring import REPO_ROOT

from .common import _clean, _error, _fmt, _identity, _lookup_code, _ok

load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://opensky-network.org/api"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
CODES_CSV = REPO_ROOT / "data" / "processed" / "airport_codes.csv"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "opensky"

TIMEOUT_S = 10
CACHE_TTL = timedelta(hours=24)
MAX_DAYS = 7           # protects the daily credit budget (~240 credits per 7-day airport)
CHUNK_DAYS = 2         # API maximum window per call
SOURCE = "OpenSky Network (live)"

# Airline-style ICAO callsign: 3-letter operator code + flight number, with an
# optional letter suffix (e.g. SWA3621, BAW12K). Registrations like N123AB don't match.
AIRLINE_CALLSIGN = re.compile(r"[A-Z]{3}\d+[A-Z]{0,2}")

# A recent airline count this far below the 2025 baseline more likely reflects
# ADS-B coverage gaps than a real drop.
LOW_COVERAGE_DIFF = -0.5

CAVEATS = [
    "Live context only: not used in the investment score (D7).",
    "ADS-B tracking data: counts all tracked flights, including non-airline "
    "(general aviation, business, military). Airline departures are identified by "
    "callsign pattern (3 letters + digits), an approximation: some non-airline "
    "operators also use such callsigns.",
    "Flights, not passengers.",
    "ADS-B coverage may be incomplete, and OpenSky estimates each flight's airports "
    "from its track, so counts can be below actual traffic.",
    "The comparison sets one recent window against a full-year 2025 average: "
    "seasonality, day-of-week effects and one-off events can explain part of the difference.",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ApiError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    """certifi's CA bundle when available: python.org builds on macOS don't use the
    system certificates, so HTTPS fails with CERTIFICATE_VERIFY_FAILED without it."""
    try:
        import certifi  # installed with httpx (anthropic client)
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()


def _urlopen(req: urllib.request.Request):
    return urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_SSL)


@lru_cache(maxsize=1)
def _icao_codes() -> dict:
    df = pd.read_csv(CODES_CSV, dtype=str)
    return dict(zip(df["code"], df["icao"]))


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(days: int) -> tuple[int, int]:
    """Last `days` full UTC days: [start of today - days, start of today)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = int(today.timestamp())
    return end - days * 86400, end


_token = {"value": None, "expires_at": 0.0}


def _get_token(refresh: bool = False) -> str | None:
    """Bearer token, or None for anonymous access when credentials are missing."""
    client_id = os.environ.get("OPENSKY_CLIENT_ID")
    client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    if not refresh and _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    for attempt in (1, 2):
        try:
            with _urlopen(urllib.request.Request(TOKEN_URL, data=body)) as r:
                payload = json.load(r)
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == 2:
                raise _ApiError(f"OpenSky authentication failed: {e}") from e
    _token["value"] = payload["access_token"]
    _token["expires_at"] = time.time() + payload.get("expires_in", 1800)
    return _token["value"]


def _get_flights(endpoint: str, icao: str, begin: int, end: int) -> list[dict]:
    """One API call (<= 2 days), with one retry on timeouts, network errors, 5xx and 401."""
    base = os.environ.get("OPENSKY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/flights/{endpoint}?" + urllib.parse.urlencode({"airport": icao, "begin": begin, "end": end})
    refresh = False
    for attempt in (1, 2):
        token = _get_token(refresh=refresh)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with _urlopen(urllib.request.Request(url, headers=headers)) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # OpenSky: no flights in this window
                return []
            if e.code == 401 and attempt == 1:  # expired token
                refresh = True
                continue
            if e.code >= 500 and attempt == 1:
                continue
            if e.code == 403 and token is None:
                raise _ApiError(
                    "OpenSky refused anonymous access (HTTP 403); set OPENSKY_CLIENT_ID "
                    "and OPENSKY_CLIENT_SECRET in .env."
                ) from e
            if e.code == 429:
                raise _ApiError("OpenSky daily credit limit reached (HTTP 429).") from e
            raise _ApiError(f"OpenSky returned HTTP {e.code} for /flights/{endpoint}.") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == 1:
                continue
            raise _ApiError(f"OpenSky request failed for /flights/{endpoint}: {e}") from e
    raise _ApiError(f"OpenSky request failed for /flights/{endpoint} after a retry.")


def _fetch_counts(icao: str, begin: int, end: int) -> dict:
    """Arrival/departure counts over the window, in chunks of the API maximum."""
    flights = {"arrival": {}, "departure": {}}
    for endpoint, seen in flights.items():
        for b in range(begin, end, CHUNK_DAYS * 86400):
            for f in _get_flights(endpoint, icao, b, min(b + CHUNK_DAYS * 86400, end)):
                # Deduplicate flights that fall on a chunk boundary.
                seen[(f.get("icao24"), f.get("firstSeen"), f.get("lastSeen"))] = f
    departures = flights["departure"].values()
    return {
        "arrivals": len(flights["arrival"]),
        "departures": len(departures),
        "airline_departures": sum(
            1 for f in departures if AIRLINE_CALLSIGN.fullmatch((f.get("callsign") or "").strip())
        ),
    }


# --- Disk cache: one JSON file per airport and window -----------------------

def _cache_path(code: str, begin: int, end: int):
    fmt = "%Y%m%d"
    b = datetime.fromtimestamp(begin, timezone.utc).strftime(fmt)
    e = datetime.fromtimestamp(end, timezone.utc).strftime(fmt)
    return CACHE_DIR / f"{code}_{b}_{e}.json"


def _read_cache(path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(path, entry: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2))
    except OSError:
        pass  # caching is best effort


def _is_fresh(entry: dict) -> bool:
    fetched = datetime.fromisoformat(entry["fetched_at"].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - fetched < CACHE_TTL


def _latest_cache(code: str) -> dict | None:
    """Most recently fetched cache entry for this airport, any window."""
    entries = [e for p in CACHE_DIR.glob(f"{code}_*.json") if (e := _read_cache(p))]
    return max(entries, key=lambda e: e["fetched_at"], default=None)


# --- Response ---------------------------------------------------------------

def _per_day(n, days):
    return n / days if n is not None else None


def _result(row: pd.Series, icao: str, entry: dict, cache_status: str, error: str | None = None) -> dict:
    begin, end = entry["begin"], entry["end"]
    days = (end - begin) // 86400
    counts = {k: entry[k] for k in ("arrivals", "departures", "airline_departures")}

    recent = {}
    for key, n in counts.items():
        recent[key] = n
        recent[f"{key}_display"] = _fmt(n, "count")
        recent[f"{key}_per_day"] = _clean(_per_day(n, days))
        recent[f"{key}_per_day_display"] = _fmt(_per_day(n, days), "decimal_1")
    airline_share = counts["airline_departures"] / counts["departures"] if counts["departures"] else None
    recent["airline_share_of_departures"] = _clean(airline_share)
    recent["airline_share_of_departures_display"] = _fmt(airline_share, "pct")

    flags = []
    baseline = row["tot_departures_all"] / 365 if pd.notna(row["tot_departures_all"]) else None
    recent_airline = _per_day(counts["airline_departures"], days)
    diff = recent_airline / baseline - 1 if baseline else None
    if baseline is None:
        flags.append("no T-100 flight data for this airport: no 2025 baseline to compare with (D9).")
    elif diff is not None and diff <= LOW_COVERAGE_DIFF:
        flags.append(
            f"recent airline departures are {_fmt(diff, 'pct_signed')} vs the 2025 average; a gap "
            "this large more likely reflects incomplete ADS-B coverage than a real drop."
        )
    if cache_status == "stale":
        flags.append(
            f"stale: live API unavailable ({error}). Showing the last cached result, fetched "
            f"{entry['fetched_at']} for {_iso(begin)} to {_iso(end)}."
        )

    return _ok(
        airport={**_identity(row), "icao": icao},
        source=SOURCE,
        cache_status=cache_status,
        stale=cache_status == "stale",
        fetched_at=entry["fetched_at"],
        window={
            "begin_utc": _iso(begin),
            "end_utc": _iso(end),
            "days": days,
            "rule": "last full UTC days; end = start of the fetch day (exclusive)",
        },
        units={
            "counts": "flights tracked by OpenSky in the window",
            "per_day": "count / days in window",
            "pct_difference": "fraction (0.05 = +5%)",
        },
        definitions={
            "airline_departures": "departures whose callsign is 3 letters + digits (+ up to 2 letters)",
            "baseline_2025": "T-100 departures performed, all service classes (scheduled, charter, cargo), / 365",
            "comparison": "recent airline departures per day vs 2025 baseline departures per day",
        },
        recent=recent,
        comparison_2025={
            "baseline_departures_per_day": _clean(baseline),
            "baseline_departures_per_day_display": _fmt(baseline, "decimal_1"),
            "recent_airline_departures_per_day": _clean(recent_airline),
            "recent_airline_departures_per_day_display": _fmt(recent_airline, "decimal_1"),
            "pct_difference": _clean(diff),
            "pct_difference_display": _fmt(diff, "pct_signed"),
        },
        caveats=CAVEATS,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def get_recent_traffic(code: str, days: int = 7) -> dict:
    """Recent arrivals/departures from OpenSky for the last `days` full UTC days,
    compared with the 2025 T-100 daily departures. Never raises."""
    try:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            return _error(f"days must be a whole number from 1 to {MAX_DAYS}.")
        row = _lookup_code(code)
        if row is None:
            return _error(f"Unknown airport code '{code}'. Use find_airport to resolve names or cities.")
        icao = _icao_codes().get(row["code"])
        if not isinstance(icao, str):
            return _error(f"No ICAO code for {row['code']}: live data unavailable.", airport=_identity(row))

        begin, end = _window(days)
        path = _cache_path(row["code"], begin, end)
        cached = _read_cache(path)
        if cached and _is_fresh(cached):
            return _result(row, icao, cached, "cached")

        try:
            counts = _fetch_counts(icao, begin, end)
        except _ApiError as e:
            fallback = _latest_cache(row["code"])
            if fallback:
                return _result(row, icao, fallback, "stale", error=str(e))
            return _error(
                f"Live data unavailable: {e} No cached result for {row['code']}.",
                airport=_identity(row),
                source=SOURCE,
            )

        entry = {
            "code": row["code"],
            "icao": icao,
            "begin": begin,
            "end": end,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **counts,
        }
        _write_cache(path, entry)
        return _result(row, icao, entry, "live")
    except Exception as e:  # last resort: the tool never raises
        return _error(f"get_recent_traffic failed: {type(e).__name__}: {e}")
