# 스트림릿 연결 엔진 모듈
import os
import json
import re
import ollama
from typing import Any, Dict, List, Optional, Set, Tuple, Generator
from tools import ALL_SCHEMAS, execute_tool
from config.settings import MODEL_PROFILES, PROMPTS_DIR


# ---------------------------------------------------------------------------
# 1. 텍스트 정제 및 프롬프트 조립 헬퍼
# ---------------------------------------------------------------------------

def clean_model_output(text: str) -> str:
    """<think> 태그 제거 및 환각성 롤(User:, Human:) 자가 반복 차단"""
    if not text:
        return ""
    
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"</?think>", "", cleaned).strip()
    target_text = cleaned if cleaned else re.sub(r"</?think>", "", text).strip()
    
    paragraphs = [p.strip() for p in target_text.split("\n\n") if p.strip()]
    if len(paragraphs) >= 2:
        for fake_role in ["User:", "Human:", "사용자:", "Assistant:"]:
            if fake_role in target_text:
                target_text = target_text.split(fake_role)[0].strip()
                break

    return target_text


def build_system_prompt(selected_files: List[str], room_context: Optional[Dict] = None) -> str:
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


# ---------------------------------------------------------------------------
# 2. 도구 실행 및 상태 갱신 서브 루틴
# ---------------------------------------------------------------------------

def _update_context_from_result(room_context: Optional[Dict], func_name: str, args: Dict, result: Any):
    """도구 실행 결과로 room_context 상태를 갱신하는 헬퍼 함수"""
    if not room_context or not isinstance(result, dict) or not result.get("success"):
        return

    # 1. 탐색 계열 성공 시 기준 작업 디렉터리(cwd) 동기화
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


def _serialize_tool_result(result: Any) -> str:
    """도구 반환값(Dict 또는 문자열)을 LLM 메시지 주입용 문자열로 안전하게 변환"""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def _parse_fallback_tool_call(content_text: str) -> Optional[Tuple[str, Dict]]:
    """모델이 도구 스키마 대신 텍스트로 내뱉은 JSON 도구 호출을 파싱"""
    if not (content_text.startswith("{") and content_text.endswith("}") and "name" in content_text):
        return None
    try:
        data = json.loads(content_text)
        func_name = data.get("name")
        args = data.get("arguments", {})
        if func_name:
            return func_name, args
    except json.JSONDecodeError:
        pass
    return None


def _handle_empty_response(retry_count: int, max_retries: int, has_tools: bool) -> Tuple[bool, str]:
    """빈 응답 발생 시 재시도 가능 여부와 주입할 리트라이 프롬프트 반환"""
    if retry_count >= max_retries:
        return False, "(답변을 생성하지 못했습니다.)"

    if has_tools:
        retry_prompt = "방금 도구 실행 결과를 네 원래 말투와 캐릭터 성격 그대로 살려서 자연스럽게 보고해줘."
    else:
        retry_prompt = "생각을 마쳤으면 네 말투 그대로 이어서 자연스럽게 답변해줘."

    return True, retry_prompt


# ---------------------------------------------------------------------------
# 3. 메인 에이전트 엔진
# ---------------------------------------------------------------------------

def run_agent_engine(
    user_message: str,
    history: List[Dict],
    prompt_files: List[str],
    tools: Optional[List[Dict]],
    profile_key: str,
    room_context: Optional[Dict] = None
) -> Generator[Dict[str, Any], None, None]:
    profile = MODEL_PROFILES[profile_key]
    model_name = profile["name"]
    options = profile.get("options", {})

    # 시스템 프롬프트 조립
    system_prompt = build_system_prompt(prompt_files, room_context)

    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    retry_count = 0
    MAX_RETRIES = 2
    executed_signatures: Set[str] = set()
    just_exported_basename: Optional[str] = None

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

        # ---------------------------------------------------------
        # Case A: 정식 Tool Calls 감지 시
        # ---------------------------------------------------------
        if message.get("tool_calls"):
            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                raw_args = tool_call["function"]["arguments"]
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                # 방금 내보낸 파일을 write_file로 즉시 덮어쓰는 사고 방지
                if func_name == "write_file" and just_exported_basename:
                    req_path = args.get("path", "")
                    if os.path.basename(req_path) == just_exported_basename:
                        continue

                # 동일 턴 중복 호출 방지 서명
                call_sig = f"{func_name}_{json.dumps(args, sort_keys=True)}"
                if call_sig in executed_signatures:
                    continue
                executed_signatures.add(call_sig)

                # 도구 실행 및 이벤트 스트리밍
                yield {"type": "tool_start", "name": func_name, "args": args}
                tool_result = execute_tool(func_name, args)

                _update_context_from_result(room_context, func_name, args, tool_result)

                if func_name == "export_search_results_to_file" and isinstance(tool_result, dict) and tool_result.get("success"):
                    saved_path = tool_result.get("saved_path", "")
                    just_exported_basename = os.path.basename(saved_path)

                yield {"type": "tool_end", "name": func_name, "result": tool_result}

                # 문자열/Dict 구분 없이 안전하게 주입
                messages.append({
                    "role": "tool",
                    "content": _serialize_tool_result(tool_result)
                })
            continue

        # ---------------------------------------------------------
        # Case B: 텍스트 본문 추출 및 폴백 검사
        # ---------------------------------------------------------
        raw_content = message.get("content", "")
        content_text = clean_model_output(raw_content)

        # 텍스트로 도구를 부른 경우 (폴백)
        fallback = _parse_fallback_tool_call(content_text)
        if fallback:
            func_name, args = fallback
            yield {"type": "tool_start", "name": func_name, "args": args}
            tool_result = execute_tool(func_name, args)
            _update_context_from_result(room_context, func_name, args, tool_result)
            yield {"type": "tool_end", "name": func_name, "result": tool_result}

            messages.append({
                "role": "tool",
                "content": _serialize_tool_result(tool_result)
            })
            continue

        # ---------------------------------------------------------
        # Case C: 빈 본문 방어 (Self-Correction Retry)
        # ---------------------------------------------------------
        if not content_text:
            can_retry, prompt_or_msg = _handle_empty_response(retry_count, MAX_RETRIES, bool(tools))
            if not can_retry:
                yield {"type": "text", "content": prompt_or_msg}
                break

            retry_count += 1
            messages.append({"role": "user", "content": prompt_or_msg})
            continue

        # ---------------------------------------------------------
        # Case D: 최종 정상 답변 도출 완료
        # ---------------------------------------------------------
        yield {"type": "text", "content": content_text}
        break