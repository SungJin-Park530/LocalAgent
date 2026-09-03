# agent 초기 기능구현
# 파일 목록 확인 기능 추가

# 중요 라이브러리 import
import os
import json
import re
import ollama
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES, DEFAULT_PROFILE

# 사용 모델
active_profile = MODEL_PROFILES[DEFAULT_PROFILE]
current_profile_key = DEFAULT_PROFILE
current_model_name = active_profile["name"]
current_options = active_profile["options"]

START_PROMPT = "Local Agent 시작 (종료: exit 또는 quit)\n" + "-" * 40
END_PROMPT = "Local Agent를 종료합니다."
EXIT_COMMANDS = ["exit", "quit"]

# 모델 목록 표시
def show_model_list(current_key: str):
    """현재 모델 목록과 사용 중인 모델을 안내합니다."""
    print("\n" + "=" * 50)
    print(" [모델 프로필 목록]")
    for key, prof in MODEL_PROFILES.items():
        marker = "▶ (사용 중)" if key == current_key else "  "
        # model_name 또는 model 키를 안전하게 탐색
        name = prof.get("name")
        desc = prof.get("description", "설명 없음")
        print(f" {marker} [{key}] : {name} - {desc}")
    print("-" * 50)
    print(" 변경 방법: /model <이름>  (예: /model 14b)")
    print("=" * 50 + "\n")

# <think> 사고 과정 제거 헬퍼 함수
def clean_model_output(text: str) -> str:
    """<think>...</think> 태그 블록 및 잔여 태그를 걷어냅니다."""
    if not text:
        return ""
    # 닫힌 태그 전체 제거
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # 태그가 덜 닫혔거나 홀로 남은 </think>, <think> 찌꺼기 제거
    text = re.sub(r"</?think>", "", text)
    return text.strip()

# 시스템 프롬프트 불러오기 함수
def load_system_prompts(prompts_dir: str = "prompts") -> str:
    """prompts 폴더 내의 .md 파일들을 정렬하여 순서대로 결합합니다."""
    if not os.path.exists(prompts_dir):
        raise FileNotFoundError(f"[오류] 프롬프트 디렉터리를 찾을 수 없습니다: {prompts_dir}")

    md_files = sorted([f for f in os.listdir(prompts_dir) if f.endswith(".md")])
    if not md_files:
        example_files = [f for f in os.listdir(prompts_dir) if f.endswith(".md.example")]
        guide = "\n".join([f"- {f} -> {f.replace('.example', '')}" for f in example_files])
        raise FileNotFoundError(
            f"\n[오류] '{prompts_dir}' 폴더에 활성화된 .md 파일이 없습니다.\n"
            f"다음 예시 파일들을 복사하여 .md 파일을 생성해 주세요:\n{guide}"
        )

    combined = []
    for file_name in md_files:
        path = os.path.join(prompts_dir, file_name)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                combined.append(content)

    return "\n\n---\n\n".join(combined)

# 초기화
system_prompt = load_system_prompts()
messages = [{"role": "system", "content": system_prompt}]

print(START_PROMPT)

while True:
    user_input = input("\nYou: ").strip()
    if not user_input:
        continue
    if user_input.lower() in EXIT_COMMANDS:
        print(END_PROMPT)
        break
    
    # -- CLI 명령어 인터셉트 --
    if user_input.startswith("/"):
        parts = user_input.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if cmd == "/model":
            if not args:
                show_model_list(current_profile_key)
            else:
                target_key = args[0].lower()
                if target_key in MODEL_PROFILES:
                    current_profile_key = target_key
                    active_model = MODEL_PROFILES[target_key]
                    print(f"\n[시스템] 모델 전환 완료: [{target_key}] ({active_model['name']})")
                    print(f"[시스템] 적용 온도(Temperature): {active_model.get('temperature', 0.7)}")
                else:
                    print(f"\n[오류] 등록되지 않은 프로필입니다: '{target_key}'")
                    show_model_list(current_profile_key)
            continue  # 모델 호출 루프로 가지 않고 다음 You: 입력으로 복귀

        elif cmd == "/clear":
            # 대화 맥락 초기화 (시스템 프롬프트만 유지)
            messages = [{"role": "system", "content": system_prompt}]
            print("\n[시스템] 대화 히스토리가 초기화되었습니다.")
            continue

        else:
            print(f"\n[오류] 지원하지 않는 명령어입니다: {cmd} (사용 가능: /model, /clear)")
            continue
        # -- CLI 명령어 인터셉트 종료 --

    messages.append({"role": "user", "content": user_input})

    # 에이전트 작업 루프: 도구 호출이 끝날 때까지 자동 반복
    while True:
        response = ollama.chat(
            model=current_model_name,
            messages=messages,
            tools=ALL_SCHEMAS,
            options=current_options
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
                
                # 검색 결과 저장 직후 write_file로 덮어쓰기 시도 차단
                if func_name == "write_file" and just_exported_file and args.get("path") == just_exported_file:
                    print(f"\n[알림] 방금 export된 파일을 덮어쓰려 하므로 스킵합니다: {just_exported_file}")
                    continue
                
                # 동일 도구 연속 중복 호출 차단
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

        # 2. 텍스트 본문 추출 및 <think> 정제
        raw_content = message.get("content", "")
        content_text = clean_model_output(raw_content)

        # 3. 폴백 도구 호출 (정규 tool_call을 안 뱉고 오직 순수 JSON만 보냈을 때만 엄격하게 검증)
        if content_text.startswith("{") and content_text.endswith("}") and "name" in content_text:
            try:
                fallback_call = json.loads(content_text)
                func_name = fallback_call.get("name")
                args = fallback_call.get("arguments", {})

                if func_name:
                    print(f"\n[Fallback Tool Call] {func_name}({args})")
                    tool_result = execute_tool(func_name, args)

                    messages.append({
                        "role": "tool",
                        "content": json.dumps(tool_result, ensure_ascii=False)
                    })
                    continue
            except json.JSONDecodeError:
                pass

        # 4. 모델이 content를 비운 채 종료했을 때 방어
        if not content_text:
            print("\n[알림] 모델 추론 완료 후 응답 텍스트를 구성 중입니다...")
            messages.append({
                "role": "user", 
                "content": "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
            })
            continue

        print(f"\nAgent: {content_text}\n")
        break