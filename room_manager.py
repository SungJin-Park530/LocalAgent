# 채팅방 관리자 코드
import os
from tools import ALL_SCHEMAS
from tools.chat_utils import CHAT_SCHEMAS

PROMPTS_DIR = "prompts"

# UI에 표시할 직관적인 도구 이름과 간단 설명 매핑
TOOL_DISPLAY_MAP = {
    "get_current_time": {
        "title": "⏰ 현재 시간 확인",
        "desc": "현재 날짜, 요일, 시간을 확인합니다."
    },
    "get_current_weather": {
        "title": "🌤️ 현재 날씨 조회",
        "desc": "특정 도시나 현재 지역의 날씨와 기온을 조회합니다."
    },
    "list_directory": {
        "title": "📂 폴더 목록 조회",
        "desc": "지정한 경로 내 파일과 하위 폴더 목록을 확인합니다."
    },
    "search_files": {
        "title": "🔍 파일/폴더 검색",
        "desc": "키워드로 특정 파일이나 폴더를 탐색합니다."
    },
    "read_file": {
        "title": "📄 파일 내용 읽기",
        "desc": "텍스트 파일의 내용을 읽어옵니다."
    },
    "write_file": {
        "title": "✏️ 새 파일 작성/수정",
        "desc": "지정한 경로에 텍스트 파일을 새로 작성하거나 덮어씁니다."
    },
    "export_search_results_to_file": {
        "title": "💾 검색 결과 파일 저장",
        "desc": "탐색 결과를 별도의 텍스트 파일로 내보냅니다."
    }
}

def get_available_tool_groups() -> dict:
    tools_map = {}
    for schema in ALL_SCHEMAS:
        func = schema.get("function", {})
        func_name = func.get("name")
        if not func_name:
            continue

        meta = TOOL_DISPLAY_MAP.get(func_name, {})
        display_title = meta.get("title", func_name)
        display_desc = meta.get("desc", func.get("description", ""))

        tools_map[func_name] = {
            "display": f"{display_title} | {display_desc}",
            "short_title": display_title,
            "schema": schema
        }
    return tools_map

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

def get_default_room_context() -> dict:
    """새로운 대화방을 위한 기본 세션 컨텍스트 구조 반환"""
    return {
        "cwd": None,             # 현재 작업/기준 디렉터리 경로
        "last_keyword": "",      # 직전 검색 키워드
        "last_exported": None    # 가장 최근 내보낸 파일 절대 경로
    }

def get_default_rooms() -> dict:
    """
    초기 실행 시 세션에 채워둘 기본 방 템플릿입니다.
    """
    return {
        "room_chat": {
            "name": "💬 기본 잡담방",
            "prompt_files": ["01_persona.md", "02_chat.md"],
            "tools": [CHAT_SCHEMAS[0]],
            "messages": [],
            "context": get_default_room_context()
        },
        "room_agent": {
            "name": "🛠️ 파일 작업방",
            "prompt_files": ["01_persona.md", "03_files.md", "04_agent_workflow.md"],
            "tools": ALL_SCHEMAS,
            "messages": [],
            "context": get_default_room_context()
        }
    }