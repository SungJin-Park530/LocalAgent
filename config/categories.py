# 에이전트가 인식하는 파일 확장자 분류
FILE_CATEGORIES = {
    "video": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts"],
    "audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"],
    "image": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff"],
    "document": [".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".hwp", ".csv", ".json", ".md"],
    "archive": [".zip", ".rar", ".7z", ".tar", ".gz", ".iso"]
}

# 탐색에서 원천 배제할 시스템, 빌드, 캐시, 대량 라이브러리 디렉터리
EXCLUDE_DIRS = {
    "$recycle.bin", 
    "system volume information", 
    "recovery", 
    "config.msi",
    "$winre_backup",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "appdata",
    "programdata",
    "windows"
}