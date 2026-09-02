# Local Agent

Ollama와 로컬 도구를 사용해 파일 작업을 수행하는 간단한 CLI 에이전트입니다.

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