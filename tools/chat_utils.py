# 시간과 날씨를 조회하는 툴
from datetime import datetime
import json
import urllib.request
from langchain_core.tools import tool

# ==========================================
# 1. 순수 비즈니스 로직 (내부 전용 함수)
# ==========================================

def _fetch_system_time() -> dict:
    """시스템의 현재 날짜, 요일, 시간을 계산하여 딕셔너리로 반환합니다."""
    now = datetime.now()
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return {
        "date": now.strftime("%Y년 %m월 %d일"),
        "weekday": weekdays[now.weekday()],
        "time": now.strftime("%H시 %M분 %S초"),
        "is_night": now.hour >= 22 or now.hour < 6,
    }

def _fetch_weather(location: str = "Seoul") -> dict:
    """wttr.in API를 호출하여 지정된 위치의 날씨 정보를 가져옵니다."""
    url = f"https://wttr.in/{location}?format=j1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            current = data["current_condition"][0]
            return {
                "location": location,
                "temp_C": f"{current['temp_C']}°C",
                "weather_desc": current["weatherDesc"][0]["value"],
                "humidity": f"{current['humidity']}%",
            }
    except Exception as e:
        return {"error": f"날씨 정보를 가져오지 못했습니다: {str(e)}"}


# ==========================================
# 2. 공개 LangChain / LangGraph 도구 인터페이스
# ==========================================

@tool
def get_current_time() -> str:
    """현재 시스템의 날짜, 요일, 시각 정보를 확인합니다. 
    사용자가 지금 몇 시인지 묻거나 시간대에 맞는 인사를 나눌 때 사용합니다."""
    return json.dumps(_fetch_system_time(), ensure_ascii=False)

@tool
def get_current_weather(location: str = "Seoul") -> str:
    """지정된 도시의 실시간 기온과 날씨 상태를 조회합니다. 
    날씨나 외출 관련 이야기를 나눌 때 사용합니다."""
    return json.dumps(_fetch_weather(location=location), ensure_ascii=False)

# 모듈 외부에 공개할 표준 도구 리스트
CHAT_TOOLS = [
    get_current_time,
    get_current_weather,
]


# ==========================================
# 3. UI 호환용 스키마 정의 (기존 유지)
# ==========================================
CHAT_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": get_current_time.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": get_current_weather.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "조회할 영문 도시 이름 (예: Seoul, Suwon, Busan). 생략 시 Seoul.",
                    }
                },
                "required": [],
            },
        },
    },
]