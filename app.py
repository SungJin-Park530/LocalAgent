import streamlit as st

from views import chat, coding


st.set_page_config(page_title="로컬 에이전트 인터페이스", layout="wide")
st.session_state.setdefault("app_mode", "chat")

with st.sidebar:
    st.title("⚙️ 제어 패널")

    mode_col_chat, mode_col_coding = st.columns(2)
    with mode_col_chat:
        if st.button(
            "💬 일반 채팅",
            type="primary" if st.session_state.app_mode == "chat" else "secondary",
            use_container_width=True,
            key="mode_chat",
        ):
            st.session_state.app_mode = "chat"
            st.rerun()
    with mode_col_coding:
        if st.button(
            "💻 코딩 모드",
            type="primary" if st.session_state.app_mode == "coding" else "secondary",
            use_container_width=True,
            key="mode_coding",
        ):
            st.session_state.app_mode = "coding"
            st.rerun()

if st.session_state.app_mode == "chat":
    chat.render_chat_page()
else:
    coding.render_coding_page()