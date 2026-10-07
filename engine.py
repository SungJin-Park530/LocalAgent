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
    message_chunk_to_message,
)
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.prebuilt import ToolNode

# ---------------------------------------------------------
# 1. 설정 및 저장소 경로 초기화
# ---------------------------------------------------------
from config.settings import (
    BASE_DIR,
    CONTEXT_SUMMARY_THRESHOLD,
    DEFAULT_PROFILE,
    MODEL_PROFILES,
    DEFAULT_SUB_MODEL,
    PROMPTS_DIR,
    SYSTEM_PROMPTS_DIR,
)
from tools import TOOL_REGISTRY

THINKING_SOFT_CAP_CHARS = 4000

# data 폴더 하위에 체크포인트 DB 격리
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CHECKPOINT_DB_PATH = os.path.join(DATA_DIR, "chat_checkpoints.db")


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

    router_prompt_path = os.path.join(SYSTEM_PROMPTS_DIR, "01_router.md")
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

    route_tool_names = {
        "browser": "search_browser_history",
        "time": "get_current_time",
        "weather": "get_current_weather",
    }
    routed_tool_name = route_tool_names.get(route)
    target_tools = (
        [t for t in active_tool_instances if t.name == routed_tool_name]
        if routed_tool_name
        else active_tool_instances
    )

    llm_runner = main_llm.bind_tools(target_tools) if target_tools else main_llm

    full_messages = (
        [SystemMessage(content=system_prompt)] if system_prompt else []
    ) + state["messages"]

    def stream_response(messages: list) -> AIMessage:
        response_chunk = None
        for chunk in llm_runner.stream(messages):
            response_chunk = (
                chunk if response_chunk is None else response_chunk + chunk
            )
        if response_chunk is None:
            return AIMessage(content="")
        return message_chunk_to_message(response_chunk)

    response = stream_response(full_messages)

    if not response.tool_calls and not response.content.strip():
        retry_messages = full_messages + [
            HumanMessage(content="최종 답변만 간결하게 작성하세요.")
        ]
        response = stream_response(retry_messages)

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
    recent_messages = state["messages"][-4:]
    messages_to_summarize = state["messages"][:-4]

    dialogue_lines = []
    for msg in messages_to_summarize:
        if isinstance(msg, HumanMessage):
            role = "사용자"
        elif isinstance(msg, ToolMessage):
            role = f"도구({msg.name})"
        else:
            role = "어시스턴트"
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

    return {
        "summary": new_summary,
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
        + [SystemMessage(content=f"이전 대화 요약: {new_summary}")]
        + recent_messages,
    }


def should_summarize(
    state: AgentState, token_model: ChatOllama
) -> Literal["summarize_node", "__end__"]:
    messages = state["messages"]
    try:
        token_count = token_model.get_num_tokens_from_messages(messages)
    except Exception:
        total_chars = sum(
            len(str(getattr(message, "content", "")))
            + len(str(getattr(message, "tool_calls", "")))
            for message in messages
        )
        token_count = int(total_chars / 2.5)

    print(
        f"[컨텍스트 감시] 메시지 {len(messages)}개 | "
        f"누적 {token_count} 토큰 | 임계치 {CONTEXT_SUMMARY_THRESHOLD} 토큰"
    )
    if token_count > CONTEXT_SUMMARY_THRESHOLD:
        return "summarize_node"
    return END


def check_tool_or_summary(
    state: AgentState, token_model: ChatOllama
) -> Literal["tools", "summarize_node", "__end__"]:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return should_summarize(state, token_model)


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
        num_predict=opts.get("num_predict", 8192),
        num_ctx=opts.get("num_ctx", 16384),
        reasoning=opts.get("reasoning", True),
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
    allowed_names = {
        tool_schema.get("function", {}).get("name")
        for tool_schema in tools
    }
    active_tools = [
        TOOL_REGISTRY[name]
        for name in allowed_names
        if name in TOOL_REGISTRY
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
    builder.add_node("tools", ToolNode(active_tools))
    builder.add_node("summarize_node", summarize_node)
    
    # 1) 시작 진입점(Entrypoint) 연결 -> 에러 해결 핵심
    builder.add_edge(START, "router")

    # 2) 라우터 -> 에이전트
    builder.add_edge("router", "agent")

    # 3) 에이전트 완료 후 조건 분기 (도구 실행, 요약 노드, 종료)
    builder.add_conditional_edges(
        "agent",
        lambda state: check_tool_or_summary(state, main_llm),
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
            stream_mode=["messages", "updates"],
        )

        in_thinking = False
        thinking_chars_count = 0
        thinking_limit_reached = False
        marker_buffer = ""

        def emit_content(content: str) -> list[dict]:
            nonlocal in_thinking, thinking_chars_count, thinking_limit_reached
            if not content:
                return []
            if not in_thinking or thinking_limit_reached:
                return [{"type": "text_chunk", "content": content}]

            remaining = THINKING_SOFT_CAP_CHARS - thinking_chars_count
            thinking_part = content[:remaining]
            events_out = []
            if thinking_part:
                thinking_chars_count += len(thinking_part)
                events_out.append({"type": "thinking_chunk", "content": thinking_part})

            overflow = content[len(thinking_part):]
            if overflow:
                in_thinking = False
                thinking_limit_reached = True
                events_out.append({
                    "type": "thinking_limit_reached",
                    "content": "\n[추론 상한 도달: 답변 전환]\n",
                })
                events_out.append({"type": "text_chunk", "content": overflow})
            return events_out

        def consume_text(content: str, flush: bool = False) -> list[dict]:
            nonlocal in_thinking, marker_buffer, thinking_limit_reached
            marker_buffer += content
            emitted = []
            markers = ("<think>", "</think>")

            while marker_buffer:
                matches = [
                    (marker_buffer.find(marker), marker)
                    for marker in markers
                    if marker_buffer.find(marker) >= 0
                ]
                if matches:
                    marker_index, marker = min(matches, key=lambda item: item[0])
                    emitted.extend(emit_content(marker_buffer[:marker_index]))
                    marker_buffer = marker_buffer[marker_index + len(marker):]
                    if marker == "<think>" and not thinking_limit_reached:
                        in_thinking = True
                    elif marker == "</think>":
                        in_thinking = False
                    continue

                if flush:
                    emitted.extend(emit_content(marker_buffer))
                    marker_buffer = ""
                    break

                partial_length = max(
                    (
                        length
                        for marker in markers
                        for length in range(1, len(marker))
                        if marker_buffer.endswith(marker[:length])
                    ),
                    default=0,
                )
                if partial_length:
                    safe_length = len(marker_buffer) - partial_length
                    emitted.extend(emit_content(marker_buffer[:safe_length]))
                    marker_buffer = marker_buffer[safe_length:]
                    break
                else:
                    emitted.extend(emit_content(marker_buffer))
                    marker_buffer = ""

            return emitted

        for mode, payload in events:
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "agent":
                    continue
                token = chunk.content
                if isinstance(token, str):
                    for stream_event in consume_text(token):
                        yield stream_event
                elif isinstance(token, list):
                    for block in token:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            for stream_event in consume_text(block["text"]):
                                yield stream_event

            elif mode == "updates":
                if "agent" in payload:
                    msg = payload["agent"]["messages"][-1]
                    for tool_call in getattr(msg, "tool_calls", []):
                        yield {"type": "tool_start", "name": tool_call["name"]}
                elif "tools" in payload:
                    for tool_msg in payload["tools"]["messages"]:
                        yield {
                            "type": "tool_end",
                            "name": getattr(tool_msg, "name", "tool"),
                            "result": tool_msg.content,
                        }

        for stream_event in consume_text("", flush=True):
            yield stream_event

    finally:
        conn.close()