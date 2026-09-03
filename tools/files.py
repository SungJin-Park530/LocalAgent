# 파일 조회 기능을 담당하는 코드

import os
import re
import json
from send2trash import send2trash
import shutil
from config.categories import FILE_CATEGORIES, EXCLUDE_DIRS
from config.settings import CACHE_FILE_PATH, DEFAULT_EXPORT_PATH

ALLOWED_EXTENSIONS = {".txt", ".py", ".md", ".json", ".csv", ".log"}
MAX_FILE_SIZE = 1_000_000  # 1MB

# 프로젝트 루트에 숨김 캐시 파일 경로 지정
CACHE_FILE = os.path.normpath(os.path.abspath(CACHE_FILE_PATH))

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
    """폴더 내의 항목을 조회하거나, 키워드/카테고리/용량/하위폴더 탐색 조건을 걸어 파일을 검색합니다."""
    try:
        clean_path = path.strip().strip("'\"")
    
        # "D:" 또는 "d:" 처럼 슬래시 없는 드라이브 문자가 들어온 경우 강제로 루트 경로로 보정
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

                # 단순 폴더 조회일 때는 용량 검사 패스
                matches.append({
                    "name": item,
                    "path": full_path,
                    "type": "directory" if is_dir else "file"
                })
                if len(matches) >= max_results:
                    break
        else:
            scanned_count = 0
            MAX_SCANNED_LIMIT = 50000
            
            for root, dirs, files in os.walk(abs_path, topdown=True, onerror=None):
                dirs[:] = [
                    d for d in dirs 
                    if d.lower() not in EXCLUDE_DIRS and not d.startswith("$") and not d.startswith(".")
                ]

                for file in files:
                    scanned_count += 1
                    
                    if scanned_count >= MAX_SCANNED_LIMIT:
                        print(f"[Scan Limit Reached] 최대 스캔 한도 {MAX_SCANNED_LIMIT}개에 도달했습니다.")
                        dirs.clear()
                        break
                    
                    ext = os.path.splitext(file)[1].lower()
                    if target_extensions and ext not in target_extensions:
                        continue

                    if keyword and keyword.lower() not in file.lower():
                        continue

                    full_path = os.path.join(root, file)
                    
                    size_mb = 0
                    need_stat = (min_size_mb > 0) or (max_size_mb > 0)
                    
                    # 파일의 크기를 확인해야 할 때만 검사
                    if need_stat:
                        try:
                            stat = os.stat(full_path)
                            size_mb = round(stat.st_size / (1024 * 1024), 2)

                            # 용량 조건 필터링
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

                if len(matches) >= max_results or scanned_count >= MAX_SCANNED_LIMIT:
                    break

        # 검색된 원본 전체를 디스크 캐시에 확실하게 기록
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(matches, f, ensure_ascii=False)
        except Exception as e:
            print(f"[캐시 저장 실패]: {e}")

        # LLM에게는 요약만 반환
        return {
            "success": True,
            "target_path": abs_path,
            "total_found": len(matches),
            "hit_limit": len(matches) >= max_results,
            "message": f"총 {len(matches)}개 파일이 검색되어 캐시에 보관되었습니다."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def export_search_results_to_file(dest_path: str = "search_result.txt", keyword: str = "") -> dict:
    """최근 검색 결과 캐시에서 전체 또는 특정 키워드가 포함된 목록만 텍스트 파일로 저장합니다."""
    if not os.path.exists(CACHE_FILE):
        return {"success": False, "error": "저장할 최근 검색 결과가 없습니다. 먼저 검색을 실행해 주세요."}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached_results = json.load(f)

        if not cached_results:
            return {"success": False, "error": "저장할 최근 검색 결과가 비어 있습니다."}

        # 키워드가 들어온 경우 캐시 내에서 2차 필터링
        if keyword.strip():
            clean_kw = keyword.strip().lower()
            target_list = [item for item in cached_results if clean_kw in item["name"].lower()]
        else:
            target_list = cached_results

        if not target_list:
            return {"success": False, "error": f"검색 결과 중 '{keyword}'(이)가 포함된 파일이 없습니다."}

        abs_dest = os.path.normpath(os.path.abspath(dest_path))
        safe_path = get_unique_filepath(abs_dest)

        title_kw = f" [필터: '{keyword}']" if keyword.strip() else ""
        lines = [f"=== 검색 결과 목록{title_kw} (총 {len(target_list)}개) ===\n"]
        for idx, item in enumerate(target_list, 1):
            lines.append(f"{idx}. {item['name']} | {item.get('size_mb', 0)}MB | {item['path']}\n")

        with open(safe_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return {
            "success": True,
            "saved_count": len(target_list),
            "saved_path": safe_path
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def read_file(path: str) -> dict:
    """텍스트 파일의 내용을 읽어 반환합니다."""
    try:
        abs_path = os.path.abspath(path)

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

        # 인코딩 문제 방지를 위해 utf-8 기본, 실패 시 cp949(EUC-KR) 시도
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
        abs_path = os.path.abspath(path)

        # 상위 디렉터리가 없으면 자동 생성
        parent_dir = os.path.dirname(abs_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        # 파일 쓰기 (w: 덮어쓰기/새로만들기, a: 이어쓰기)
        write_mode = "a" if mode == "append" else "w"
        with open(abs_path, write_mode, encoding="utf-8") as f:
            f.write(content)

        return {
            "success": True,
            "path": abs_path,
            "message": f"파일이 성공적으로 {'수정' if write_mode == 'a' else '생성/저장'}되었습니다."
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def delete_file(path: str) -> dict:
    """지정한 파일이나 폴더를 영구 삭제하지 않고 휴지통으로 안전하게 이동시킵니다."""
    try:
        abs_path = os.path.abspath(path)

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"파일을 찾을 수 없습니다: {abs_path}"}

        # 휴지통으로 이동 (영구 삭제 방지)
        send2trash(abs_path)

        return {
            "success": True,
            "path": abs_path,
            "message": "파일이 휴지통으로 안전하게 이동되었습니다. (영구 삭제는 휴지통에서 직접 진행해야 합니다.)"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def move_file(source_path: str, dest_path: str) -> dict:
    """파일이나 폴더를 다른 경로로 이동하거나 이름을 변경합니다."""
    try:
        abs_src = os.path.abspath(source_path)
        abs_dst = os.path.abspath(dest_path)
        shutil.move(abs_src, abs_dst)
        return {"success": True, "message": f"{abs_src} -> {abs_dst} 이동 완료"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_in_last_search(keyword: str) -> dict:
    """최근 검색된 파일 캐시(.search_cache.json) 내에서 특정 키워드가 포함된 항목이 있는지 확인합니다."""
    if not os.path.exists(CACHE_FILE):
        return {"success": False, "error": "최근 검색 결과가 없습니다. 먼저 파일 검색을 실행해 주세요."}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached_results = json.load(f)

        if not cached_results:
            return {"success": False, "error": "최근 검색 결과가 비어 있습니다."}

        # 대소문자 구분 없이 키워드 매칭
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
            "matches": matched[:5]  # LLM 컨텍스트 보호를 위해 최대 5개까지만 노출
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# 도구 정의 리스트 (각 도구를 별도 딕셔너리로 분리)
FILES_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "폴더 내의 파일/폴더 목록을 조회하거나, 키워드·카테고리·용량(MB)·하위폴더(재귀) 조건으로 파일을 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "조회 또는 검색할 기준 폴더 경로 (기본값: '.')"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "파일명이나 폴더명에 포함될 검색어 (단순 목록 조회 시 비워둠)"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["video", "audio", "image", "document"],
                        "description": "파일 종류 필터 (동영상 요청 시 'video', 문서 요청 시 'document' 지정)"
                    },
                    "min_size_mb": {
                        "type": "number",
                        "description": "최소 파일 크기 단위: MB (예: 1GB 이상이면 1024)"
                    },
                    "max_size_mb": {
                        "type": "number",
                        "description": "최대 파일 크기 단위: MB (예: 2GB 이하이면 2048)"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "하위 폴더까지 깊숙이 뒤질지 여부 (기본값: false)"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_search_results_to_file",
            "description": "최근 검색된 파일 목록을 텍스트 파일(.txt)로 저장합니다. 전체를 저장하거나, 특정 단어가 포함된 것만 골라서 저장할 수 있습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dest_path": {
                        "type": "string",
                        "description": "저장할 파일 경로 (예: 'F:\\search_result.txt')"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "특정 단어가 포함된 항목만 필터링하여 저장하려는 경우 지정 (기본값: 전체 저장)"
                    }
                },
                "required": ["dest_path"]
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
                    "path": {
                        "type": "string",
                        "description": "읽을 텍스트 파일의 상대 또는 절대 경로"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "지정한 경로에 텍스트나 소스 코드 내용을 새로 작성하거나 저장합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "작성하거나 저장할 파일의 상대 또는 절대 경로 (예: 'calc.py')"
                    },
                    "content": {
                        "type": "string",
                        "description": "파일에 들어갈 전체 코드 또는 텍스트 내용"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "지정한 파일을 영구 삭제하지 않고 휴지통으로 안전하게 이동합니다. 사용자가 삭제를 요구할 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "휴지통으로 보낼 파일의 경로"
                    }
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
                    "source_path": {
                        "type": "string",
                        "description": "이동하거나 이름을 바꿀 원본 파일/폴더 경로"
                    },
                    "dest_path": {
                        "type": "string",
                        "description": "이동할 대상 경로 또는 변경할 새 파일/폴더 경로"
                    }
                },
                "required": ["source_path", "dest_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_in_last_search",
            "description": "최근 검색된 파일 결과 목록 내에서 사용자가 언급한 특정 단어나 파일명이 포함되어 있는지 빠르게 확인합니다. 사용자가 '그중에 ~ 파일 있어?'라고 물을 때 호출합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "찾으려는 파일명이나 키워드 (예: '어벤져스', '키코드')"
                    }
                },
                "required": ["keyword"]
            }
        }
    }
]
