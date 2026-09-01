"""
Streamlit-specific helpers.

Kept separate from streamlit_main.py so the Streamlit-only concerns
(resource caching, per-tab session ids) don't clutter the page script.

Note: streamlit_main.py adds the repo root to sys.path *before* importing
this module - that's what makes the `from app.main import get_agent` below
resolve correctly even though Streamlit runs streamlit_main.py directly.
"""
import ast
import json
import uuid

import streamlit as st

from app.main import get_agent


@st.cache_resource(show_spinner="Setting up the recruiter assistant...")
def load_agent():
    """
    Built once per Streamlit server process (Streamlit reruns the whole
    script on every interaction, so without caching this would reload the
    LLM, the Chroma store and the DB tools on every single message).
    """
    return get_agent()


def get_session_id() -> str:
    """One stable id per browser tab, used as the Agent's conversation/session key."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return st.session_state.session_id


def clean_agent_reply(raw) -> str:
    """
    The Main Agent sometimes hands back something other than clean plain
    text - either an actual dict object, or a string that merely *looks*
    like one, e.g. "{'intention': 'continue', 'response': 'Hello! ...'}"
    (Agent.step() is typed to return str, but the underlying AgentExecutor
    doesn't always honor that). That's the Main Agent's own prompt/
    formatting behavior (agents.py / agents_Instructions/main_agent.md) -
    not something this UI controls - so unwrap it defensively here,
    whatever shape it arrives in, rather than crashing or showing raw
    braces in the chat. Falls back to a plain string version of the input
    for anything that doesn't match a recognized shape.
    """
    parsed = raw
    if isinstance(raw, str):
        text = raw.strip()
        parsed = None
        if text.startswith("{") and text.endswith("}"):
            for loader in (ast.literal_eval, json.loads):
                try:
                    parsed = loader(text)
                    break
                except (ValueError, SyntaxError):
                    continue
        if parsed is None:
            return raw  

    if isinstance(parsed, dict):
        for key in ("response", "output", "message"):
            if isinstance(parsed.get(key), str):
                return parsed[key]
        for value in parsed.values():
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return clean_agent_reply(value)

    return str(raw)
