# 도구 모듈 초기화 및 실행 관리

from .browser import BROWSER_SCHEMAS, BROWSER_TOOLS
from .chat_utils import CHAT_SCHEMAS, CHAT_TOOLS
from .files import FILES_SCHEMAS, FILES_TOOLS
from .web_shopping import WEB_SCHEMAS, WEB_TOOLS

# 1. UI용 OpenAI 스키마 모음 (Streamlit 사이드바용)
ALL_SCHEMAS = [
    *CHAT_SCHEMAS,
    *BROWSER_SCHEMAS,
    *FILES_SCHEMAS,
    *WEB_SCHEMAS,
]

# 2. LangGraph용 전역 도구 인스턴스 모음
ALL_TOOLS = [
    *CHAT_TOOLS,
    *BROWSER_TOOLS,
    *FILES_TOOLS,
    *WEB_TOOLS,
]

# 3. 도구 이름 기반 동적 레지스트리 (engine.py가 참조)
TOOL_REGISTRY = {t.name: t for t in ALL_TOOLS}
