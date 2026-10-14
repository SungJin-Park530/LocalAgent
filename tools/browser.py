# 브라우저 방문 기록 조회 도구

import os
import shutil
import sqlite3
import tempfile
import time as time_module
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List
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
    Chrome 방문 기록을 안전하게 복사하여 검색하고, 
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

    # 고유한 임시 파일을 사용해 동시 조회 간 파일 경합을 막습니다.
    temp_file = tempfile.NamedTemporaryFile(
        prefix="localagent_chrome_history_", suffix=".db", delete=False
    )
    temp_history = temp_file.name
    temp_file.close()

    try:
        journal_path = f"{history_src}-journal"
        journal_size = (
            os.path.getsize(journal_path) if os.path.exists(journal_path) else 0
        )
        if journal_size:
            raise RuntimeError(
                "Chrome history journal is active; close Chrome and retry"
            )

        source_before = os.stat(history_src)
        print("[Browser] source history copy started", flush=True)
        shutil.copy2(history_src, temp_history)
        source_after = os.stat(history_src)
        if (
            source_before.st_size != source_after.st_size
            or source_before.st_mtime_ns != source_after.st_mtime_ns
            or (os.path.exists(journal_path) and os.path.getsize(journal_path) > 0)
        ):
            raise RuntimeError("Chrome history changed during copy; retry the query")
        print("[Browser] source history copy completed", flush=True)
    except Exception as e:
        if os.path.exists(temp_history):
            try:
                os.remove(temp_history)
            except OSError:
                pass
        print(f"[Browser] source history copy failed: {e}", flush=True)
        return f"[오류] 방문 기록 복사 실패 (Chrome DB가 사용 중일 수 있습니다): {e}"

    query = """
    SELECT 
        title, 
        url, 
        visit_count,
        datetime(last_visit_time / 1000000 - 11644473600, 'unixepoch', 'localtime') AS visit_time
    FROM urls
    WHERE 1 = 1
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

    try:
        print("[Browser] temp DB connect/query started", flush=True)
        conn = sqlite3.connect(temp_history, timeout=3.0)
        conn.execute("PRAGMA busy_timeout = 3000")
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        print(f"[Browser] temp DB query completed: {len(rows)} rows", flush=True)
    except Exception as e:
        print(f"[Browser] temp DB query failed: {e}", flush=True)
        return f"[오류] SQLite 조회 실패: {e}"
    finally:
        if "conn" in locals():
            try:
                conn.close()
            except Exception:
                pass
        if os.path.exists(temp_history):
            try:
                os.remove(temp_history)
            except Exception:
                pass

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
    """Chrome 방문 기록을 키워드와 날짜·시간 범위로 조회합니다.

    날짜·시간 범위는 ISO 형식(YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS)의
    start_datetime과 end_datetime으로 전달합니다. 날짜만 주어지면 시작은
    그 날 00:00:00, 종료는 23:59:59로 해석합니다. 예를 들어 특정 날짜의
    오후 5시 이전은 그 날짜 00:00:00부터 17:00:00까지 지정합니다.
    두 경계가 모두 없을 때만 days(기본 최근 7일)를 사용합니다.
    시작이 종료보다 늦으면 조회하지 않고 사용자에게 범위를 확인하도록 안내합니다.
    상대 시각(예: 어제, 오늘 오전)이 포함되면 get_current_time을 먼저 호출해
    현재 날짜를 확인하고, 사용자의 현지 시각 기준 절대 범위로 변환한 뒤 이 도구를 별도로 호출합니다.
    시간 조회와 방문 기록 조회를 한 번의 응답에서 동시에 요청하지 않습니다.
    예를 들어 '어제 오전'은 전날 00:00:00부터 11:59:59까지로 지정합니다.
    현재 날짜를 추측하지 말고, 시간 조회 결과가 없을 때만 사용자에게 날짜를 확인합니다.
    기본 최대 결과는 최근 30개입니다.
    """
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
                        "description": "조회 기간(일). 사용자가 기간을 말하지 않으면 7일, 지정하면 해당 일수"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "가져올 최대 결과 개수 (기본값: 최근 30개)"
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "선택적 시작 경계. 상대 날짜·시간은 get_current_time을 먼저 호출해 기준 날짜를 확인한 뒤 계산한 절대 날짜·시각을 현지 ISO 형식 YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS[.ffffff]로 전달합니다. 날짜만 지정하면 해당 날짜 00:00:00. 예: 2026-10-10T00:00:00."
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "선택적 종료 경계. 사용자가 지정한 종료 날짜·시각을 현지 ISO 형식 YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS[.ffffff]로 전달합니다. 날짜만 지정하면 해당 날짜 23:59:59.999999까지 포함합니다. 특정 시각의 해당 초 전체를 포함하려면 .999999를 붙입니다. 예: 2026-10-10T17:00:00. 특정 날짜 오후 5시 이전은 시작도 그 날짜 00:00:00으로 지정합니다. 시작 경계가 종료보다 늦으면 호출 전에 사용자에게 확인합니다."
                    }
                },
                "required": []
            }
        }
    }
]