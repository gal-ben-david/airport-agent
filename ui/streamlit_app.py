"""
Streamlit chat UI for the airport investment agent.

A thin layer over run_agent: it keeps the chat history, shows the answers and the
tool calls behind them. No agent logic lives here.

Run from the repo root:
    streamlit run ui/streamlit_app.py
"""

import sys
from pathlib import Path

import streamlit as st

# Make `app` importable: this file is <repo>/ui/streamlit_app.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.agent.agent import run_agent  # noqa: E402
from app.agent.questions import ASSIGNMENT_QUESTIONS  # noqa: E402

st.set_page_config(page_title="Airport Investment Agent", page_icon="✈️")

# Each message: {"role", "content", "tool_calls": [...], "error": bool}
if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Airport Investment Agent")
    st.write(
        "Ask about US airports as terminal expansion candidates. The agent scores "
        "FAA Primary airports on passenger growth, seat demand and congestion within "
        "their hub-size peer group. All numbers come from deterministic tools; the "
        "LLM only picks the tools and explains the results."
    )
    st.subheader("Data sources")
    st.markdown(
        "- FAA enplanements, 2025 (preliminary)\n"
        "- BTS T-100 segments, 2025\n"
        "- BTS airline delay causes, 2025"
    )
    st.caption("Analysis, not financial advice.")
    if st.button("New conversation", width="stretch"):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def show_tool_calls(tool_calls: list[dict]) -> None:
    """Expander listing each tool call with its inputs and result."""
    if not tool_calls:
        return
    with st.expander(f"Tools used ({len(tool_calls)})"):
        for i, call in enumerate(tool_calls, 1):
            if not call["executed"]:
                status = "skipped (tool budget)"
            elif call["is_error"]:
                status = "error"
            else:
                status = "ok"
            st.markdown(f"**{i}. `{call['name']}`** — {status}")
            st.caption("Inputs")
            st.json(call["input"])
            st.caption("Result")
            st.json(call["result"], expanded=False)


def history_for_agent() -> list[dict]:
    """Chat history as run_agent expects it, without failed turns.

    A failed turn (user question + error message) is left out so the agent sees
    a clean user/assistant alternation.
    """
    msgs = st.session_state.messages
    history = []
    for i, m in enumerate(msgs):
        if m.get("error"):
            continue
        next_is_error = i + 1 < len(msgs) and msgs[i + 1].get("error")
        if m["role"] == "user" and next_is_error:
            continue
        history.append({"role": m["role"], "content": m["content"]})
    return history


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

st.title("✈️ Airport Investment Agent")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m.get("error"):
            st.error(m["content"])
        else:
            st.markdown(m["content"])
        show_tool_calls(m.get("tool_calls", []))

# Typed input, or an example question clicked on the previous run.
prompt = st.chat_input("Ask about US airports...") or st.session_state.pop("pending_prompt", None)

# Example questions before the first message; a click sends that question.
if not st.session_state.messages and not prompt:
    st.write("Try one of the assignment questions:")
    for q in ASSIGNMENT_QUESTIONS:
        if st.button(q["text"], key=q["id"], width="stretch"):
            st.session_state.pending_prompt = q["text"]
            st.rerun()

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Analyzing..."):
                result = run_agent(history_for_agent())
            reply = {"role": "assistant", "content": result["answer"],
                     "tool_calls": result["tool_calls"]}
            st.markdown(reply["content"])
        except Exception as e:  # missing API key, network or API errors
            reply = {"role": "assistant", "content": f"The agent could not answer: {e}",
                     "tool_calls": [], "error": True}
            st.error(reply["content"])
        show_tool_calls(reply["tool_calls"])

    st.session_state.messages.append(reply)
