"""
Streamlit chat UI for the recruiter Main Agent - the "dedicated UI"
mentioned in agents_Instructions/main_agent.md.

Run from the repo root with:
    streamlit run streamlit_app/streamlit_main.py
"""
import streamlit as st

from streamlit_app.utils import get_session_id, load_agent

st.set_page_config(page_title="Python Developer - Application Chat", page_icon="💬")
st.title("Python Developer Application")

agent = load_agent()
session_id = get_session_id()

if "messages" not in st.session_state:
    st.session_state.messages = []
    with st.spinner("Starting the conversation..."):
        opening_message = agent.step(session_id)
    st.session_state.messages.append({"role": "assistant", "content": opening_message})


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_input = st.chat_input("Type your message...")
if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply = agent.step(session_id, user_input)
        st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
