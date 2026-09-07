# 채팅방 관리자 코드
import os
from tools import ALL_SCHEMAS
from tools.chat_utils import CHAT_SCHEMAS

PROMPTS_DIR = "prompts"

def get_available_prompts(prompts_dir: str = PROMPTS_DIR) -> list[dict]:
    """
    prompts/ 디렉터리 내의 활성화된 .md 파일 목록을 스캔합니다.
    UI multiselect에서 식별하기 편하도록 파일명과 미리보기를 반환합니다.
    """
    if not os.path.exists(prompts_dir):
        return []

    files = sorted([f for f in os.listdir(prompts_dir) if f.endswith(".md")])
    prompt_list = []
    
    for f in files:
        path = os.path.join(prompts_dir, f)
        title = f
        try:
            with open(path, "r", encoding="utf-8") as file:
                first_line = file.readline().strip()
                if first_line.startswith("#"):
                    title = f"{f} ({first_line.lstrip('#').strip()})"
        except Exception:
            pass
        
        prompt_list.append({"filename": f, "display": title})
        
    return prompt_list

def get_available_tool_groups() -> dict[str, list[dict]]:
    """
    현재 등록된 ALL_SCHEMAS 도구들을 UI에서 묶음/단위별로 선택할 수 있도록 매핑합니다.
    지금은 파일 도구군 위주이므로 '파일 제어' 그룹으로 묶고, 개별 도구 단위도 지원합니다.
    """
    # 추후 도구가 늘어나면 함수명 접두사나 별도 태그 기준으로 자동 그룹화 가능
    tool_map = {}
    for schema in ALL_SCHEMAS:
        name = schema["function"]["name"]
        desc = schema["function"].get("description", "")
        tool_map[name] = {
            "schema": schema,
            "display": f"{name} ({desc[:25]}...)" if len(desc) > 25 else f"{name} ({desc})"
        }
    return tool_map

def get_default_rooms() -> dict:
    """
    초기 실행 시 세션에 채워둘 기본 방 템플릿입니다.
    """
    return {
        "room_chat": {
            "name": "💬 기본 잡담방",
            "prompt_files": ["01_persona.md", "02_chat.md"],
            "tools": [CHAT_SCHEMAS[0]],
            "messages": []
        },
        "room_agent": {
            "name": "🛠️ 파일 작업방",
            "prompt_files": ["01_persona.md", "03_files.md"],
            "tools": ALL_SCHEMAS,
            "messages": []
        }
    }