# 파일 조회 기능을 담당하는 코드

import os
from send2trash import send2trash
import shutil

ALLOWED_EXTENSIONS = {".txt", ".py", ".md", ".json", ".csv", ".log"}
MAX_FILE_SIZE = 1_000_000  # 1MB

def list_files(path: str = ".") -> dict:
    """지정한 폴더의 파일과 폴더 목록을 반환합니다."""
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"경로가 존재하지 않습니다: {abs_path}"}

        items = os.listdir(abs_path)
        result = [
            {
                "name": item,
                "type": "directory" if os.path.isdir(os.path.join(abs_path, item)) else "file"
            }
            for item in items
        ]

        return {
            "success": True,
            "path": abs_path,
            "items": result
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

def search_files(path: str = ".", keyword: str = "") -> dict:
    """지정한 폴더 내에서 특정 키워드가 포함된 파일이나 폴더를 빠르게 찾아냅니다."""
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"경로가 존재하지 않습니다: {abs_path}"}

        matches = []
        # 지정한 경로 1단계 탐색 (원할 경우 os.walk로 하위까지 확장 가능)
        for item in os.listdir(abs_path):
            if keyword.lower() in item.lower():
                full_path = os.path.join(abs_path, item)
                matches.append({
                    "name": item,
                    "path": full_path,
                    "type": "directory" if os.path.isdir(full_path) else "file"
                })

        return {
            "success": True,
            "keyword": keyword,
            "total_matches": len(matches),
            "matches": matches
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# 도구 정의 리스트 (각 도구를 별도 딕셔너리로 분리)
FILES_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "지정한 폴더의 파일 및 디렉터리 목록을 확인합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "확인할 폴더 경로 (기본값: 현재 폴더 '.')"
                    }
                },
                "required": ["path"]
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
            "name": "search_files",
            "description": "특정 폴더 안에서 이름에 키워드가 포함된 파일이나 디렉터리를 빠르게 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "검색을 시작할 폴더 경로 (예: 'E:/', '.')"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "찾고자 하는 파일명이나 확장자 일부 (예: 'move_test', '.txt')"
                    }
                },
                "required": ["path", "keyword"]
            }
        }
    }
]
