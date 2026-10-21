"""Single-airport tools: find_airport, get_airport_profile."""

import re

from .common import (
    REGIONS,
    UNITS,
    _error,
    _identity,
    _lookup_code,
    _ok,
    _profile,
    _scores,
)

MAX_CANDIDATES = 10

# Generic words removed before matching names and cities, so that
# "Anchorage airport" matches like "Anchorage".
FILLER_WORDS = re.compile(r"\b(the|airport|airports|international|intl)\b", re.IGNORECASE)

# Informal names that don't match a single city or code. Each carries the
# assumption the agent must state when it uses the match.
LA_NOTE = (
    "Assumed 'Los Angeles' means LAX, the only Primary airport in the city of "
    "Los Angeles. The wider LA area also has Burbank (BUR), Long Beach (LGB), "
    "Ontario (ONT) and Santa Ana / John Wayne (SNA)."
)
ALIASES = {
    "la": ("LAX", LA_NOTE),
    "los angeles": ("LAX", LA_NOTE),
}


def find_airport(query: str) -> dict:
    """Resolve a code, city or name to a Primary airport.

    Generic words ("airport", "international", ...) are ignored. Order: FAA code
    -> IATA code -> known alias -> exact city -> partial name/city match.
    Several hits return `candidates` instead of a guess.
    """
    if not isinstance(query, str) or not query.strip():
        return _error("query must be a non-empty string (airport code, city or name).")
    q = query.strip()
    # Fall back to the full query if it was only filler words.
    name_q = " ".join(FILLER_WORDS.sub(" ", q).split()) or q
    df = _scores()

    if name_q.lower() in REGIONS:
        return _error(
            f"'{name_q}' is a region, not an airport. Use rank_airports(states='{name_q}') "
            f"(states: {', '.join(REGIONS[name_q.lower()])})."
        )

    for candidate in dict.fromkeys([q, name_q]):  # e.g. "SFO airport" -> "SFO"
        row = _lookup_code(candidate)
        if row is not None:
            match_type = "faa_code" if row["code"].upper() == candidate.upper() else "iata_code"
            return _ok(query=q, match=_identity(row), match_type=match_type, note=None, candidates=[])

    if name_q.lower() in ALIASES:
        code, note = ALIASES[name_q.lower()]
        return _ok(query=q, match=_identity(_lookup_code(code)), match_type="alias", note=note, candidates=[])

    hits = df[df["city"].str.lower() == name_q.lower()]
    match_type = "city"
    if hits.empty and len(name_q) >= 3:
        pattern = name_q.lower()
        hits = df[
            df["city"].str.lower().str.contains(pattern, regex=False)
            | df["name"].str.lower().str.contains(pattern, regex=False)
        ]
        match_type = "partial_name_or_city"

    if len(hits) == 1:
        return _ok(query=q, match=_identity(hits.iloc[0]), match_type=match_type, note=None, candidates=[])
    if len(hits) > 1:
        hits = hits.sort_values("enpl_2025", ascending=False)
        return _ok(
            query=q,
            match=None,
            match_type="ambiguous",
            note=f"{len(hits)} airports match '{name_q}'. Ask the user which one they mean.",
            candidates=[_identity(r) for _, r in hits.head(MAX_CANDIDATES).iterrows()],
        )
    return _error(
        f"No FAA Primary airport matches '{name_q}'. Only airports with more than "
        "10,000 annual enplanements are covered (D2)."
    )


def get_airport_profile(code: str) -> dict:
    """Full metrics, score, components, rank and flags for one airport."""
    row = _lookup_code(code)
    if row is None:
        return _error(f"Unknown airport code '{code}'. Use find_airport to resolve names or cities.")
    return _ok(units=UNITS, airport=_profile(row))
