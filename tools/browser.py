# 브라우저 방문 기록 조회 도구

import ipaddress
import os
import sqlite3
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, List
from langchain_core.tools import tool
from urllib.parse import urlsplit, urlunsplit


MAX_TITLE_LENGTH = 120
MAX_URL_LENGTH = 180
MAX_RESULT_LINE_LENGTH = 360
MAX_RESULT_LENGTH = 12000
DEFAULT_HISTORY_DAYS = 7
DEFAULT_HISTORY_LIMIT = 30


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


def _is_localhost_url(url: str) -> int:
    """Streamlit 로컬 앱 주소와 루프백 주소를 방문 기록에서 제외합니다."""
    try:
        host = urlsplit(str(url or "")).hostname
    except ValueError:
        return 0

    if not host:
        return 0
    if host.rstrip(".") == "localhost":
        return 1
    try:
        return int(ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return 0


def _get_chrome_history_path() -> str:
    """Windows 환경의 Chrome History 기본 파일 경로를 반환합니다."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        user_profile = os.environ.get("USERPROFILE", "")
        return os.path.join(
            user_profile, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "History"
        )
    return os.path.join(local_app_data, "Google", "Chrome", "User Data", "Default", "History")


def _parse_history_bound(value: str, is_end: bool) -> datetime:
    normalized = value.strip()
    parsed = datetime.fromisoformat(normalized)
    has_time = "T" in normalized or " " in normalized

    if not has_time:
        parsed_date = date.fromisoformat(normalized)
        parsed = datetime.combine(
            parsed_date,
            time.max if is_end else time.min,
        )

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _to_webkit_timestamp(value: datetime) -> int:
    webkit_epoch_diff = 11644473600
    return int((value.timestamp() + webkit_epoch_diff) * 1000000)


def _fetch_browser_history(
    keyword: str = "",
    days: int = DEFAULT_HISTORY_DAYS,
    limit: int = DEFAULT_HISTORY_LIMIT,
    start_datetime: str = "",
    end_datetime: str = "",
) -> str:
    """
    Chrome 방문 기록을 read-only SQLite 연결로 검색하고,
    LLM이 바로 해석할 수 있는 요약 텍스트로 반환합니다.
    """
    start_bound = None
    end_bound = None
    try:
        if start_datetime:
            start_bound = _parse_history_bound(start_datetime, is_end=False)
        if end_datetime:
            end_bound = _parse_history_bound(end_datetime, is_end=True)
    except ValueError:
        return (
            "[조회 중단: 기간 재확인 필요] 날짜와 시간은 "
            "YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS 형식이어야 합니다. "
            "올바른 탐색 범위를 다시 확인해 주세요."
        )

    if start_bound and end_bound and start_bound > end_bound:
        return (
            "[조회 중단: 기간 재확인 필요] 시작 시간이 종료 시간보다 늦습니다. "
            f"입력된 범위는 {start_bound:%Y-%m-%d %H:%M}부터 "
            f"{end_bound:%Y-%m-%d %H:%M}까지입니다. "
            "날짜와 시간 범위를 사용자에게 확인해 주세요."
        )

    history_src = _get_chrome_history_path()
    if not os.path.exists(history_src):
        return f"[오류] Chrome 방문 기록 파일이 존재하지 않습니다: {history_src}"

    query = """
    SELECT 
        title, 
        url, 
        visit_count,
        datetime(last_visit_time / 1000000 - 11644473600, 'unixepoch', 'localtime') AS visit_time
    FROM urls
    WHERE 1 = 1
            AND is_localhost_url(url) = 0
    """
    params: List[Any] = []

    if start_bound:
        query += " AND last_visit_time >= ?"
        params.append(_to_webkit_timestamp(start_bound))
    if end_bound:
        query += " AND last_visit_time <= ?"
        params.append(_to_webkit_timestamp(end_bound))
    if not start_bound and not end_bound:
        time_threshold = datetime.now() - timedelta(days=days)
        query += " AND last_visit_time >= ?"
        params.append(_to_webkit_timestamp(time_threshold))

    clean_kw = keyword.strip()
    if clean_kw:
        query += " AND (title LIKE ? OR url LIKE ?)"
        pattern = f"%{clean_kw}%"
        params.extend([pattern, pattern])

    query += " ORDER BY last_visit_time DESC LIMIT ?"
    params.append(limit)

    rows = None
    for use_nolock in (False, True):
        conn = None
        try:
            lock_option = "&nolock=1" if use_nolock else ""
            source_uri = (
                f"{Path(history_src).resolve().as_uri()}?mode=ro{lock_option}"
            )
            if use_nolock:
                print("[Browser] retrying read-only query without file locks", flush=True)
            else:
                print("[Browser] source history query started", flush=True)
            conn = sqlite3.connect(source_uri, uri=True, timeout=0.25)
            conn.execute("PRAGMA busy_timeout = 250")
            conn.create_function("is_localhost_url", 1, _is_localhost_url)
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            print(f"[Browser] source history query completed: {len(rows)} rows", flush=True)
            break
        except sqlite3.OperationalError as error:
            if not use_nolock and "locked" in str(error).lower():
                continue
            print(f"[Browser] source history query failed: {error}", flush=True)
            return f"[오류] SQLite 조회 실패: {error}"
        except Exception as error:
            print(f"[Browser] source history query failed: {error}", flush=True)
            return f"[오류] SQLite 조회 실패: {error}"
        finally:
            if conn is not None:
                conn.close()

    if not rows:
        filter_desc = f"키워드: '{clean_kw}', " if clean_kw else ""
        period_desc = (
            f"{start_bound or '시작 제한 없음'} ~ {end_bound or '종료 제한 없음'}"
            if start_bound or end_bound
            else f"최근 {days}일"
        )
        return f"{period_desc} 방문 기록 중 조건({filter_desc}최대 {limit}건)에 일치하는 내역이 없습니다."

    # LLM이 읽기 편하고 입력 크기가 제한된 결과 텍스트 생성
    period_desc = (
        f"{start_bound or '시작 제한 없음'} ~ {end_bound or '종료 제한 없음'}"
        if start_bound or end_bound
        else f"최근 {days}일"
    )
    lines = [f"[조회 성공] {period_desc} 방문 기록 {len(rows)}건:"]
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
def search_browser_history(
    keyword: str = "",
    days: int = DEFAULT_HISTORY_DAYS,
    limit: int = DEFAULT_HISTORY_LIMIT,
    start_datetime: str = "",
    end_datetime: str = "",
) -> str:
    """Chrome 방문 기록을 조회합니다."""
    started_at = time_module.monotonic()
    print("[Browser] search_browser_history enter", flush=True)
    try:
        return _fetch_browser_history(
            keyword=keyword,
            days=days,
            limit=limit,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
    finally:
        elapsed = time_module.monotonic() - started_at
        print(f"[Browser] search_browser_history exit ({elapsed:.2f}s)", flush=True)

BROWSER_TOOLS = [
    search_browser_history,
]

BROWSER_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_browser_history",
            "description": "Chrome 방문 기록을 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "페이지 제목 또는 URL 검색어. 기본값은 빈 문자열입니다."
                    },
                    "days": {
                        "type": "integer",
                        "description": "날짜 경계가 없을 때 조회할 최근 일수. 기본값은 7입니다."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "최대 결과 개수. 기본값은 30입니다."
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "선택적 시작 경계. ISO 날짜 또는 현지 날짜·시각입니다."
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "선택적 종료 경계. ISO 날짜 또는 현지 날짜·시각입니다."
                    }
                },
                "required": []
            }
        }
    }
]