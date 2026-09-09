# Local Agent

Ollama와 로컬 도구를 사용해 파일 작업을 수행하는 간단한 CLI 에이전트입니다.

프로젝트의 주요 디렉토리와 파일은 루트의 실행 및 설정 파일(`agent.py`, `app.py`, `engine.py`, `room_manager.py`, `requirements.txt`)을 중심으로, `config/`에 설정과 카테고리 정의를, `prompts/`에 프롬프트를, `tools/`에 채팅 및 파일 도구를, `cache/`에 검색 캐시를, `search_result/`에 검색 결과를, `legacy/`에 이전 시스템 프롬프트를 저장하는 구조입니다.

```text
LocalAgent/
├── agent.py
├── app.py
├── engine.py
├── progress.md
├── README.md
├── requirements.txt
├── room_manager.py
├── cache/
│   └── search_cache.json
├── config/
│   ├── __init__.py
│   ├── categories.py
│   ├── settings.py
│   └── settings.py.example
├── legacy/
│   └── system.md
├── prompts/
│   ├── 01_persona.md
│   ├── 01_persona.md.example
│   ├── 02_chat.md
│   ├── 02_general.md.example
│   ├── 03_files.md
│   └── 03_files.md.example
├── search_result/
└── tools/
	├── __init__.py
	├── chat_utils.py
	└── files.py
```

## 준비

- Python 3 설치
- [Ollama](https://ollama.com/) 설치
- 사용할 모델 다운로드

프로젝트 폴더에서 가상환경을 만들고 활성화합니다.

```bash
py -m venv .venv
.venv\Scripts\activate
```

Git Bash에서는 활성화 명령이 다릅니다.

```bash
source .venv/Scripts/activate
```

활성화한 환경에 의존성과 모델을 준비합니다.

```bash
ollama pull qwen3:30b-a3b
py -m pip install -r requirements.txt
```

## 시스템 프롬프트 설정

개인 설정이 담기는 시스템 프롬프트는 Git에 포함되지 않습니다. 예제 파일을 복사한 뒤 내용을 채웁니다.

```bash
copy prompts\system.md.example prompts\system.md
```

Git Bash에서는 다음 명령을 사용합니다.

```bash
cp prompts/system.md.example prompts/system.md
```

## 실행

먼저 별도 터미널에서 Ollama 서버를 실행합니다.

```bash
ollama serve
```

그 다음 프로젝트 폴더에서 에이전트를 실행합니다.

```bash
py agent.py
```

대화를 종료하려면 `exit` 또는 `quit`을 입력합니다.