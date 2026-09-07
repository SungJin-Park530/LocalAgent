# 스트림릿 연결 엔진 모듈
import os
import json
import re
import ollama
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES

def clean_model_output(text: str) -> str:
    """
    <think>...</think> 태그 블록을 걷어내되, 
    본문 없이 think 블록만 생성된 경우에는 내부 내용을 살려냅니다.
    """
    if not text:
        return ""
    
    # 1. 정상적으로 닫힌 <think>...</think> 뒤에 실제 본문이 있는 경우 정제
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"</?think>", "", cleaned).strip()
    
    if cleaned:
        return cleaned

    # 2. 본문이 없고 모델이 <think> 내부에만 내용을 채운 경우 태그만 떼고 구출
    fallback_text = re.sub(r"</?think>", "", text).strip()
    return fallback_text

def build_system_prompt(selected_files: list[str], prompts_dir: str = "prompts") -> str:
    """주어진 프롬프트 파일 목록을 읽어 하나의 시스템 프롬프트로 결합합니다."""
    combined = []
    for file_name in selected_files:
        path = os.path.join(prompts_dir, file_name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    combined.append(content)
    return "\n\n---\n\n".join(combined)

def run_agent_engine(
    user_message: str,
    history: list,
    prompt_files: list[str],
    tools: list[dict] | None,
    profile_key: str
):
    profile = MODEL_PROFILES[profile_key]
    model_name = profile["name"]
    options = profile.get("options", {})

    system_prompt = build_system_prompt(prompt_files)

    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    retry_count = 0
    MAX_RETRIES = 2

    while True:
        chat_kwargs = {
            "model": model_name,
            "messages": messages,
            "options": options
        }
        if tools:
            chat_kwargs["tools"] = tools

        response = ollama.chat(**chat_kwargs)
        message = response["message"]
        messages.append(message)

        # 1) 정식 tool_calls 처리
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

        # 2) 텍스트 본문 추출 및 정제 (구출 로직 반영)
        raw_content = message.get("content", "")
        content_text = clean_model_output(raw_content)

        # 3) 폴백 JSON 도구 호출
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

        # 4) 빈 본문 방어 및 최대 재시도 제어
        if not content_text:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                yield {"type": "text", "content": "응답을 구성하는 데 실패했습니다. 다시 말씀해 주세요."}
                break

            if tools:
                messages.append({
                    "role": "user",
                    "content": "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
                })
                continue
            else:
                messages.append({
                    "role": "user",
                    "content": "생각을 마쳤으면 네 말투 그대로 이어서 자연스럽게 답변해줘."
                })
                continue

        # 최종 텍스트 UI 반환
        yield {"type": "text", "content": content_text}
        break