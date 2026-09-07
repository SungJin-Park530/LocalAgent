# 스트림릿 연결 엔진 모듈
import os
import json
import re
import ollama
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES

def clean_model_output(text: str) -> str:
    """<think>...</think> 태그 블록 및 잔여 태그를 걷어냅니다."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?think>", "", text)
    return text.strip()

def load_system_prompts(prompts_dir: str = "prompts") -> str:
    """원본 agent.py와 완전히 동일하게 모든 md 파일을 순서대로 로드합니다."""
    if not os.path.exists(prompts_dir):
        return ""
    md_files = sorted([f for f in os.listdir(prompts_dir) if f.endswith(".md")])
    combined = []
    for file_name in md_files:
        path = os.path.join(prompts_dir, file_name)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                combined.append(content)
    return "\n\n---\n\n".join(combined)

# 시스템 프롬프트 캐싱
SYSTEM_PROMPT = load_system_prompts()

def run_agent_engine(user_message: str, history: list, mode: str, profile_key: str):
    profile = MODEL_PROFILES[profile_key]
    model_name = profile["name"]
    # 원본처럼 세팅 파일의 options를 손대지 않고 온전히 전달
    options = profile.get("options", {})

    # 메시지 리스트 구축
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    # 원본 agent.py 루프 1:1 재현
    while True:
        # 원본과 동일하게 tools=ALL_SCHEMAS를 항상 공급하여 챗 템플릿 일관성 유지
        response = ollama.chat(
            model=model_name,
            messages=messages,
            tools=ALL_SCHEMAS,
            options=options
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

                if func_name == "write_file" and just_exported_file and args.get("path") == just_exported_file:
                    continue

                call_signature = f"{func_name}_{json.dumps(args, sort_keys=True)}"
                if call_signature in executed_calls:
                    continue
                executed_calls.add(call_signature)

                yield {"type": "tool_start", "name": func_name, "args": args}
                tool_result = execute_tool(func_name, args)

                if func_name == "export_search_results_to_file" and tool_result.get("success"):
                    just_exported_file = args.get("dest_path")

                yield {"type": "tool_end", "name": func_name, "result": tool_result}

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
            continue

        # 2. 텍스트 본문 추출 및 <think> 정제
        raw_content = message.get("content", "")
        content_text = clean_model_output(raw_content)

        # 3. 폴백 도구 호출 (JSON 직접 파싱)
        if content_text.startswith("{") and content_text.endswith("}") and "name" in content_text:
            try:
                fallback_call = json.loads(content_text)
                func_name = fallback_call.get("name")
                args = fallback_call.get("arguments", {})

                if func_name:
                    yield {"type": "tool_start", "name": func_name, "args": args}
                    tool_result = execute_tool(func_name, args)
                    yield {"type": "tool_end", "name": func_name, "result": tool_result}

                    messages.append({
                        "role": "tool",
                        "content": json.dumps(tool_result, ensure_ascii=False)
                    })
                    continue
            except json.JSONDecodeError:
                pass

        # 4. 원본 agent.py와 동일한 후처리 (본문이 비었을 때 답변 유도)
        if not content_text:
            messages.append({
                "role": "user",
                "content": "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
            })
            continue

        # 최종 텍스트 UI 반환
        yield {"type": "text", "content": content_text}
        break