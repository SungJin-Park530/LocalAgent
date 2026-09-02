# agent 초기 기능구현
# 파일 목록 확인 기능 추가

# 중요 라이브러리 import
import os
import json
import ollama
from tools import ALL_SCHEMAS, execute_tool

MODEL = "qwen3:30b-a3b"
START_PROMPT = "Local Agent 시작 (종료: exit 또는 quit)\n" + "-" * 40
END_PROMPT = "Local Agent를 종료합니다."
EXIT_COMMANDS = ["exit", "quit"]

# 시스템 프롬프트 불러오기 함수
def load_system_prompt(file_path: str = "prompts/system.md") -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()

# messages 초기화 부분
system_prompt = load_system_prompt()
messages = [{"role": "system", "content": system_prompt}]

print(START_PROMPT)

while True:
    user_input = input("\nYou: ").strip()
    if not user_input:
        continue
    if user_input.lower() in EXIT_COMMANDS:
        print(END_PROMPT)
        break

    messages.append({"role": "user", "content": user_input})

    # 에이전트 작업 루프: 도구 호출이 끝날 때까지 자동 반복
    while True:
        response = ollama.chat(
            model=MODEL,
            messages=messages,
            tools=ALL_SCHEMAS
        )
        message = response["message"]
        messages.append(message)

        # 1. 모델이 정식 tool_calls를 반환한 경우
        if message.get("tool_calls"):
            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                raw_args = tool_call["function"]["arguments"]
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                print(f"[Tool Call] {func_name}({args})")

                tool_result = execute_tool(func_name, args)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
            # 도구 결과를 메시지에 넣었으므로, 다음 행동(추가 도구 호출 또는 최종 응답)을 위해 루프 계속 진행
            continue

        # 2. 모델이 tool_calls 대신 본문(content)에 JSON 형태로 툴 호출을 적었을 경우의 예외 처리 (Fallback)
        content_text = message.get("content", "").strip()
        if content_text.startswith("{") and "name" in content_text and "arguments" in content_text:
            try:
                fallback_call = json.loads(content_text)
                func_name = fallback_call.get("name")
                args = fallback_call.get("arguments", {})

                print(f"[Fallback Tool Call] {func_name}({args})")

                tool_result = execute_tool(func_name, args)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
                continue
            except json.JSONDecodeError:
                pass

        # 도구 호출이 더 이상 없고 일반 텍스트 답변이 완성된 경우 루프 탈출 및 출력
        print(f"\nAgent: {content_text}\n")
        break