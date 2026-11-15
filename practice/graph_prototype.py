import sqlite3
import time
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from tools.browser import search_browser_history as raw_search_browser_history


@tool
def search_browser_history(keyword: str = "", days: int = 7, limit: int = 5) -> str:
    """Chrome 브라우저 방문 기록을 조회합니다."""
    return raw_search_browser_history(keyword=keyword, days=days, limit=limit)


browser_tools = [search_browser_history]
router_llm = ChatOllama(model="소형 모델", temperature=0.0)
summarizer_llm = ChatOllama(model="소형 모델", temperature=0.2)
main_llm = ChatOllama(model="메인 모델", temperature=0.3)
browser_llm = main_llm.bind_tools(browser_tools)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    route: str
    summary: str


def router_node(state: AgentState) -> dict:
    last_user_msg = state["messages"][-1].content
    prompt = [
        SystemMessage(
            content=(
                "사용자의 요청 의도를 분류하세요.\n"
                "- 크롬, 브라우저, 방문 기록, 히스토리, 들어간 사이트 조회를 요청하면: 'browser'\n"
                "- 그 외 모든 일반 잡담, 인사, 지식 질문은: 'chat'\n\n"
                "[예시]\n"
                "Q: 최근 방문기록 봐줘 -> browser\n"
                "Q: 크롬 사이트 목록 확인해줄래? -> browser\n"
                "Q: 오늘 날씨 어때? -> chat\n\n"
                "설명 없이 오직 'browser' 또는 'chat' 단어 하나만 출력하세요."
            )
        ),
        HumanMessage(content=last_user_msg),
    ]
    decision = router_llm.invoke(prompt).content.strip().lower()
    selected_route = "browser" if "browser" in decision else "chat"
    return {"route": selected_route}


def chat_node(state: AgentState) -> dict:
    response = main_llm.invoke(state["messages"])
    return {"messages": [response]}


def browser_agent_node(state: AgentState) -> dict:
    response = browser_llm.invoke(state["messages"])
    return {"messages": [response]}


def route_decision(state: AgentState) -> Literal["chat_node", "browser_agent_node"]:
    return "browser_agent_node" if state["route"] == "browser" else "chat_node"


def summarize_node(state: AgentState) -> dict:
    print("\n[요약 노드 (1.5B) 가동] 메시지가 기준치를 초과하여 이전 대화를 압축합니다...")
    t_start = time.perf_counter()
    existing_summary = state.get("summary", "")
    messages_to_summarize = state["messages"][:-2]

    dialogue_lines = []
    for msg in messages_to_summarize:
        role = "사용자" if isinstance(msg, HumanMessage) else "어시스턴트"
        dialogue_lines.append(f"{role}: {msg.content}")
    dialogue_text = "\n".join(dialogue_lines)

    summary_prompt = [
        HumanMessage(
            content=(
                f"기존 요약본: {existing_summary if existing_summary else '없음'}\n\n"
                f"[추가 대화 내용]\n{dialogue_text}\n\n"
                "기존 요약본과 추가 대화 내용을 종합하여 핵심 사실(사용자 정보, 작업 결과 등)을 2~3문장으로 한국어로 간결하게 요약해줘."
            )
        )
    ]

    res = summarizer_llm.invoke(summary_prompt)
    new_summary = res.content.strip()
    delete_actions = [
        RemoveMessage(id=msg.id)
        for msg in messages_to_summarize
        if hasattr(msg, "id") and msg.id
    ]

    elapsed = time.perf_counter() - t_start
    print(f"[요약 노드] 완료 ({elapsed:.2f}초 소요)\n새 요약본: {new_summary}")

    return {"summary": new_summary, "messages": delete_actions}


def should_summarize(state: AgentState) -> Literal["summarize_node", "__end__"]:
    if len(state["messages"]) > 6:
        return "summarize_node"
    return END


def browser_next_step(
    state: AgentState,
) -> Literal["browser_tools", "summarize_node", "__end__"]:
    last_msg = state["messages"][-1]
    if getattr(last_msg, "tool_calls", None):
        return "browser_tools"
    return should_summarize(state)


def build_graph(checkpointer):
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("chat_node", chat_node)
    builder.add_node("browser_agent_node", browser_agent_node)
    builder.add_node("browser_tools", ToolNode(browser_tools))
    builder.add_node("summarize_node", summarize_node)
    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_decision,
        {"chat_node": "chat_node", "browser_agent_node": "browser_agent_node"},
    )
    builder.add_conditional_edges(
        "browser_agent_node",
        browser_next_step,
        {
            "browser_tools": "browser_tools",
            "summarize_node": "summarize_node",
            END: END,
        },
    )
    builder.add_edge("browser_tools", "browser_agent_node")
    builder.add_conditional_edges(
        "chat_node",
        should_summarize,
        {"summarize_node": "summarize_node", END: END},
    )
    builder.add_edge("summarize_node", END)
    return builder.compile(checkpointer=checkpointer)


def main() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        graph = build_graph(SqliteSaver(conn))
        print("=" * 60)
        print("랭그래프 요약 노드 & 상태 확인(/state) 테스트")
        print("명령어:")
        print("  /state   : 현재 세션의 누적 메시지 수와 요약본 출력")
        print("  exit     : 종료")
        print("=" * 60)

        config = {"configurable": {"thread_id": "session_room_1"}}
        while True:
            try:
                user_input = input("\n[User]: ").strip()
                if not user_input or user_input.lower() in ("exit", "quit", "q"):
                    break

                if user_input == "/state":
                    current_state = graph.get_state(config)
                    values = current_state.values
                    msgs = values.get("messages", [])
                    summary = values.get("summary", "(요약본 아직 없음)")
                    print("\n" + "=" * 40)
                    print(f"[현재 상태 장부] 누적 메시지 개수: {len(msgs)}개")
                    print(f"[저장된 요약본]:\n{summary}")
                    print("=" * 40)
                    continue

                output = graph.invoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config,
                )
                print(f"\n[AI]: {output['messages'][-1].content}")
            except KeyboardInterrupt:
                break
    finally:
        conn.close()


if __name__ == "__main__":
    main()