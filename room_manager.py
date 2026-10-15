# 채팅방 관리자 코드
import json
import os
import tempfile
from tools.browser import BROWSER_SCHEMAS
from tools.files import FILES_SCHEMAS
from tools import TOOL_REGISTRY
from config.settings import BASE_DIR

PROMPTS_DIR = "prompts"
ROOM_STATE_PATH = os.path.join(BASE_DIR, "data", "rooms.json")

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
            "thread_id": "room_chat",
            "persona": "shiki",
            "selected_tools": [],
            "tools": get_tool_schemas_for_prompt_tools([]),
            "messages": [],
            "context": get_default_room_context()
        },
        "room_agent": {
            "name": "🛠️ 파일 작업방",
            "thread_id": "room_agent",
            "persona": "shiki",
            "selected_tools": ["files"],
            "tools": get_tool_schemas_for_prompt_tools(["files"]),
            "messages": [],
            "context": get_default_room_context()
        }
    }


def _normalize_room(room_id: str, room: dict) -> dict:
    selected_tools = [
        key for key in room.get("selected_tools", [])
        if isinstance(key, str) and key in TOOL_GROUP_SCHEMAS
    ]
    messages = room.get("messages", [])
    context = room.get("context", {})
    return {
        "name": str(room.get("name") or room_id),
        "thread_id": str(room.get("thread_id") or room_id),
        "persona": room.get("persona"),
        "selected_tools": selected_tools,
        "tools": get_tool_schemas_for_prompt_tools(selected_tools),
        "messages": messages if isinstance(messages, list) else [],
        "context": context if isinstance(context, dict) else get_default_room_context(),
    }


def load_room_state(path: str = ROOM_STATE_PATH) -> dict:
    """Load saved rooms, then add only default rooms missing from storage."""
    saved_state = {}
    try:
        with open(path, "r", encoding="utf-8") as state_file:
            raw_state = json.load(state_file)
        if isinstance(raw_state, dict) and isinstance(raw_state.get("rooms"), dict):
            saved_state = raw_state
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"[Rooms] Saved room state unavailable; using defaults: {error}")

    defaults = get_default_rooms()
    saved_rooms = saved_state.get("rooms", {})
    rooms = {
        room_id: _normalize_room(room_id, room)
        for room_id, room in saved_rooms.items()
        if isinstance(room_id, str) and isinstance(room, dict)
    }
    for room_id, default_room in defaults.items():
        if room_id not in rooms:
            rooms[room_id] = _normalize_room(room_id, default_room)

    active_room_id = saved_state.get("active_room_id")
    if active_room_id not in rooms:
        active_room_id = next(iter(rooms), None)
    return {"rooms": rooms, "active_room_id": active_room_id}


def save_room_state(
    rooms: dict,
    active_room_id: str | None,
    path: str = ROOM_STATE_PATH,
) -> None:
    """Persist room metadata and UI messages using an atomic UTF-8 JSON write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": 1,
        "active_room_id": active_room_id,
        "rooms": {
            room_id: _normalize_room(room_id, room)
            for room_id, room in rooms.items()
            if isinstance(room_id, str) and isinstance(room, dict)
        },
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=os.path.dirname(path),
            prefix="rooms_",
            suffix=".tmp",
            delete=False,
        ) as state_file:
            temporary_path = state_file.name
            json.dump(payload, state_file, ensure_ascii=False, indent=2)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)