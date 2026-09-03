# 모델 설정값
import os

# 프로젝트 루트 경로 (F:\LocalAgent)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 캐시 및 임시 파일 디렉터리 설정 (없으면 자동 생성)
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 검색 캐시 파일 경로
CACHE_FILE_PATH = os.path.join(CACHE_DIR, "search_cache.json")

# 기본 파일 내보내기 경로
DEFAULT_EXPORT_PATH = os.path.join(BASE_DIR, "search_result.txt")

# 프롬프트 디렉터리 경로
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")

# 모델 풀 설정
AVAILABLE_MODELS = {
    "default": "fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:9b",
    "qwen_30b": "qwen3:30b-a3b"
}

CURRENT_MODEL = AVAILABLE_MODELS["default"]

# 모델별 최적 파라미터 매핑
MODEL_PROFILES = {
    "qwen_9b": {
        "name": "fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:9b",
        "options": {
            "num_ctx": 16384,
            "num_predict": 1024,
            "temperature": 0.7
        }
    },
    "qwen_30b": {
        "name": "qwen3:30b-a3b",
        "options": {
            "num_ctx": 4096,       # 대형 모델은 4k~8k로 타이트하게 관리
            "num_predict": 1024,
            "temperature": 0.6
        }
    }
}

# 기본 선택 프로필
DEFAULT_PROFILE = "qwen_9b"