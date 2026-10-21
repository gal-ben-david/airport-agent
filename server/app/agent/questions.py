"""
The assignment questions, in one place so scripts and evals reuse the exact wording.
"""

ASSIGNMENT_QUESTIONS = [
    {"id": "new_england", "text": "Which airports in New England are strong candidates for terminal expansion?"},
    {"id": "la_vs_santa_ana", "text": "Compare LA and Santa Ana airport congestion levels."},
    {"id": "anchorage_long_haul", "text": "What is the percentage of long haul flights out of Anchorage airport?"},
    {"id": "sfo_unmet_demand", "text": "What is the unmet flight demand in SFO airport and why?"},
]

# Follow-up asked after a previous question, to test use of conversation history.
FOLLOW_UPS = [
    {"id": "bradley_vs_boston", "after": "new_england", "text": "And how does Bradley compare to Boston?"},
]
