import sqlite3
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from engine import preflight_node, route_after_router


class PreflightState(TypedDict):
    messages: Annotated[list, add_messages]
    missing_tool: Optional[str]


def test_router_branches_to_preflight_only_when_tool_is_missing():
    assert route_after_router({"missing_tool": "files"}) == "preflight_node"
    assert route_after_router({"missing_tool": None}) == "agent"
    assert route_after_router({}) == "agent"


def test_preflight_returns_friendly_message_without_model_call():
    result = preflight_node({"missing_tool": "files"})

    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "[파일 탐색]" in result["messages"][0].content
    assert "사이드바 설정" in result["messages"][0].content


def test_preflight_graph_skips_agent_and_persists_streamed_message(tmp_path):
    agent_calls = []
    checkpoint_path = tmp_path / "preflight.sqlite"
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        builder = StateGraph(PreflightState)
        builder.add_node("router", lambda state: {"missing_tool": "browser"})
        builder.add_node("preflight_node", preflight_node)
        builder.add_node("agent", lambda state: agent_calls.append(True) or {})
        builder.add_edge(START, "router")
        builder.add_conditional_edges(
            "router",
            route_after_router,
            {"preflight_node": "preflight_node", "agent": "agent"},
        )
        builder.add_edge("preflight_node", END)
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=SqliteSaver(connection))
        config = {"configurable": {"thread_id": "preflight-test"}}

        events = list(
            graph.stream(
                {"messages": [HumanMessage(content="최근 방문 사이트") ]},
                config=config,
                stream_mode=["updates"],
            )
        )
        state = graph.get_state(config).values

        assert agent_calls == []
        assert any("preflight_node" in payload for _, payload in events)
        assert isinstance(state["messages"][-1], AIMessage)
        assert "[브라우저 기록]" in state["messages"][-1].content
    finally:
        connection.close()


def test_workflows_define_smart_pass_and_clarification_rules():
    files_workflow = open(
        "prompts/workflow/01_files_workflow.md", encoding="utf-8"
    ).read()
    browser_workflow = open(
        "prompts/workflow/02_browser_workflow.md", encoding="utf-8"
    ).read()

    assert "즉시 실행 (Smart Pass)" in files_workflow
    assert "어느 드라이브나 폴더에서 어떤 파일을 찾아드릴까요?" in files_workflow
    assert "즉시 실행 (Smart Pass)" in browser_workflow
    assert "키워드나 대략적인 시점" in browser_workflow