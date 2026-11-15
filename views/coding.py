import streamlit as st


def render_coding_page() -> None:
    with st.sidebar:
        st.divider()
        st.caption("📁 워크스페이스 설정 (준비 중)")

    st.title("💻 로컬 sLLM 코딩 워크스페이스")
    st.info("이곳에 프로젝트 샌드박스 탐색기 및 코딩 에이전트 화면이 구성될 예정입니다.")