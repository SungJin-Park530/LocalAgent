import re
from pathlib import Path

from config.settings import PROMPTS_DIR


PERSONA_PATTERN = re.compile(r"^\d+_persona_(?P<key>.+)\.md$")
TOOL_PATTERN = re.compile(r"^\d+_(?P<key>.+)\.md$")
WORKFLOW_PATTERN = re.compile(r"^\d+_(?P<key>.+)_workflow\.md$")


def _read_prompt(path: Path) -> str:
    with path.open("r", encoding="utf-8") as prompt_file:
        return prompt_file.read().strip()


def get_available_personas(prompts_dir: str = PROMPTS_DIR) -> list[dict[str, str]]:
    persona_dir = Path(prompts_dir) / "persona"
    personas = []
    for path in sorted(persona_dir.glob("*.md")):
        match = PERSONA_PATTERN.match(path.name)
        if match and not path.name.startswith("00_"):
            key = match.group("key").strip()
            personas.append({"key": key, "display": key or "기본 정보"})
    return personas


def get_available_prompt_tools(prompts_dir: str = PROMPTS_DIR) -> list[str]:
    tools_dir = Path(prompts_dir) / "tools"
    tool_keys = []
    for path in sorted(tools_dir.glob("*.md")):
        match = TOOL_PATTERN.match(path.name)
        if match and not path.name.startswith("00_"):
            tool_keys.append(match.group("key"))
    return tool_keys


def assemble_system_prompt(
    selected_persona: str | None,
    selected_tools: list[str],
) -> str:
    prompts_root = Path(PROMPTS_DIR)
    sections = []

    if selected_persona:
        general_path = prompts_root / "persona" / "00_persona_general.md"
        if general_path.is_file():
            sections.append(_read_prompt(general_path))

        persona_key = Path(selected_persona).stem
        persona_key = re.sub(r"^\d+_persona_", "", persona_key)
        for persona in get_available_personas():
            if persona["key"] == persona_key:
                persona_path = next(
                    path
                    for path in (prompts_root / "persona").glob("*.md")
                    if (match := PERSONA_PATTERN.match(path.name))
                    and match.group("key") == persona_key
                )
                sections.append(_read_prompt(persona_path))
                break

    tools_dir = prompts_root / "tools"
    workflow_dir = prompts_root / "workflow"
    tool_files = {
        match.group("key"): path
        for path in sorted(tools_dir.glob("*.md"))
        if not path.name.startswith("00_")
        and (match := TOOL_PATTERN.match(path.name))
    }
    workflow_files = {
        match.group("key"): path
        for path in sorted(workflow_dir.glob("*.md"))
        if not path.name.startswith("00_")
        and (match := WORKFLOW_PATTERN.match(path.name))
    }

    selected_keys = []
    for selected_tool in selected_tools:
        key = Path(selected_tool).stem
        key = re.sub(r"^\d+_", "", key)
        if key not in selected_keys:
            selected_keys.append(key)

    for key in selected_keys:
        tool_path = tool_files.get(key)
        workflow_path = workflow_files.get(key)
        if tool_path:
            sections.append(_read_prompt(tool_path))
        if workflow_path:
            sections.append(_read_prompt(workflow_path))

    if not selected_keys:
        chat_path = workflow_dir / "00_chat.md"
        if not chat_path.is_file():
            chat_path = workflow_dir / "00_chat_workflow.md"
        if chat_path.is_file():
            sections.append(_read_prompt(chat_path))

    return "\n\n".join(section for section in sections if section)