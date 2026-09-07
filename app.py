# 스트림릿 페이지 코드

import uuid
import streamlit as st
from config.settings import MODEL_PROFILES, DEFAULT_PROFILE
from engine import run_agent_engine
from room_manager import get_available_prompts, get_available_tool_groups, get_default_rooms

st.set_page_config(page_title="로컬 에이전트 인터페이스", layout="wide")

# ==========================================
# 1. 세션 상태 초기화
# ==========================================
if "rooms" not in st.session_state:
    st.session_state.rooms = get_default_rooms()

if "active_room_id" not in st.session_state:
    st.session_state.active_room_id = list(st.session_state.rooms.keys())[0]

if "is_generating" not in st.session_state:
    st.session_state.is_generating = False

# 현재 활성 방 데이터 안전 조회
if st.session_state.active_room_id not in st.session_state.rooms:
    st.session_state.active_room_id = list(st.session_state.rooms.keys())[0]

active_room = st.session_state.rooms[st.session_state.active_room_id]

# ==========================================
# 2. 다이얼로그 모달
# ==========================================
@st.dialog("➕ 새 채팅방 만들기")
def create_room_dialog():
    available_prompts = get_available_prompts()
    available_tools = get_available_tool_groups()

    room_name = st.text_input("방 이름", placeholder="예: 만화 수다방, 코드 리뷰방")

    prompt_options = {p["filename"]: p["display"] for p in available_prompts}
    selected_prompts = st.multiselect(
        "적용할 시스템 프롬프트",
        options=list(prompt_options.keys()),
        default=["01_persona.md"] if "01_persona.md" in prompt_options else [],
        format_func=lambda x: prompt_options.get(x, x)
    )

    tool_options = {name: data["display"] for name, data in available_tools.items()}
    selected_tool_names = st.multiselect(
        "부여할 도구",
        options=list(tool_options.keys()),
        format_func=lambda x: tool_options.get(x, x)
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("생성하기", type="primary", use_container_width=True):
            if not room_name.strip():
                st.error("방 이름을 입력해주세요.")
                return

            new_room_id = f"room_{uuid.uuid4().hex[:8]}"
            selected_schemas = [available_tools[t]["schema"] for t in selected_tool_names]

            st.session_state.rooms[new_room_id] = {
                "name": room_name.strip(),
                "prompt_files": selected_prompts,
                "tools": selected_schemas,
                "messages": []
            }
            st.session_state.active_room_id = new_room_id
            st.rerun()

    with col2:
        if st.button("취소", use_container_width=True):
            st.rerun()

@st.dialog("⚠️ 대화 내역 초기화")
def confirm_clear_dialog(room_id: str):
    target_name = st.session_state.rooms[room_id]["name"]
    st.write(f"정말로 **{target_name}**의 대화 내역을 모두 삭제하시겠습니까?")
    st.caption("대화 기록만 삭제되며 방 설정은 유지됩니다.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("삭제 확인", type="primary", use_container_width=True):
            st.session_state.rooms[room_id]["messages"] = []
            st.rerun()
    with col2:
        if st.button("취소", use_container_width=True):
            st.rerun()

# ==========================================
# 3. 사이드바 제어 패널
# ==========================================
with st.sidebar:
    st.title("⚙️ 제어 패널")

    if st.button("➕ 새 채팅방 만들기", use_container_width=True, disabled=st.session_state.is_generating):
        create_room_dialog()

    st.divider()

    # 방 선택 라디오
    room_keys = list(st.session_state.rooms.keys())
    current_index = room_keys.index(st.session_state.active_room_id)

    selected_room = st.radio(
        "채팅방 목록",
        options=room_keys,
        index=current_index,
        format_func=lambda r_id: st.session_state.rooms[r_id]["name"],
        disabled=st.session_state.is_generating
    )

    if selected_room != st.session_state.active_room_id:
        st.session_state.active_room_id = selected_room
        st.rerun()

    st.divider()

    # 모델 프로필 선택
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

    if st.button("🗑️ 현재 방 대화 초기화", use_container_width=True, disabled=st.session_state.is_generating):
        confirm_clear_dialog(st.session_state.active_room_id)

# ==========================================
# 4. 메인 채팅 영역
# ==========================================
st.subheader(active_room["name"])

info_prompts = ", ".join(active_room["prompt_files"]) if active_room["prompt_files"] else "기본값 없음"
info_tools_count = len(active_room["tools"])
st.caption(f"📄 프롬프트: `{info_prompts}` | 🛠️ 장착 도구: `{info_tools_count}개`")

# 대화 히스토리 출력
for msg in active_room["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 입력 인터페이스
user_input = st.chat_input(
    "모델이 답변 중입니다..." if st.session_state.is_generating else "메시지를 입력하세요...",
    disabled=st.session_state.is_generating
)

if user_input:
    st.session_state.is_generating = True

    # 1. 사용자 메시지 기록 및 렌더링
    active_room["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 2. 모델 응답 스트림 수신
    with st.chat_message("assistant"):
        status_box = st.empty()
        full_response = ""

        try:
            generator = run_agent_engine(
                user_message=user_input,
                history=active_room["messages"][:-1],
                prompt_files=active_room["prompt_files"],
                tools=active_room["tools"],
                profile_key=selected_model_key
            )

            for event in generator:
                if event["type"] == "tool_start":
                    status_box.status(f"🛠️ 도구 실행: `{event['name']}`", state="running")
                elif event["type"] == "tool_end":
                    with st.expander(f"도구 결과: `{event['name']}`", expanded=False):
                        st.json(event["result"])
                elif event["type"] == "text":
                    full_response = event["content"]
                    st.markdown(full_response)
        finally:
            st.session_state.is_generating = False

        status_box.empty()

    # 3. 완료 후 히스토리 반영 및 화면 리셋
    if full_response:
        active_room["messages"].append({"role": "assistant", "content": full_response})
    st.rerun()