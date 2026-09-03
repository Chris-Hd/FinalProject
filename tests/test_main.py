import os
os.chdir('../')

from app.main import get_agent

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