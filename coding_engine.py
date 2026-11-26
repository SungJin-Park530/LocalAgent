from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from config.settings import DEFAULT_PROFILE, MODEL_PROFILES, PROMPTS_DIR
from tools.coding_workspace import (
    WorkspaceSnapshotManager,
    create_coding_read_tools,
    create_coding_write_tools,
    resolve_safe_path,
)


READ_TOOL_NAMES = frozenset({"list_project_tree", "get_repo_map", "read_file_lines", "search_in_project"})
WRITE_TOOL_NAMES = frozenset({"replace_in_file", "create_or_overwrite_file"})
MAX_FAILURES = 3
COMPACT_MIN_CHARS = 500


class CodingState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    consecutive_failures: int
    failure_details: list[str]
    stopped: bool


def _compact_coding_messages(messages: list[BaseMessage], recent_window: int = 6) -> list[BaseMessage]:
    boundary = max(0, len(messages) - max(recent_window, 0))
    compacted = []
    for index, message in enumerate(messages):
        if (index < boundary and isinstance(message, ToolMessage)
                and message.name in READ_TOOL_NAMES and isinstance(message.content, str)
                and len(message.content) > COMPACT_MIN_CHARS):
            message = message.model_copy(update={
                "content": f"[이전 탐색 결과 압축됨: {message.name} - 최신 내용이 필요하면 다시 조회하세요]"
            })
        compacted.append(message)
    return compacted


def _load_coding_prompt(phase: Literal["plan", "act"]) -> str:
    root = Path(PROMPTS_DIR) / "coding"
    names = ("01_coder_system.md", "02_plan_mode.md" if phase == "plan" else "03_act_mode.md")
    sections = []
    for name in names:
        path = root / name
        if not path.is_file():
            path = root / f"{name}.example"
        sections.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(sections)


def _failed_tool_result(name: str, result: str) -> bool:
    lowered = result.lower().strip()
    if lowered.startswith(("[error]", "[syntaxerror]", "error:", "실패:", "오류:", "차단:")):
        return True
    if name in WRITE_TOOL_NAMES:
        return any(marker in result for marker in (
            "수정할 수 없습니다", "치환할 수 없습니다", "다시 확인하세요", "더 포함해서 호출하세요",
            "이미 존재하는 파일입니다", "파일을 저장하지 않았습니다", "실패", "오류", "에러", "차단",
        )) or "error" in lowered or "failed" in lowered
    return False


def _make_model(profile_key: str):
    if profile_key not in MODEL_PROFILES:
        raise ValueError(f"Unknown model profile: {profile_key}")
    profile = MODEL_PROFILES[profile_key]
    options = profile.get("options", {})
    return ChatOllama(
        model=profile["name"], temperature=options.get("temperature", 0.3),
        num_predict=options.get("num_predict", 8192), num_ctx=options.get("num_ctx", 16384),
        reasoning=options.get("reasoning", True), repeat_penalty=options.get("repeat_penalty", 1.15),
    )


def run_coding_agent(
    messages: list[BaseMessage], workspace_root: str | Path, phase: Literal["plan", "act"],
    snapshot_manager: WorkspaceSnapshotManager, profile_key: str = DEFAULT_PROFILE, *, model=None,
):
    """Yield generated messages and a final event for one workspace-bound graph run."""
    if phase not in ("plan", "act"):
        raise ValueError(f"Unknown coding phase: {phase}")
    root = resolve_safe_path(workspace_root, ".")
    if snapshot_manager.workspace_root != root:
        raise ValueError("Snapshot manager belongs to a different workspace")
    tools = create_coding_read_tools(root)
    if phase == "act":
        tools += create_coding_write_tools(root, snapshot_manager)
    prompt = _load_coding_prompt(phase)
    runner = (model if model is not None else _make_model(profile_key)).bind_tools(tools)
    tool_by_name = {item.name: item for item in tools}

    def agent_node(state: CodingState):
        response = runner.invoke([SystemMessage(content=prompt), *_compact_coding_messages(state["messages"])])
        return {"messages": [response]}

    def tools_node(state: CodingState):
        outputs = []
        failures = state["consecutive_failures"]
        details = list(state["failure_details"])
        for call in state["messages"][-1].tool_calls:
            name = call["name"]
            if failures >= MAX_FAILURES:
                result, failed = "[Error] 연속 실패로 도구 실행이 중단되었습니다.", True
            else:
                try:
                    if name not in tool_by_name:
                        raise PermissionError(f"현재 단계에서 허용되지 않은 도구: {name}")
                    result = str(tool_by_name[name].invoke(call["args"]))
                    failed = _failed_tool_result(name, result)
                except Exception as error:
                    result, failed = f"[Error] {type(error).__name__}: {error}", True
                failures = failures + 1 if failed else 0
                details = (details + [f"{name}: {result}"])[-MAX_FAILURES:] if failed else []
            outputs.append(ToolMessage(content=result, name=name, tool_call_id=call["id"], status="error" if failed else "success"))
        return {"messages": outputs, "consecutive_failures": failures, "failure_details": details}

    def breaker_node(state: CodingState):
        summary = "도구 실행이 연속 3회 실패하여 중단했습니다. 시도 및 실패 원인:\n" + "\n".join(
            f"- {detail}" for detail in state["failure_details"]
        )
        return {"messages": [AIMessage(content=summary)], "stopped": True}

    graph = StateGraph(CodingState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("breaker", breaker_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", lambda state: "tools" if state["messages"][-1].tool_calls else END)
    graph.add_conditional_edges("tools", lambda state: "breaker" if state["consecutive_failures"] >= MAX_FAILURES else "agent")
    graph.add_edge("breaker", END)
    compiled = graph.compile()
    if phase == "act":
        snapshot_manager.begin_turn()
    stopped = False
    pending_calls = {}
    try:
        for update in compiled.stream(
            {"messages": list(messages), "consecutive_failures": 0, "failure_details": [], "stopped": False},
            config={"recursion_limit": 40}, stream_mode="updates",
        ):
            for node, values in update.items():
                stopped = stopped or values.get("stopped", False)
                for message in values.get("messages", []):
                    if isinstance(message, AIMessage):
                        pending_calls.update({call["id"]: call["name"] for call in message.tool_calls})
                    elif isinstance(message, ToolMessage):
                        pending_calls.pop(message.tool_call_id, None)
                    yield {"type": "message", "node": node, "message": message}
    except GraphRecursionError:
        stopped = True
        for call_id, name in pending_calls.items():
            yield {"type": "message", "node": "limit", "message": ToolMessage(
                content="[Error] 실행 단계 수 제한으로 도구 호출이 취소되었습니다.",
                name=name, tool_call_id=call_id, status="error",
            )}
        yield {"type": "message", "node": "limit", "message": AIMessage(content="실행 단계 수 제한에 도달하여 중단했습니다. 현재까지의 도구 결과와 변경사항을 확인하세요.")}
    yield {"type": "done", "phase": phase, "stopped": stopped}