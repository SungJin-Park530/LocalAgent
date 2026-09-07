# 스트림릿 페이지 코드

import streamlit as st
from config.settings import MODEL_PROFILES, DEFAULT_PROFILE
from engine import run_agent_engine

st.set_page_config(page_title="로컬 에이전트 인터페이스", layout="wide")

# ==========================================
# 1. 세션 상태 초기화
# ==========================================
if "messages_chat" not in st.session_state:
    st.session_state.messages_chat = []

if "messages_agent" not in st.session_state:
    st.session_state.messages_agent = []

if "is_generating" not in st.session_state:
    st.session_state.is_generating = False

# ==========================================
# 2. 대화 초기화 확인 팝업 모달
# ==========================================
@st.dialog("⚠️ 대화 내역 초기화")
def confirm_clear_dialog(target_mode: str):
    room_name = "일상 잡담방" if target_mode == "chat" else "파일 에이전트방"
    st.write(f"정말로 **{room_name}**의 대화 내역을 모두 삭제하시겠습니까?")
    st.caption("삭제된 대화는 복구할 수 없습니다.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("삭제 확인", type="primary", use_container_width=True):
            if target_mode == "chat":
                st.session_state.messages_chat = []
            else:
                st.session_state.messages_agent = []
            st.rerun()
    with col2:
        if st.button("취소", use_container_width=True):
            st.rerun()

# ==========================================
# 3. 사이드바 UI (비활성화 및 팝업 연동)
# ==========================================
with st.sidebar:
    st.title("⚙️ 제어 패널")
    
    # 톡방 선택
    mode = st.radio(
        "대화 모드 선택",
        options=["chat", "agent"],
        format_func=lambda x: "💬 일상 잡담방" if x == "chat" else "🛠️ 파일 에이전트방",
        index=0,
        disabled=st.session_state.is_generating  # 추론 중 탭 전환도 비활성화
    )
    
    st.divider()
    
    # 모델 선택 드롭다운 (추론 중 비활성화)
    selected_model_key = st.selectbox(
        "사용할 모델",
        options=list(MODEL_PROFILES.keys()),
        index=list(MODEL_PROFILES.keys()).index(DEFAULT_PROFILE) if DEFAULT_PROFILE in MODEL_PROFILES else 0,
        format_func=lambda k: f"{k} ({MODEL_PROFILES[k].get('name')})",
        disabled=st.session_state.is_generating
    )
    
    desc = MODEL_PROFILES[selected_model_key].get("description", "")
    if desc:
        st.caption(f"💡 {desc}")

    st.divider()

    # 대화 초기화 버튼 (클릭 시 팝업 호출)
    if st.button("🗑️ 현재 방 대화 초기화", use_container_width=True, disabled=st.session_state.is_generating):
        confirm_clear_dialog(mode)

# ==========================================
# 4. 메인 채팅 렌더링
# ==========================================
current_history = st.session_state.messages_chat if mode == "chat" else st.session_state.messages_agent

st.subheader("💬 일상 잡담방" if mode == "chat" else "🛠️ 파일 에이전트방")

# 이전 히스토리 출력
for msg in current_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 사용자 입력창 (추론 중 비활성화 및 금지 커서 처리)
user_input = st.chat_input(
    "모델이 답변 중입니다..." if st.session_state.is_generating else "메시지를 입력하세요...",
    disabled=st.session_state.is_generating
)

# 사용자 입력 처리
if user_input:
    # 1. 생성 상태 플래그 켜기
    st.session_state.is_generating = True

    # 2. 히스토리에 유저 메시지 기록 및 렌더링
    current_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 3. 어시스턴트 답변 생성
    with st.chat_message("assistant"):
        status_box = st.empty()
        full_response = ""

        try:
            for event in run_agent_engine(user_input, current_history[:-1], mode, selected_model_key):
                if event["type"] == "tool_start":
                    status_box.status(f"🛠️ 도구 실행: `{event['name']}`", state="running")
                elif event["type"] == "tool_end":
                    with st.expander(f"도구 결과: `{event['name']}`", expanded=False):
                        st.json(event["result"])
                elif event["type"] == "text":
                    full_response = event["content"]
                    st.markdown(full_response)
        finally:
            # 에러가 나더라도 생성 상태 플래그는 반드시 복구
            st.session_state.is_generating = False

        status_box.empty()

    # 4. 세션 저장 후 최종 갱신
    if full_response:
        current_history.append({"role": "assistant", "content": full_response})
    
    st.rerun()