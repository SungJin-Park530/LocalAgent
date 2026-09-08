# 스트림릿 연결 엔진 모듈
import os
import json
import re
import ollama
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES

def clean_model_output(text: str) -> str:
    if not text:
        return ""
    
    # 1. <think> 태그 제거
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"</?think>", "", cleaned).strip()
    
    target_text = cleaned if cleaned else re.sub(r"</?think>", "", text).strip()
    
    # 2. 동일/유사 문단 자가 반복 방어
    # 빈 줄 기준으로 문단 분리 후, 모델이 혼자서 가상 턴을 진행한 경우 첫 답변 영역 추출
    paragraphs = [p.strip() for p in target_text.split("\n\n") if p.strip()]
    if len(paragraphs) >= 2:
        # 모델이 "User:" 등을 가상으로 생성하며 혼자 북치고 장구친 경우 차단
        first_p = paragraphs[0]
        for fake_role in ["User:", "Human:", "사용자:", "Assistant:"]:
            if fake_role in target_text:
                target_text = target_text.split(fake_role)[0].strip()
                break

    return target_text

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

        # 4) 빈 본문 방어 (기존 방식 유지하되 명확히 처리)
        if not content_text:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                yield {"type": "text", "content": "(답변을 생성하지 못했습니다.)"}
                break

            if tools and message.get("tool_calls"):
                messages.append({
                    "role": "user",
                    "content": "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
                })
                continue
            else:
                # 첫 턴에 생각만 뱉고 본문을 안 썼을 때 정상적으로 답변을 유도
                messages.append({
                    "role": "user",
                    "content": "생각을 마쳤으면 네 말투 그대로 이어서 자연스럽게 답변해줘."
                })
                continue

        # 유효한 본문이 나왔을 때만 단 1회 yield 하고 루프 탈출
        yield {"type": "text", "content": content_text}
        break