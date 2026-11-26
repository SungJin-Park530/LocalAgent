from pathlib import Path

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coding_engine import run_coding_agent
from config.settings import DEFAULT_PROFILE, MODEL_PROFILES
from tools.coding_workspace import WorkspaceSnapshotManager, list_project_tree


PLAN_WORKFLOW = "🧭 계획 먼저 수립 (Plan → Act)"
AUTO_WORKFLOW = "⚡ 즉시 수정 실행 (Auto Act)"


def _run_turn(workspace_root, workspace_state, phase, message):
    messages = workspace_state["messages"]
    messages.append(HumanMessage(content=message))
    workspace_state["pending_plan"] = False
    progress = st.empty()
    completed = False
    try:
        for event in run_coding_agent(
            messages, workspace_root, phase, workspace_state["snapshot_manager"],
            st.session_state.coding_selected_model,
        ):
            if event["type"] == "message":
                result = event["message"]
                messages.append(result)
                if isinstance(result, AIMessage) and result.tool_calls:
                    names = ", ".join(call["name"] for call in result.tool_calls)
                    progress.info(f"도구 실행 중: {names}")
                elif isinstance(result, ToolMessage):
                    progress.info(f"도구 결과 확인: {result.name}")
            elif event["type"] == "done":
                completed = not event["stopped"]
    except Exception as error:
        pending_calls = {}
        for previous in messages:
            if isinstance(previous, AIMessage):
                pending_calls.update({call["id"]: call["name"] for call in previous.tool_calls})
            elif isinstance(previous, ToolMessage):
                pending_calls.pop(previous.tool_call_id, None)
        for call_id, name in pending_calls.items():
            messages.append(ToolMessage(
                content="[Error] 실행 중단으로 도구 호출이 취소되었습니다.",
                name=name, tool_call_id=call_id, status="error",
            ))
        messages.append(AIMessage(content=f"코딩 에이전트를 실행하지 못했습니다: {type(error).__name__}: {error}"))
    finally:
        progress.empty()
    if phase == "plan" and completed and messages and isinstance(messages[-1], AIMessage):
        workspace_state["pending_plan"] = bool(messages[-1].content) and not messages[-1].tool_calls
    st.rerun()


def render_coding_page() -> None:
    st.session_state.setdefault("coding_workspace_root", "")
    st.session_state.setdefault("coding_workspace_states", {})
    st.session_state.setdefault("coding_messages", [])
    model_keys = list(MODEL_PROFILES)
    default_model = DEFAULT_PROFILE if DEFAULT_PROFILE in model_keys else next(iter(model_keys), "")
    st.session_state.setdefault("coding_selected_model", default_model)
    if model_keys and st.session_state.coding_selected_model not in model_keys:
        st.session_state.coding_selected_model = default_model

    with st.sidebar:
        st.divider()
        st.subheader("📁 워크스페이스 설정")
        if model_keys:
            st.selectbox(
                "코딩 모델",
                options=model_keys,
                format_func=lambda key: f"{key} ({MODEL_PROFILES[key].get('name', '')})",
                key="coding_selected_model",
            )
        else:
            st.warning("사용 가능한 모델 프로필이 없습니다.")

        workflow = st.radio("워크플로우", [PLAN_WORKFLOW, AUTO_WORKFLOW], key="coding_workflow")

        workspace_input = st.text_input(
            "작업 프로젝트 폴더 (Workspace Root)",
            value=st.session_state.coding_workspace_root,
            placeholder=r"F:\MyProject",
            key="coding_workspace_input",
        )
        if st.button("폴더 적용", use_container_width=True):
            try:
                if not workspace_input.strip():
                    raise ValueError("폴더 경로를 입력해 주세요.")
                workspace = Path(workspace_input.strip()).expanduser().resolve()
                if not workspace.is_dir():
                    raise ValueError("존재하는 폴더 경로를 입력해 주세요.")
                st.session_state.coding_workspace_root = str(workspace)
                st.success(f"프로젝트 폴더 적용됨: {workspace.name or workspace.anchor}")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"작업 폴더를 적용할 수 없습니다: {error}")

        workspace_root = st.session_state.coding_workspace_root
        workspace_valid = bool(workspace_root) and Path(workspace_root).is_dir()
        if workspace_valid:
            states = st.session_state.coding_workspace_states
            if workspace_root not in states:
                states[workspace_root] = {
                    "messages": [], "pending_plan": False,
                    "snapshot_manager": WorkspaceSnapshotManager(workspace_root),
                }
            workspace_state = states[workspace_root]
            st.session_state.coding_messages = workspace_state["messages"]
            st.caption(f"현재 프로젝트: {Path(workspace_root).name or Path(workspace_root).anchor}")
            with st.expander("프로젝트 구조", expanded=True):
                try:
                    st.code(list_project_tree(workspace_root), language=None)
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"프로젝트 구조를 읽을 수 없습니다: {error}")
            try:
                diff_text = workspace_state["snapshot_manager"].get_unified_diff()
                if st.button("↩️ 이번 턴 변경사항 되돌리기 (Undo)", disabled=not diff_text, use_container_width=True) and diff_text:
                    restored = workspace_state["snapshot_manager"].undo_last_turn()
                    st.success("복원된 파일: " + ", ".join(restored))
                    diff_text = workspace_state["snapshot_manager"].get_unified_diff()
                with st.expander("📝 현재 턴 변경 Diff 보기"):
                    if diff_text:
                        st.code(diff_text, language="diff")
                    else:
                        st.caption("현재 턴의 변경사항이 없습니다.")
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"변경사항을 확인하거나 되돌릴 수 없습니다: {error}")
        elif workspace_root:
            st.error("설정된 작업 폴더를 찾을 수 없습니다. 다른 폴더를 적용해 주세요.")

    st.title("💻 로컬 sLLM 코딩 워크스페이스")
    if not workspace_valid:
        st.info("사이드바에서 작업할 프로젝트 폴더를 먼저 지정해 주세요.")
        return

    st.success(f"연결된 샌드박스: {workspace_root}")
    if st.button("대화 초기화"):
        workspace_state["messages"].clear()
        workspace_state["pending_plan"] = False

    for message in workspace_state["messages"]:
        if isinstance(message, HumanMessage):
            with st.chat_message("user"):
                st.markdown(message.content)
        elif isinstance(message, AIMessage):
            with st.chat_message("assistant"):
                if message.content:
                    st.markdown(message.content)
                for call in message.tool_calls:
                    st.caption(f"도구 호출: {call['name']}")
        elif isinstance(message, ToolMessage):
            with st.expander(f"도구 결과: {message.name}"):
                st.text(message.content)

    if workspace_state["pending_plan"]:
        if st.button("✅ 계획 승인 및 코드 수정 실행", type="primary"):
            _run_turn(workspace_root, workspace_state, "act", "위 계획을 승인합니다. 계획에 따라 코드를 수정해 주세요.")

    user_input = st.chat_input("코딩 작업을 입력하세요", disabled=not bool(model_keys))
    if isinstance(user_input, str) and user_input.strip():
        phase = "act" if workflow == AUTO_WORKFLOW else "plan"
        _run_turn(workspace_root, workspace_state, phase, user_input)