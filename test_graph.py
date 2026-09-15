from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

# 1. State 정의: 대화 요약본(summary) 필드 추가
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    summary: str
    step_count: int

llm = ChatOllama(model="사용중인 모델명을 적어주세요", temperature=0.3)

# 2. 챗봇 노드
def chatbot_node(state: AgentState) -> dict:
    print(f"\n[챗봇 노드] 현재 메시지 수: {len(state['messages'])}")
    response = llm.invoke(state["messages"])
    return {
        "messages": [response],
        "step_count": state.get("step_count", 0) + 1
    }

# 3. 요약 노드 (오래된 대화를 압축하는 역할)
def summarizer_node(state: AgentState) -> dict:
    print(f"\n[요약 노드 가동] 메시지가 기준치를 넘어 대화를 압축합니다.")
    
    # 1. 이전 대화 내역을 읽기 쉬운 텍스트로 직렬화
    dialogue_text = ""
    for msg in state["messages"]:
        role = "사용자" if isinstance(msg, HumanMessage) else "어시스턴트"
        dialogue_text += f"{role}: {msg.content}\n"

    # 2. 마지막에 HumanMessage로 요약 명령 전달
    prompt = [
        HumanMessage(content=f"다음 대화 내용을 한국어 1문장으로 핵심만 요약해줘.\n\n[대화 내용]\n{dialogue_text}")
    ]
    
    summary_response = llm.invoke(prompt)
    new_summary = summary_response.content.strip()
    
    return {
        "summary": new_summary
    }

# 4. 조건부 라우팅 함수 (어느 노드로 갈지 결정하는 판별기)
def should_summarize(state: AgentState) -> Literal["summarizer", "__end__"]:
    # 메시지가 2개(사용자 질문 1 + 모델 답변 1)를 초과하면 요약 노드로 분기
    # 테스트를 위해 기준을 2개로 낮게 잡았습니다.
    if len(state["messages"]) > 2:
        return "summarizer"
    return END

# 5. Graph 조립
builder = StateGraph(AgentState)

builder.add_node("chatbot", chatbot_node)
builder.add_node("summarizer", summarizer_node)

builder.add_edge(START, "chatbot")

# chatbot 노드가 끝난 뒤 조건부 분기 실행
builder.add_conditional_edges(
    "chatbot",
    should_summarize,
    {
        "summarizer": "summarizer",
        END: END
    }
)
builder.add_edge("summarizer", END)

graph = builder.compile()

# ---------------------------------------------------------
# 6. 실행 테스트: 질문을 연달아 2개 넣어서 요약 분기를 타게 만들기
# ---------------------------------------------------------
if __name__ == "__main__":
    print("--- 랭그래프 조건부 분기(요약) 테스트 ---")
    
    initial_input = {
        "messages": [
            HumanMessage(content="안녕, 나는 사과와 바나나를 좋아해."),
            HumanMessage(content="내가 무슨 과일을 좋아한다고 했지?")
        ],
        "summary": "",
        "step_count": 0
    }
    
    final_output = graph.invoke(initial_input)
    
    print("\n--- 최종 결과 ---")
    print(f"[답변]: {final_output['messages'][-1].content}")
    print(f"[생성된 요약본]: {final_output.get('summary', '없음')}")