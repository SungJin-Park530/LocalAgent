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
    if not os.path.exists(file_path):
        example_path = file_path + ".example"
        raise FileNotFoundError(
            f"\n[오류] '{file_path}' 파일이 없습니다.\n"
            f"'{example_path}' 파일을 복사하여 '{file_path}'로 이름을 바꾼 후 페르소나를 설정해 주세요!"
        )
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

        # 1. 정식 tool_calls 처리
        if message.get("tool_calls"):
            
            just_exported_file = None
            executed_calls = set()
            
            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                raw_args = tool_call["function"]["arguments"]
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                
                # 검색 결과 저장 이후 write_file로 덮어쓰려 한다면 스킵한다
                if func_name == "write_file" and just_exported_file and args.get("path") == just_exported_file:
                    print(f"\n[알림] 방금 export된 파일을 덮어쓰려 하므로 스킵합니다: {just_exported_file}")
                    continue
                
                # 완전히 동일한 도구 연속 호출 차단
                call_signature = f"{func_name}_{json.dumps(args, sort_keys=True)}"
                if call_signature in executed_calls:
                    continue
                executed_calls.add(call_signature)
                
                print(f"\n[Tool Call] {func_name}({args})")
                tool_result = execute_tool(func_name, args)
                
                if func_name == "export_search_results_to_file" and tool_result.get("success"):
                    just_exported_file = args.get("dest_path")

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
            continue

        # 2. 텍스트 본문 추출
        content_text = message.get("content", "").strip()

        # 만약 모델이 JSON 형태로 툴 호출을 텍스트로 보냈을 때의 폴백
        if content_text.startswith("{") and "name" in content_text and "arguments" in content_text:
            try:
                fallback_call = json.loads(content_text)
                func_name = fallback_call.get("name")
                args = fallback_call.get("arguments", {})

                print(f"\n[Fallback Tool Call] {func_name}({args})")
                tool_result = execute_tool(func_name, args)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
                continue
            except json.JSONDecodeError:
                pass

        # 3. 모델이 content를 비운 채 종료했을 때 방어
        if not content_text:
            print("\n[알림] 모델 추론 완료 후 응답 텍스트를 구성 중입니다...")
            messages.append({
                "role": "user", 
                "content": "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
            })
            continue

        print(f"\nAgent: {content_text}\n")
        break