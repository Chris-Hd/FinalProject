"""
Main wiring
-----------
Builds one fully-wired Agent (Main Agent + Info/Schedule/Exit advisors,
with the Info and Schedule tools attached) so that any front end -
the Streamlit UI (streamlit_app/streamlit_main.py) or a plain terminal
run (python -m app.main) - can import get_agent() and drive the same
conversation via Agent.step(session_id, user_input).

This mirrors the usage documented in:
* app/modules/embedding.py        -> build_search_job_description_tool()
* app/modules/sql_interface/schedule_db.py -> build_schedule_tools()
* app/modules/agents/agents.py     -> Agent(...)
"""
import os

from dotenv import load_dotenv

from app.modules.agents.agents import Agent
from app.modules.embedding import Embedder, build_search_job_description_tool
from app.modules.sql_interface.schedule_db import build_schedule_tools

load_dotenv()

POSITION = "Python Dev"
JOB_DESCRIPTION_PDF = "Python Developer Job Description.pdf"


def get_agent(position=POSITION, verbose=False):
    """
    Build and return a fully-wired Agent: Schedule tools (SQL Server) +
    Info tool (Chroma / job-description RAG) attached, ready to use as:

        agent = get_agent()
        opening_message = agent.step(session_id)              # first turn, no input
        reply           = agent.step(session_id, user_input)  # every turn after

    Reads configuration from .env (see .env.example): OPENAI_API_KEY
    (required), OPENAI_MODEL / OPENAI_FT_MODEL / OPENAI_BASE_URL (optional),
    plus the SQL_* variables consumed by schedule_db.ScheduleDB.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set - check your .env file (see .env.example).")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    ft_model = os.environ.get("OPENAI_FT_MODEL")  # optional fine-tuned model for the Exit advisor
    base_url = os.environ.get("OPENAI_BASE_URL")  # optional, e.g. for Ollama - unset -> OpenAI's default

    if not os.path.isdir("chroma_db"):
        print("[main] 'chroma_db' not found - building the vector store from the job description PDF...")
        Embedder(api_key=api_key).build_vectorstore(JOB_DESCRIPTION_PDF)

    sch_tools = build_schedule_tools(position=position)
    info_tools = [build_search_job_description_tool(api_key=api_key)]

    return Agent(
        api_key=api_key,
        model=model,
        ft_model=ft_model,
        base_url=base_url,
        system_message=f"The candidate has submitted their application for the {position} position.",
        sch_tools=sch_tools,
        info_tools=info_tools,
        temperature=0,
        verbose=verbose,
    )


if __name__ == "__main__":
    agent = get_agent(verbose=True)
    session_id = "terminal-test"

    print("Bot:", agent.step(session_id))  # opening turn - no user input yet
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in {"exit", "quit"}:
            break
        print("Bot:", agent.step(session_id, user_input))
