# 채팅방 관리자 코드
import os
from tools import ALL_SCHEMAS
from tools.browser import BROWSER_SCHEMAS
from tools.files import FILES_SCHEMAS
from tools import TOOL_REGISTRY

PROMPTS_DIR = "prompts"

# UI에 표시할 직관적인 도구 이름과 간단 설명 매핑
TOOL_DISPLAY_MAP = {
    "search_browser_history": {
        "title": "🌐 브라우저 방문 기록",
        "desc": "Chrome 방문 기록에서 최근 방문한 페이지를 찾습니다."
    },
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

TOOL_GROUP_SCHEMAS = {
    "browser": BROWSER_SCHEMAS,
    "files": FILES_SCHEMAS,
}


def get_available_tool_keys() -> list[str]:
    available_keys = []
    for tool_key, schemas in TOOL_GROUP_SCHEMAS.items():
        if any(
            schema.get("function", {}).get("name") in TOOL_REGISTRY
            for schema in schemas
        ):
            available_keys.append(tool_key)
    return available_keys


def get_tool_schemas_for_prompt_tools(selected_tools: list[str]) -> list[dict]:
    schemas_by_name = {
        schema["function"]["name"]: schema
        for schema in ALL_SCHEMAS
        if schema.get("function", {}).get("name")
    }
    selected_schemas = [schemas_by_name["get_current_time"]]
    included_names = {"get_current_time"}

    for tool_key in selected_tools:
        for schema in TOOL_GROUP_SCHEMAS.get(tool_key, []):
            name = schema.get("function", {}).get("name")
            if name and name not in included_names:
                selected_schemas.append(schema)
                included_names.add(name)
    return selected_schemas

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
    역할별 프롬프트 디렉터리에서 활성화된 .md 파일을 찾아 반환합니다.
    UI 선택값에는 prompts/ 기준 상대 경로를 보존합니다.
    """
    files = []
    for role in ("persona", "instructions", "tools", "workflow"):
        role_dir = os.path.join(prompts_dir, role)
        for current_dir, directories, filenames in os.walk(role_dir):
            directories.sort()
            for filename in sorted(filenames):
                if filename.endswith(".md"):
                    path = os.path.join(current_dir, filename)
                    relative_path = os.path.relpath(path, prompts_dir).replace(os.sep, "/")
                    files.append((relative_path, path))

    prompt_list = []

    for relative_path, path in files:
        title = relative_path
        try:
            with open(path, "r", encoding="utf-8") as file:
                first_line = file.readline().strip()
                if first_line.startswith("#"):
                    title = f"{relative_path} ({first_line.lstrip('#').strip()})"
        except Exception:
            pass

        prompt_list.append({"filename": relative_path, "display": title})

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
            "persona": "shiki",
            "selected_tools": [],
            "tools": get_tool_schemas_for_prompt_tools([]),
            "messages": [],
            "context": get_default_room_context()
        },
        "room_agent": {
            "name": "🛠️ 파일 작업방",
            "persona": "shiki",
            "selected_tools": ["files"],
            "tools": get_tool_schemas_for_prompt_tools(["files"]),
            "messages": [],
            "context": get_default_room_context()
        }
    }