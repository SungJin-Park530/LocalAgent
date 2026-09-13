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

if "is_generating" not in st.session_state:
    st.session_state.is_generating = False

# 방 목록 키 안전 조회
room_keys = list(st.session_state.rooms.keys())

# active_room_id 초기화 및 유효성 보정 (빈 딕셔너리일 때 [0] 접근 방지)
if "active_room_id" not in st.session_state or st.session_state.active_room_id not in room_keys:
    st.session_state.active_room_id = room_keys[0] if room_keys else None

# ==========================================
# 2. 다이얼로그 모달
# ==========================================
@st.dialog("➕ 새 채팅방 만들기")
def create_room_dialog():
    available_prompts = get_available_prompts()
    available_tools = get_available_tool_groups()

    room_name = st.text_input("방 이름", placeholder="예: 만화 수다방, 코드 리뷰방")

    # 1. 프롬프트 선택 (기본값: 페르소나)
    prompt_options = {p["filename"]: p["display"] for p in available_prompts}
    selected_prompts = st.multiselect(
        "적용할 시스템 프롬프트",
        options=list(prompt_options.keys()),
        default=["01_persona.md"] if "01_persona.md" in prompt_options else [],
        format_func=lambda x: prompt_options.get(x, x)
    )

    # 2. 도구 선택 (기본값: 현재 시간 확인 도구)
    tool_keys = list(available_tools.keys())
    default_tools = ["get_current_time"] if "get_current_time" in tool_keys else []
    
    selected_tool_names = st.multiselect(
        "부여할 도구",
        options=tool_keys,
        default=default_tools,
        format_func=lambda k: available_tools[k]["display"]
    )

    col1, col2 = st.columns(2)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("생성하기", type="primary", use_container_width=True):
            # [유효성 검사 3종]
            if not room_name.strip():
                st.error("방 이름을 입력해주세요.")
            elif not selected_prompts:
                st.error("최소 1개 이상의 시스템 프롬프트를 선택해주세요.")
            elif not selected_tool_names:
                st.error("안정적인 응답 생성을 위해 최소 1개 이상의 도구를 선택해주세요.")
            else:
                # 모든 검증을 통과했을 때만 생성 진행
                new_room_id = f"room_{uuid.uuid4().hex[:8]}"
                selected_schemas = [available_tools[k]["schema"] for k in selected_tool_names]

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

@st.dialog("🗑️ 채팅방 삭제")
def confirm_delete_room_dialog(room_id: str):
    target_name = st.session_state.rooms[room_id]["name"]
    st.write(f"정말로 **{target_name}**을(를) 완전히 삭제하시겠습니까?")
    st.caption("방에 포함된 모든 대화 기록과 설정이 사라지며 되돌릴 수 없습니다.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("삭제 확인", type="primary", use_container_width=True):
            # 1. 방 삭제
            del st.session_state.rooms[room_id]
            
            # 2. 남은 방 포인터 재조정
            remaining_keys = list(st.session_state.rooms.keys())
            st.session_state.active_room_id = remaining_keys[0] if remaining_keys else None
            
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

    room_keys = list(st.session_state.rooms.keys())

    # 방이 1개 이상 존재할 때만 방 선택 라디오 및 액션 노출
    if room_keys:
        # 안전한 인덱스 바인딩
        if st.session_state.active_room_id not in room_keys:
            st.session_state.active_room_id = room_keys[0]
            
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

        # 방 대화 비우기 & 방 자체 삭제
        col_clear, col_del = st.columns(2)
        with col_clear:
            if st.button("🧹 대화 비우기", use_container_width=True, disabled=st.session_state.is_generating):
                confirm_clear_dialog(st.session_state.active_room_id)
        with col_del:
            if st.button("🗑️ 방 삭제", type="secondary", use_container_width=True, disabled=st.session_state.is_generating):
                confirm_delete_room_dialog(st.session_state.active_room_id)
    else:
        st.info("생성된 채팅방이 없습니다.")

# ==========================================
# 4. 메인 채팅 영역
# ==========================================

# 방이 전혀 없을 때 (Empty State)
if not st.session_state.rooms or not st.session_state.active_room_id:
    st.info("💬 활성화된 채팅방이 없습니다.")
    st.markdown(
        """
        좌측 사이드바의 **[➕ 새 채팅방 만들기]** 버튼을 눌러 원하는 프롬프트와 도구를 장착한 새 채팅방을 생성해 보세요!
        """
    )
    st.stop()  # 이후 채팅 렌더링 및 chat_input 실행 중단

# 방이 존재하면 정상적으로 active_room 추출 후 기존 로직 진행
active_room = st.session_state.rooms[st.session_state.active_room_id]

col_title, col_settings = st.columns([0.85, 0.15])

# 1) 헤더 및 설정 팝오버 배치 (가로 2단 컬럼)
col_title, col_settings = st.columns([0.85, 0.15])

with col_title:
    st.subheader(active_room["name"])

with col_settings:
    with st.popover("⚙️ 방 설정", use_container_width=True):
        st.markdown(f"#### ⚙️ `{active_room['name']}` 상세")
        st.caption("현재 적용된 시스템 프롬프트 및 도구 설정입니다. (읽기 전용)")
        st.divider()

        # 프롬프트 목록 표시
        st.markdown("**📄 적용된 프롬프트**")
        if active_room["prompt_files"]:
            for p_file in active_room["prompt_files"]:
                st.markdown(f"- `{p_file}`")
        else:
            st.caption("설정된 프롬프트가 없습니다.")

        st.divider()

        # 도구 목록 표시 (함수명 및 메타데이터 추출)
        tools_count = len(active_room["tools"])
        st.markdown(f"**🛠️ 장착된 도구 ({tools_count}개)**")
        if active_room["tools"]:
            for tool in active_room["tools"]:
                func_info = tool.get("function", {})
                func_name = func_info.get("name", "알 수 없는 도구")
                func_desc = func_info.get("description", "설명 없음")
                
                # 도구명과 설명을 깔끔하게 리스트 형태로 렌더링
                st.markdown(f"- **`{func_name}`**")
                st.caption(f"  └ {func_desc}")
        else:
            st.caption("장착된 도구가 없습니다.")

st.divider()

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
            # 컨텍스트 객체 확보 (키가 없을 경우 빈 딕셔너리로 초기화)
            room_context = active_room.setdefault("context", {})

            generator = run_agent_engine(
                user_message=user_input,
                history=active_room["messages"][:-1],
                prompt_files=active_room["prompt_files"],
                tools=active_room["tools"],
                profile_key=selected_model_key,
                room_context=room_context  # <-- context 전달
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