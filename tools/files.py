# 파일 조회 및 파일 입출력 기능을 담당하는 도구 모듈

import os
import re
import json
import shutil
from send2trash import send2trash
from config.categories import FILE_CATEGORIES, EXCLUDE_DIRS
from config.settings import CACHE_FILE_PATH, EXPORT_DIR, DEFAULT_EXPORT_FILENAME, MAX_SCAN_LIMIT

ALLOWED_EXTENSIONS = {".txt", ".py", ".md", ".json", ".csv", ".log"}
MAX_FILE_SIZE = 1_000_000  # 1MB

# 캐시 파일 정규화 경로
CACHE_FILE = os.path.normpath(os.path.abspath(CACHE_FILE_PATH))


def search_folders(
    path: str = ".",
    keyword: str = "",
    recursive: bool = False,
    max_results: int = 50
) -> dict:
    """디렉터리(폴더) 목록만 빠르게 조회하거나 키워드로 폴더를 검색합니다."""
    try:
        clean_path = path.strip().strip("'\"")
        if re.match(r"^[a-zA-Z]:$", clean_path):
            clean_path += "\\"
        elif re.match(r"^[a-zA-Z]:[\\/]+$", clean_path):
            clean_path = clean_path[:2] + "\\"

        abs_path = os.path.normpath(os.path.abspath(clean_path))
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"경로가 존재하지 않습니다: {abs_path}"}

        folders = []
        kw_lower = keyword.strip().lower() if keyword else ""

        if not recursive:
            with os.scandir(abs_path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        name = entry.name
                        if name.lower() in EXCLUDE_DIRS or name.startswith(("$", ".")):
                            continue
                        if kw_lower and kw_lower not in name.lower():
                            continue
                        folders.append({"name": name, "path": entry.path})
                        if len(folders) >= max_results:
                            break
        else:
            scanned_dir_count = 0
            MAX_DIR_SCAN = 10000

            for root, dirs, _ in os.walk(abs_path, topdown=True, onerror=None):
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in EXCLUDE_DIRS and not d.startswith(("$", "."))
                ]
                scanned_dir_count += len(dirs)

                for d in dirs:
                    if kw_lower and kw_lower not in d.lower():
                        continue
                    folders.append({"name": d, "path": os.path.join(root, d)})
                    if len(folders) >= max_results:
                        dirs.clear()
                        break

                if len(folders) >= max_results or scanned_dir_count >= MAX_DIR_SCAN:
                    break

        return {
            "success": True,
            "target_path": abs_path,
            "total_found": len(folders),
            "folders": folders,
            "message": f"총 {len(folders)}개 폴더를 확인했습니다."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_unique_filepath(filepath: str) -> str:
    """동일한 이름의 파일이 이미 존재하면 파일명 뒤에 (1), (2) 등을 붙여 고유한 경로를 반환합니다."""
    if not os.path.exists(filepath):
        return filepath

    directory, filename = os.path.split(filepath)
    name, ext = os.path.splitext(filename)

    counter = 1
    while True:
        new_filename = f"{name} ({counter}){ext}"
        new_filepath = os.path.join(directory, new_filename)
        if not os.path.exists(new_filepath):
            return new_filepath
        counter += 1

def search_files(
    path: str = ".",
    keyword: str = "",
    category: str = "",
    min_size_mb: float = 0.0,
    max_size_mb: float = 0.0,
    recursive: bool = False,
    max_results: int = 50
) -> dict:
    """폴더 내의 항목을 조회하거나 조건에 맞는 파일을 검색합니다."""
    try:
        clean_path = path.strip().strip("'\"")
        if re.match(r"^[a-zA-Z]:$", clean_path):
            clean_path += "\\"
        elif re.match(r"^[a-zA-Z]:[\\/]+$", clean_path):
            clean_path = clean_path[:2] + "\\"

        abs_path = os.path.normpath(os.path.abspath(clean_path))
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"지정한 경로가 존재하지 않습니다: {abs_path}"}

        target_extensions = FILE_CATEGORIES.get(category.lower()) if category else None
        matches = []

        if not recursive:
            try:
                entries = os.listdir(abs_path)
            except PermissionError:
                return {"success": False, "error": f"폴더 접근 권한이 없습니다: {abs_path}"}

            for item in entries:
                if item.lower() in EXCLUDE_DIRS or item.startswith("$"):
                    continue
                full_path = os.path.join(abs_path, item)
                is_dir = os.path.isdir(full_path)

                if keyword and keyword.lower() not in item.lower():
                    continue

                matches.append({
                    "name": item,
                    "path": full_path,
                    "type": "directory" if is_dir else "file"
                })
                if len(matches) >= max_results:
                    break
        else:
            scanned_count = 0
            hit_scan_limit = False

            for root, dirs, files in os.walk(abs_path, topdown=True, onerror=None):
                dirs[:] = [
                    d for d in dirs 
                    if d.lower() not in EXCLUDE_DIRS and not d.startswith(("$", "."))
                ]

                for file in files:
                    scanned_count += 1
                    if scanned_count >= MAX_SCAN_LIMIT:
                        hit_scan_limit = True
                        dirs.clear()
                        break

                    ext = os.path.splitext(file)[1].lower()
                    if target_extensions and ext not in target_extensions:
                        continue
                    if keyword and keyword.lower() not in file.lower():
                        continue

                    full_path = os.path.join(root, file)
                    need_stat = (min_size_mb > 0) or (max_size_mb > 0)

                    if need_stat:
                        try:
                            stat = os.stat(full_path)
                            size_mb = round(stat.st_size / (1024 * 1024), 2)
                            if min_size_mb > 0 and size_mb < min_size_mb:
                                continue
                            if max_size_mb > 0 and size_mb > max_size_mb:
                                continue
                        except (PermissionError, FileNotFoundError, OSError):
                            continue
                    else:
                        try:
                            size_mb = round(os.path.getsize(full_path) / (1024 * 1024), 2)
                        except OSError:
                            size_mb = 0.0

                    matches.append({
                        "name": file,
                        "path": full_path,
                        "type": "file",
                        "size_mb": size_mb,
                        "extension": ext
                    })

                    if len(matches) >= max_results:
                        break

                if len(matches) >= max_results or hit_scan_limit:
                    break

        # 디스크 캐시 저장
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(matches, f, ensure_ascii=False)
        except Exception as e:
            print(f"[캐시 저장 실패]: {e}")

        return {
            "success": True,
            "target_path": abs_path,
            "total_found": len(matches),
            "scanned_files_count": scanned_count,
            "hit_scan_limit": hit_scan_limit,  # 5만 개 한도 도달 여부
            "hit_max_results": len(matches) >= max_results, # 50개 초과 여부
            "message": (
                f"최대 검사 한도({MAX_SCAN_LIMIT}개)에 도달하여 탐색이 중단되었습니다. "
                if hit_scan_limit else f"총 {len(matches)}개 파일이 검색되어 캐시에 보관되었습니다."
            )
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def export_search_results_to_file(dest_path: str = DEFAULT_EXPORT_FILENAME, keyword: str = "") -> dict:
    """최근 검색 결과 캐시에서 목록을 추출하여 search_result 디렉터리에 안전하게 저장합니다."""
    if not os.path.exists(CACHE_FILE):
        return {"success": False, "error": "저장할 최근 검색 결과가 없습니다. 먼저 파일 검색을 실행해 주세요."}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached_results = json.load(f)

        if not cached_results:
            return {"success": False, "error": "저장할 최근 검색 결과가 비어 있습니다."}

        if keyword.strip():
            clean_kw = keyword.strip().lower()
            target_list = [item for item in cached_results if clean_kw in item["name"].lower()]
        else:
            target_list = cached_results

        if not target_list:
            return {"success": False, "error": f"검색 결과 중 '{keyword}'(이)가 포함된 파일이 없습니다."}

        # 경로 탈출 방지: 파일명만 추출하여 EXPORT_DIR로 귀속
        raw_name = dest_path.strip() if dest_path and dest_path.strip() else DEFAULT_EXPORT_FILENAME
        safe_filename = os.path.basename(raw_name)
        if not os.path.splitext(safe_filename)[1]:
            safe_filename += ".txt"

        target_abs_path = os.path.join(EXPORT_DIR, safe_filename)
        safe_path = get_unique_filepath(target_abs_path)

        title_kw = f" [필터: '{keyword}']" if keyword.strip() else ""
        lines = [f"=== 검색 결과 목록{title_kw} (총 {len(target_list)}개) ===\n"]
        for idx, item in enumerate(target_list, 1):
            lines.append(f"{idx}. {item['name']} | {item.get('size_mb', 0)}MB | {item['path']}\n")

        with open(safe_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return {
            "success": True,
            "saved_count": len(target_list),
            "saved_path": safe_path,
            "filename": os.path.basename(safe_path),
            "message": f"검색 결과 {len(target_list)}건이 '{os.path.basename(safe_path)}' 파일로 저장되었습니다."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def read_file(path: str) -> dict:
    """텍스트 파일의 내용을 읽어 반환합니다."""
    try:
        clean_path = path.strip().strip("'\"")
        abs_path = os.path.normpath(os.path.abspath(clean_path))

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"파일을 찾을 수 없습니다: {abs_path}"}

        if os.path.isdir(abs_path):
            return {"success": False, "error": "지정한 경로는 디렉터리입니다. 파일을 지정해주세요."}

        _, ext = os.path.splitext(abs_path)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            return {"success": False, "error": f"지원하지 않는 확장자입니다 ({ext}). 지원 확장자: {list(ALLOWED_EXTENSIONS)}"}

        file_size = os.path.getsize(abs_path)
        if file_size > MAX_FILE_SIZE:
            return {"success": False, "error": f"파일 용량 초과 (최대 1MB, 현재: {file_size} bytes)"}

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(abs_path, "r", encoding="cp949") as f:
                content = f.read()

        return {
            "success": True,
            "path": abs_path,
            "size": file_size,
            "content": content
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def write_file(path: str, content: str, mode: str = "w") -> dict:
    """지정한 파일에 텍스트 내용을 저장하거나 덧붙입니다."""
    try:
        clean_path = path.strip().strip("'\"")
        abs_path = os.path.normpath(os.path.abspath(clean_path))

        parent_dir = os.path.dirname(abs_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        is_overwrite = os.path.exists(abs_path) and mode != "append"
        write_mode = "a" if mode == "append" else "w"

        with open(abs_path, write_mode, encoding="utf-8") as f:
            f.write(content)

        action_desc = "이어쓰기 완료" if write_mode == "a" else ("덮어쓰기 완료" if is_overwrite else "신규 생성 완료")

        return {
            "success": True,
            "path": abs_path,
            "filename": os.path.basename(abs_path),
            "action": action_desc,
            "message": f"파일 {action_desc}: {abs_path}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def delete_file(path: str) -> dict:
    """지정한 파일이나 폴더를 영구 삭제하지 않고 휴지통으로 안전하게 이동시킵니다."""
    try:
        clean_path = path.strip().strip("'\"")
        abs_path = os.path.normpath(os.path.abspath(clean_path))

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"항목을 찾을 수 없습니다: {abs_path}"}

        send2trash(abs_path)

        return {
            "success": True,
            "path": abs_path,
            "filename": os.path.basename(abs_path),
            "message": f"휴지통으로 안전하게 이동되었습니다: {abs_path}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def move_file(source_path: str, dest_path: str) -> dict:
    """파일이나 폴더를 다른 경로로 이동하거나 이름을 변경합니다."""
    try:
        abs_src = os.path.normpath(os.path.abspath(source_path.strip().strip("'\"")))
        abs_dst = os.path.normpath(os.path.abspath(dest_path.strip().strip("'\"")))

        if not os.path.exists(abs_src):
            return {"success": False, "error": f"원본 경로가 존재하지 않습니다: {abs_src}"}

        shutil.move(abs_src, abs_dst)
        return {
            "success": True,
            "source": abs_src,
            "destination": abs_dst,
            "message": f"이동 완료: {abs_src} -> {abs_dst}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_in_last_search(keyword: str) -> dict:
    """최근 검색된 파일 캐시 내에서 특정 키워드가 포함된 항목이 있는지 확인합니다."""
    if not os.path.exists(CACHE_FILE):
        return {"success": False, "error": "최근 검색 결과가 없습니다. 먼저 파일 검색을 실행해 주세요."}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached_results = json.load(f)

        if not cached_results:
            return {"success": False, "error": "최근 검색 결과가 비어 있습니다."}

        clean_kw = keyword.strip().lower()
        matched = [
            {"name": item["name"], "size_mb": item.get("size_mb", 0), "path": item["path"]}
            for item in cached_results
            if clean_kw in item["name"].lower()
        ]

        return {
            "success": True,
            "keyword": keyword,
            "found": len(matched) > 0,
            "total_matched": len(matched),
            "matches": matched[:5]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# 도구 정의 리스트 (LLM 스키마 동기화)
FILES_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_folders",
            "description": "지정한 경로의 하위 폴더 목록만 빠르게 조회하거나 폴더를 검색합니다. 파일은 검색하지 않아 매우 빠릅니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "조회할 기준 폴더 절대 경로 (예: 'D:\\' 또는 '.')"},
                    "keyword": {"type": "string", "description": "찾으려는 폴더명 키워드"},
                    "recursive": {"type": "boolean", "description": "하위 폴더 깊숙이 찾을지 여부"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "폴더 내 항목 조회 또는 키워드·카테고리·용량(MB)·하위폴더 조건으로 파일을 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "조회 또는 검색할 기준 폴더 절대 경로"},
                    "keyword": {"type": "string", "description": "파일명 또는 폴더명 검색어"},
                    "category": {
                        "type": "string",
                        "enum": ["video", "audio", "image", "document"],
                        "description": "파일 종류 필터"
                    },
                    "min_size_mb": {"type": "number", "description": "최소 파일 크기 (MB 단위)"},
                    "max_size_mb": {"type": "number", "description": "최대 파일 크기 (MB 단위)"},
                    "recursive": {"type": "boolean", "description": "하위 폴더 재귀 검색 여부"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_search_results_to_file",
            "description": "최근 검색된 파일 목록을 프로젝트 루트의 search_result 폴더 아래 텍스트 파일(.txt)로 저장합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dest_path": {
                        "type": "string",
                        "description": "저장할 파일명 (생략 시 'search_result.txt'로 자동 지정되며 무조건 search_result 디렉터리에 저장됨)"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "특정 단어가 포함된 항목만 필터링하여 저장하려는 경우 지정"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "텍스트 파일의 내용을 읽어옵니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "읽을 텍스트 파일의 절대 경로"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "지정한 경로에 텍스트나 소스 코드를 새로 작성하거나 저장합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "작성할 파일의 절대 경로"},
                    "content": {"type": "string", "description": "파일에 들어갈 전체 텍스트 내용"},
                    "mode": {"type": "string", "enum": ["w", "append"], "description": "새로쓰기('w') 또는 이어쓰기('append')"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "지정한 파일이나 폴더를 영구 삭제하지 않고 휴지통으로 안전하게 이동합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "휴지통으로 보낼 파일 또는 폴더의 절대 경로"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "파일이나 디렉터리를 다른 경로로 이동하거나 이름을 변경합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "원본 파일/폴더 절대 경로"},
                    "dest_path": {"type": "string", "description": "대상 절대 경로"}
                },
                "required": ["source_path", "dest_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_in_last_search",
            "description": "최근 검색된 캐시 목록 내에서 사용자가 언급한 특정 단어나 파일명이 있는지 확인합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "확인할 파일명이나 키워드"}
                },
                "required": ["keyword"]
            }
        }
    }
]