# 도구 모듈 초기화 및 실행 관리

from .files import FILES_SCHEMAS
from .chat_utils import CHAT_SCHEMAS
from .browser import BROWSER_SCHEMAS
from . import files as files_module
from . import chat_utils
from . import browser as browser_module

# 나중에 모듈이 추가되면 여기에 리스트만 더해주면 됩니다.
ALL_SCHEMAS = [
    *FILES_SCHEMAS,
    *CHAT_SCHEMAS,
    *BROWSER_SCHEMAS,
]

# 도구 함수를 검색할 모듈 등록
TOOL_MODULES = [
    files_module,
    chat_utils,
    browser_module,
]

def execute_tool(func_name: str, args: dict) -> dict:
    """등록된 도구 모듈들을 순회하며 해당 함수를 찾아 동적으로 실행합니다."""
    for module in TOOL_MODULES:
        if hasattr(module, func_name):
            tool_func = getattr(module, func_name)
            return tool_func(**args)
    return {"success": False, "error": f"정의되지 않은 도구입니다: {func_name}"}