# 브라우저 방문 기록 조회 도구

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List
from langchain_core.tools import tool
import json
from urllib.parse import urlsplit, urlunsplit


MAX_TITLE_LENGTH = 120
MAX_URL_LENGTH = 180
MAX_RESULT_LINE_LENGTH = 360
MAX_RESULT_LENGTH = 6000


def _clean_display_text(value: str, max_length: int) -> str:
    """브라우저 기록의 제어 문자와 과도하게 긴 표시 텍스트를 정리합니다."""
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[:max_length - 3]}..."


def _sanitize_url(url: str) -> str:
    """URL의 추적/검색 쿼리와 fragment를 제거하고 표시 길이를 제한합니다."""
    raw_url = "".join(str(url or "").split())
    if not raw_url:
        return "(URL 없음)"

    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme and parsed.netloc:
            sanitized = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            )
        else:
            sanitized = raw_url.split("?", 1)[0].split("#", 1)[0]
    except ValueError:
        sanitized = raw_url.split("?", 1)[0].split("#", 1)[0]

    sanitized = _clean_display_text(sanitized, MAX_URL_LENGTH)
    return f"{sanitized} [쿼리 생략]" if "?" in raw_url else sanitized

def _get_chrome_history_path() -> str:
    """Windows 환경의 Chrome History 기본 파일 경로를 반환합니다."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        user_profile = os.environ.get("USERPROFILE", "")
        return os.path.join(
            user_profile, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "History"
        )
    return os.path.join(local_app_data, "Google", "Chrome", "User Data", "Default", "History")


def _fetch_browser_history(
    keyword: str = "",
    days: int = 7,
    limit: int = 15
) -> str:
    """
    Chrome 방문 기록을 안전하게 복사하여 검색하고, 
    LLM이 바로 해석할 수 있는 요약 텍스트로 반환합니다.
    """
    history_src = _get_chrome_history_path()
    if not os.path.exists(history_src):
        return f"[오류] Chrome 방문 기록 파일이 존재하지 않습니다: {history_src}"

    # 임시 디렉터리로 복사하여 파일 락(Lock) 방어
    temp_dir = tempfile.gettempdir()
    temp_history = os.path.join(temp_dir, f"temp_chrome_history_{os.getpid()}.db")

    try:
        shutil.copy2(history_src, temp_history)
    except Exception as e:
        return f"[오류] 방문 기록 파일 복사 실패 (Chrome 실행 권한 문제 등): {e}"

    # 기간(WebKit 마이크로초) 계산
    time_threshold = datetime.now() - timedelta(days=days)
    webkit_epoch_diff = 11644473600
    threshold_microsec = int((time_threshold.timestamp() + webkit_epoch_diff) * 1000000)

    query = """
    SELECT 
        title, 
        url, 
        visit_count,
        datetime(last_visit_time / 1000000 - 11644473600, 'unixepoch', 'localtime') AS visit_time
    FROM urls
    WHERE last_visit_time >= ?
    """
    params: List[Any] = [threshold_microsec]

    clean_kw = keyword.strip()
    if clean_kw:
        query += " AND (title LIKE ? OR url LIKE ?)"
        pattern = f"%{clean_kw}%"
        params.extend([pattern, pattern])

    query += " ORDER BY last_visit_time DESC LIMIT ?"
    params.append(limit)

    try:
        conn = sqlite3.connect(temp_history)
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"[오류] SQLite 조회 실패: {e}"
    finally:
        if os.path.exists(temp_history):
            try:
                os.remove(temp_history)
            except Exception:
                pass

    if not rows:
        filter_desc = f"키워드: '{clean_kw}', " if clean_kw else ""
        return f"최근 {days}일 동안의 방문 기록 중 조건({filter_desc}최대 {limit}건)에 일치하는 내역이 없습니다."

    # LLM이 읽기 편하고 입력 크기가 제한된 결과 텍스트 생성
    lines = [f"[조회 성공] 최근 {days}일 기준 방문 기록 {len(rows)}건:"]
    for title, url, visit_count, visit_time in rows:
        display_title = _clean_display_text(title, MAX_TITLE_LENGTH) or "(제목 없음)"
        display_url = _sanitize_url(url)
        result_line = (
            f"- [{visit_time}] {display_title} "
            f"(방문 {visit_count}회) | {display_url}"
        )
        lines.append(_clean_display_text(result_line, MAX_RESULT_LINE_LENGTH))

    result = "\n".join(lines)
    if len(result) > MAX_RESULT_LENGTH:
        result = (
            result[:MAX_RESULT_LENGTH - 40].rsplit("\n", 1)[0]
            + "\n- (결과가 길어 나머지 방문 기록은 생략되었습니다.)"
        )

    return result

@tool
def search_browser_history(keyword: str = "", days: int = 7, limit: int = 5) -> str:
    """Chrome 브라우저의 최근 방문 기록을 조회합니다. 
    사용자가 방문한 웹사이트, 검색했던 페이지 내역이나 특정 URL을 찾을 때 사용합니다."""
    # 내부 조회 로직 호출
    res = _fetch_browser_history(keyword=keyword, days=days, limit=limit)
    return json.dumps(res, ensure_ascii=False)

BROWSER_TOOLS = [
    search_browser_history,
]

BROWSER_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_browser_history",
            "description": (
                "사용자의 Chrome 브라우저 방문 기록(URL, 페이지 제목, 방문 시각)을 조회합니다. "
                "특정 주제나 사이트를 방문했던 기억을 찾거나, 최근 방문한 페이지 목록을 확인할 때 사용합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 키워드 (웹페이지 제목이나 URL에 포함된 단어). 특정 키워드 없이 최근 전체 기록을 보려면 빈 문자열로 지정합니다."
                    },
                    "days": {
                        "type": "integer",
                        "description": "최근 며칠간의 기록을 조회할지 지정 (기본값: 7일)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "가져올 최대 결과 개수 (기본값: 15개)"
                    }
                },
                "required": []
            }
        }
    }
]