# 시간과 날씨를 조회하는 툴
import urllib.request
import json
from datetime import datetime

# 1. 시간 조회
def get_current_time(**kwargs) -> dict:
    """현재 시스템의 날짜, 요일, 시간을 반환합니다."""
    now = datetime.now()
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return {
        "date": now.strftime("%Y년 %m월 %d일"),
        "weekday": weekdays[now.weekday()],
        "time": now.strftime("%H시 %M분 %S초"),
        "is_night": now.hour >= 22 or now.hour < 6
    }

# 2. 무료 오픈 날씨 조회 (wttr.in JSON 포맷)
def get_current_weather(location: str = "Seoul", **kwargs) -> dict:
    """현재 위치(기본 Seoul 또는 입력받은 도시)의 날씨를 조회합니다."""
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
                "humidity": f"{current['humidity']}%"
            }
    except Exception as e:
        return {"error": f"날씨 정보를 가져오지 못했습니다: {str(e)}"}

# 3. 도구 스키마 정의
CHAT_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "현재 날짜, 요일, 시각 정보를 확인합니다. 사용자가 지금 몇 시인지 묻거나 시간대(아침, 밤 등)에 맞는 인사를 나눌 때 사용합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "지정된 도시의 현재 기온과 날씨 상태를 확인합니다. 날씨나 외출 관련 이야기를 나눌 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "조회할 영문 도시 이름 (예: Seoul, Suwon, Busan). 생략 시 Seoul."
                    }
                },
                "required": []
            }
        }
    }
]