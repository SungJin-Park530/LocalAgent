# 도구 모듈 초기화 및 실행 관리

from .browser import BROWSER_SCHEMAS, BROWSER_TOOLS
from .chat_utils import CHAT_SCHEMAS, CHAT_TOOLS
from .files import FILES_SCHEMAS, FILES_TOOLS

from . import browser as browser_module
from . import chat_utils
from . import files as files_module

# 1. UI용 OpenAI 스키마 모음 (Streamlit 사이드바용)
ALL_SCHEMAS = [
    *CHAT_SCHEMAS,
    *BROWSER_SCHEMAS,
    *FILES_SCHEMAS,
]

# 2. LangGraph용 전역 도구 인스턴스 모음
ALL_TOOLS = [
    *CHAT_TOOLS,
    *BROWSER_TOOLS,
    *FILES_TOOLS,
]

# 3. 도구 이름 기반 동적 레지스트리 (engine.py가 참조)
TOOL_REGISTRY = {t.name: t for t in ALL_TOOLS}

# 4. 기존 수동 실행 호환용 모듈 목록 및 실행 함수
TOOL_MODULES = [
    chat_utils,
    browser_module,
    files_module,
]


def execute_tool(func_name: str, args: dict) -> dict:
  """(레거시 호환용) 등록된 도구 모듈들을 순회하며 해당 함수를 찾아 동적으로 실행합니다."""
  for module in TOOL_MODULES:
    if hasattr(module, func_name):
      tool_func = getattr(module, func_name)
      return tool_func(**args)
  return {"success": False, "error": f"정의되지 않은 도구입니다: {func_name}"}