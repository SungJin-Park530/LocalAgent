import os
import sqlite3
import time
import json
from typing import Annotated, Generator, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# ---------------------------------------------------------
# 1. 설정 및 저장소 경로 초기화
# ---------------------------------------------------------
from config.settings import (
    BASE_DIR,
    DEFAULT_PROFILE,
    MODEL_PROFILES,
    DEFAULT_SUB_MODEL,
    PROMPTS_DIR,
    SYSTEM_PROMPTS_DIR,
)
from tools.browser import search_browser_history as raw_search_browser_history
from tools.chat_utils import (
    get_current_time as raw_get_current_time,
)
from tools.chat_utils import (
    get_current_weather as raw_get_current_weather,
)

# data 폴더 하위에 체크포인트 DB 격리
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CHECKPOINT_DB_PATH = os.path.join(DATA_DIR, "chat_checkpoints.db")


# ---------------------------------------------------------
# 2. 도구(Tools) 표준 래핑
# ---------------------------------------------------------
@tool
def get_current_time() -> dict:
    """현재 시스템의 날짜, 요일, 시간을 확인합니다."""
    return raw_get_current_time()


@tool
def get_current_weather(location: str = "Seoul") -> dict:
    """현재 위치(기본 Seoul 또는 입력받은 도시)의 날씨와 기온을 조회합니다."""
    return raw_get_current_weather(location=location)


@tool
def search_browser_history(
    keyword: str = "", days: int = 7, limit: int = 5
) -> str:
    """Chrome 브라우저 방문 기록을 조회합니다. 사용자가 들어간 웹사이트 내역이나 URL을 찾을 때 사용합니다."""
    return raw_search_browser_history(keyword=keyword, days=days, limit=limit)


# 기본 제공 도구 셋
ALL_AVAILABLE_TOOLS = [
    get_current_time,
    get_current_weather,
    search_browser_history,
]
TOOL_MAP = {t.name: t for t in ALL_AVAILABLE_TOOLS}


# ---------------------------------------------------------
# 3. 모델 및 상태(State) 정의
# ---------------------------------------------------------
router_llm = ChatOllama(model=DEFAULT_SUB_MODEL, temperature=0.0)
summarizer_llm = ChatOllama(
    model=DEFAULT_SUB_MODEL, temperature=0.2, num_predict=300
)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    route: str
    summary: str
    active_tools: list  # 방별로 부여된 도구 이름 리스트


# ---------------------------------------------------------
# 4. 그래프 노드 정의
# ---------------------------------------------------------
def router_node(state: AgentState) -> dict:
    """1.5B 경량 모델을 통한 초고속 의도 분류"""
    last_user_msg = state["messages"][-1].content

    router_prompt_path = os.path.join(SYSTEM_PROMPTS_DIR, "00_router.md")
    with open(router_prompt_path, "r", encoding="utf-8") as prompt_file:
        router_prompt = prompt_file.read()

    prompt = [
        SystemMessage(content=router_prompt),
        HumanMessage(content=last_user_msg),
    ]
    decision = router_llm.invoke(prompt).content.strip().lower()

    if "browser" in decision:
        route = "browser"
    elif "time" in decision:
        route = "time"
    elif "weather" in decision:
        route = "weather"
    else:
        route = "chat"

    return {"route": route}


def agent_node(
    state: AgentState, main_llm: ChatOllama, active_tool_instances: list, system_prompt: str
) -> dict:
    route = state.get("route", "chat")

    target_tools = []
    if route != "chat":
        for t in active_tool_instances:
            if (
                (route == "browser" and "browser" in t.name)
                or (route == "time" and "time" in t.name)
                or (route == "weather" and "weather" in t.name)
            ):
                target_tools.append(t)

    llm_runner = main_llm.bind_tools(target_tools) if target_tools else main_llm

    full_messages = (
        [SystemMessage(content=system_prompt)] if system_prompt else []
    ) + state["messages"]
    response = llm_runner.invoke(full_messages)

    if not response.tool_calls and not response.content.strip():
        retry_messages = full_messages + [
            HumanMessage(content="최종 답변만 간결하게 작성하세요.")
        ]
        response = llm_runner.invoke(retry_messages)

    # ---------------------------------------------------------
    # [방어 로직] 모델이 tool_calls 대신 텍스트로 JSON을 뱉었을 때 구제
    # ---------------------------------------------------------
    if not response.tool_calls and response.content.strip().startswith("{") and "name" in response.content:
        try:
            parsed = json.loads(response.content.strip())
            if "name" in parsed:
                # 랭체인 규격 tool_calls 딕셔너리로 강제 복원
                response.tool_calls = [{
                    "name": parsed["name"],
                    "args": parsed.get("arguments", {}),
                    "id": f"call_{int(time.time())}"
                }]
        except Exception:
            pass  # 단순 일반 대화 JSON 형태일 경우 무시
    # ---------------------------------------------------------

    return {"messages": [response]}


def summarize_node(state: AgentState) -> dict:
    """오래된 컨텍스트 요약 및 메시지 제거 (1.5B 처리)"""
    existing_summary = state.get("summary", "")
    messages_to_summarize = state["messages"][:-2]

    dialogue_lines = []
    for msg in messages_to_summarize:
        role = "사용자" if isinstance(msg, HumanMessage) else "어시스턴트"
        dialogue_lines.append(f"{role}: {msg.content}")
    dialogue_text = "\n".join(dialogue_lines)

    prompt = [
        HumanMessage(
            content=(
                f"기존 요약: {existing_summary if existing_summary else '없음'}\n\n"
                f"[대화 내용]\n{dialogue_text}\n\n"
                "위 대화의 핵심 맥락과 중요한 사실을 한국어 2~3문장으로 간결히 요약하세요."
            )
        )
    ]
    res = summarizer_llm.invoke(prompt)
    new_summary = res.content.strip()

    delete_actions = [
        RemoveMessage(id=m.id)
        for m in messages_to_summarize
        if hasattr(m, "id") and m.id
    ]
    return {"summary": new_summary, "messages": delete_actions}


def should_summarize(state: AgentState) -> Literal["summarize_node", "__end__"]:
    # 턴 수 관리 (메시지 10개 초과 시 압축)
    if len(state["messages"]) > 10:
        return "summarize_node"
    return END


def check_tool_or_summary(
    state: AgentState,
) -> Literal["tools", "summarize_node", "__end__"]:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return should_summarize(state)


# ---------------------------------------------------------
# 5. UI 호환 메인 러너 함수 (app.py 인터페이스 완벽 대응)
# ---------------------------------------------------------
def run_agent_engine(
    user_message: str,
    history: list,
    prompt_files: list,
    tools: list,
    profile_key: str = DEFAULT_PROFILE,
    room_context: dict = None,
) -> Generator[dict, None, None]:
    """Streamlit UI의 run_agent_engine 호출을 랭그래프로 변환 실행하는 제너레이터"""
    # 1. 모델 인스턴스 준비
    profile = MODEL_PROFILES.get(profile_key, MODEL_PROFILES[DEFAULT_PROFILE])
    model_name = profile.get("name")
    opts = profile.get("options", {})

    main_llm = ChatOllama(
        model=model_name,
        temperature=opts.get("temperature", 0.3),
        num_predict=opts.get("num_predict", 1024),
        num_ctx=opts.get("num_ctx", 16384),
        reasoning=opts.get("reasoning", False),
        repeat_penalty=1.15,  # 무한 반복 루프 억제
        stop=["<|im_end|>", "<|endoftext|>", "### Human:", "사용자:"],  # 발산 강제 차단
    )
    
    # 1.5 시스템 프롬프트 로더
    system_content = ""
    if prompt_files:
        for p_file in prompt_files:
            file_path = os.path.join(PROMPTS_DIR, p_file)
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    system_content += f.read() + "\n\n"

    # 2. 방에 장착된 도구 인스턴스 필터링
    allowed_names = [t.get("function", {}).get("name") for t in tools]
    active_tools = [
        TOOL_MAP[name] for name in allowed_names if name in TOOL_MAP
    ]

    # 3. 동적 그래프 빌드
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node(
        "agent",
        lambda s: agent_node(
            s, 
            main_llm=main_llm, 
            active_tool_instances=active_tools, 
            system_prompt=system_content.strip()
        ),
    )
    builder.add_node("tools", ToolNode(active_tools if active_tools else [get_current_time]))
    builder.add_node("summarize_node", summarize_node)
    
    # 1) 시작 진입점(Entrypoint) 연결 -> 에러 해결 핵심
    builder.add_edge(START, "router")

    # 2) 라우터 -> 에이전트
    builder.add_edge("router", "agent")

    # 3) 에이전트 완료 후 조건 분기 (도구 실행, 요약 노드, 종료)
    builder.add_conditional_edges(
        "agent",
        check_tool_or_summary,
        {
            "tools": "tools",
            "summarize_node": "summarize_node",
            END: END
        },
    )
    
    # 4) 도구 실행 후 다시 에이전트로 반환
    builder.add_edge("tools", "agent")

    # 5) 요약 완료 후 최종 종료
    builder.add_edge("summarize_node", END)

    # 4. 체크포인터 연결 (thread_id = 방 ID)
    # Streamlit 세션의 active_room_id 사용 (없을 시 room_default)
    room_id = (
        room_context.get("room_id", "room_default")
        if room_context
        else "room_default"
    )

    conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    memory = SqliteSaver(conn)
    graph = builder.compile(checkpointer=memory)

    config = {"configurable": {"thread_id": room_id}}

    # 시스템 프롬프트 및 사용자 메시지 조립
    # (필요시 prompt_files 내용을 읽어 SystemMessage로 결합 가능)
    input_messages = [HumanMessage(content=user_message)]

    try:
        events = graph.stream(
            {"messages": input_messages},
            config=config,
            stream_mode="updates"
        )

        for event in events:
            # [디버깅 로그 추가] 실제 어떤 노드 이벤트가 들어오는지 콘솔에 출력
            print(f"\n>>> [LangGraph Event 수신]: {event}")

            # 1. agent 노드에서 발생한 이벤트 처리
            if "agent" in event:
                msg = event["agent"]["messages"][-1]
                print(f">>> [Agent 노드 메시지 타입]: {type(msg)}, 내용: {msg.content}")

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield {"type": "tool_start", "name": tc["name"]}
                elif msg.content:
                    yield {"type": "text", "content": msg.content}

            # 2. tools 노드 처리
            elif "tools" in event:
                for tool_msg in event["tools"]["messages"]:
                    yield {
                        "type": "tool_end",
                        "name": getattr(tool_msg, "name", "tool"),
                        "result": tool_msg.content
                    }

    finally:
        conn.close()