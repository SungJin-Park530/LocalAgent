# 스트림릿 연결 엔진 모듈
import os
import json
import re
import ollama
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES, PROMPTS_DIR

def clean_model_output(text: str) -> str:
    if not text:
        return ""
    
    # 1. <think> 태그 제거
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"</?think>", "", cleaned).strip()
    
    target_text = cleaned if cleaned else re.sub(r"</?think>", "", text).strip()
    
    # 2. 동일/유사 문단 자가 반복 및 가상 롤 방어
    paragraphs = [p.strip() for p in target_text.split("\n\n") if p.strip()]
    if len(paragraphs) >= 2:
        for fake_role in ["User:", "Human:", "사용자:", "Assistant:"]:
            if fake_role in target_text:
                target_text = target_text.split(fake_role)[0].strip()
                break

    return target_text

def build_system_prompt(selected_files: list[str], room_context: dict = None) -> str:
    """프롬프트 파일들을 결합하고 세션 컨텍스트(메모리)를 하단에 동적 주입합니다."""
    combined = []
    for file_name in selected_files:
        path = os.path.join(PROMPTS_DIR, file_name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    combined.append(content)
    
    full_prompt = "\n\n---\n\n".join(combined)

    # [동적 컨텍스트 주입] 세션 메모리 상태가 있으면 프롬프트 끝머리에 부착
    if room_context:
        state_parts = []
        if room_context.get("cwd"):
            state_parts.append(f"기준 경로: {room_context['cwd']}")
        if room_context.get("last_keyword"):
            state_parts.append(f"직전 검색어: {room_context['last_keyword']}")
        if room_context.get("last_exported"):
            state_parts.append(f"최근 저장 파일: {room_context['last_exported']}")

        if state_parts:
            status_banner = "\n\n---\n\n[현재 작업 상태 | " + " | ".join(state_parts) + "]"
            full_prompt += status_banner

    return full_prompt

def _update_context_from_result(room_context: dict, func_name: str, args: dict, result: dict):
    """도구 실행 결과로 room_context 상태를 갱신하는 헬퍼 함수"""
    if not room_context or not isinstance(result, dict) or not result.get("success"):
        return

    # 1. 디렉터리/파일 탐색 성공 시 기준 작업 디렉터리(cwd) 동기화
    if func_name in ("search_folders", "search_files"):
        target_path = result.get("target_path")
        if target_path:
            room_context["cwd"] = target_path
        if args.get("keyword"):
            room_context["last_keyword"] = args.get("keyword")

    # 2. 파일 내보내기 성공 시 파일 정보 기록
    elif func_name == "export_search_results_to_file":
        saved_path = result.get("saved_path")
        if saved_path:
            room_context["last_exported"] = saved_path

def run_agent_engine(
    user_message: str,
    history: list,
    prompt_files: list[str],
    tools: list[dict] | None,
    profile_key: str,
    room_context: dict = None  # 세션 메모리 수신
):
    profile = MODEL_PROFILES[profile_key]
    model_name = profile["name"]
    options = profile.get("options", {})

    # 시스템 프롬프트 조립 (context 동적 반영)
    system_prompt = build_system_prompt(prompt_files, room_context)

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
            just_exported_basename = None
            executed_calls = set()

            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                raw_args = tool_call["function"]["arguments"]
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                # 방금 내보낸 파일에 대해 write_file로 즉시 덮어쓰는 사고 방지 (파일명 기준 비교)
                if func_name == "write_file" and just_exported_basename:
                    req_path = args.get("path", "")
                    if os.path.basename(req_path) == just_exported_basename:
                        continue

                call_signature = f"{func_name}_{json.dumps(args, sort_keys=True)}"
                if call_signature in executed_calls:
                    continue
                executed_calls.add(call_signature)

                yield {"type": "tool_start", "name": func_name, "args": args}
                tool_result = execute_tool(func_name, args)

                # 컨텍스트 메모리 갱신
                _update_context_from_result(room_context, func_name, args, tool_result)

                if func_name == "export_search_results_to_file" and tool_result.get("success"):
                    saved_path = tool_result.get("saved_path", "")
                    just_exported_basename = os.path.basename(saved_path)

                yield {"type": "tool_end", "name": func_name, "result": tool_result}

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
            continue

        # 2) 텍스트 본문 추출 및 정제
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
                    _update_context_from_result(room_context, func_name, args, tool_result)
                    yield {"type": "tool_end", "name": func_name, "result": tool_result}

                    messages.append({
                        "role": "tool",
                        "content": json.dumps(tool_result, ensure_ascii=False)
                    })
                    continue
            except json.JSONDecodeError:
                pass

        # 4) 빈 본문 방어
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
                messages.append({
                    "role": "user",
                    "content": "생각을 마쳤으면 네 말투 그대로 이어서 자연스럽게 답변해줘."
                })
                continue

        # 유효한 본문이 나왔을 때 단 1회 yield 하고 루프 탈출
        yield {"type": "text", "content": content_text}
        break