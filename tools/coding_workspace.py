import ast
import difflib
import os
import re
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, NamedTuple

from langchain_core.tools import StructuredTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


DEFAULT_IGNORE_DIRS = frozenset({
    ".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".next",
    ".nuxt", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "coverage", ".idea", ".vscode",
})
DEFAULT_IGNORE_FILES = frozenset({
    ".DS_Store", "Thumbs.db", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "uv.lock",
})
ASSET_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".tif", ".tiff", ".avif", ".mp3", ".mp4", ".wav", ".ogg", ".webm",
    ".mov", ".ttf", ".otf", ".woff", ".woff2", ".pdf", ".zip", ".gz",
    ".tar", ".7z", ".rar", ".db", ".sqlite", ".sqlite3", ".pyc", ".exe",
    ".dll", ".so", ".dylib", ".bin", ".class", ".jar",
})
MAX_TREE_ITEMS = 500


class TextFile(NamedTuple):
    content: str
    encoding: str | None
    newline: str | None


def resolve_safe_path(workspace_root: str | Path, target_path: str | Path) -> Path:
    root = Path(workspace_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {root}")
    target = Path(target_path)
    resolved = (root / target).resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError(f"Path is outside the workspace: {target}")
    return resolved


def is_binary_or_asset(path: str | Path) -> bool:
    path = Path(path)
    if path.suffix.lower() in ASSET_EXTENSIONS:
        return True
    with path.open("rb") as file:
        while chunk := file.read(65536):
            if b"\x00" in chunk:
                return True
    return False


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    amount = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        amount /= 1024
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
    return f"{size} B"


def format_asset_metadata(path: str | Path, workspace_root: str | Path | None = None) -> str:
    path = Path(path)
    display_path = path.relative_to(Path(workspace_root).resolve()) if workspace_root else path
    return (
        f"[Asset/Binary] {display_path} ({_format_size(path.stat().st_size)}) - "
        "바이너리/에셋 파일이므로 본문을 읽지 않고 경로 및 파일명만 참조합니다."
    )


def read_text_safe(workspace_root: str | Path, target_path: str | Path) -> TextFile:
    path = resolve_safe_path(workspace_root, target_path)
    if is_binary_or_asset(path):
        return TextFile(format_asset_metadata(path, workspace_root), None, None)
    data = path.read_bytes()
    try:
        content = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = data.decode("cp949")
        encoding = "cp949"
    newline = "\r\n" if "\r\n" in content else "\n"
    return TextFile(content, encoding, newline)


def write_text_safe(workspace_root: str | Path, target_path: str | Path, content: str) -> None:
    path = resolve_safe_path(workspace_root, target_path)
    if path.exists():
        original = read_text_safe(workspace_root, target_path)
        if original.encoding is None:
            raise ValueError(f"Cannot write text to an asset or binary file: {path}")
        encoding = original.encoding
        newline = original.newline
    else:
        encoding = "utf-8"
        newline = "\n"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.replace("\n", newline).encode(encoding))


def list_project_tree(
    workspace_root: str | Path,
    max_depth: int = 3,
    max_items: int = MAX_TREE_ITEMS,
    include_ignored: bool = False,
) -> str:
    root = resolve_safe_path(workspace_root, ".")
    if max_depth < 0 or max_items < 1:
        raise ValueError("max_depth must be non-negative and max_items must be positive")
    max_items = min(max_items, MAX_TREE_ITEMS)
    lines = [f"{root.name or root.anchor}/"]
    count = 0
    truncated = False

    def visit(directory: Path, depth: int) -> None:
        nonlocal count, truncated
        if depth > max_depth or truncated:
            return
        for entry in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            try:
                resolved = resolve_safe_path(root, entry)
                is_dir = resolved.is_dir()
                if not include_ignored and (
                    (is_dir and entry.name in DEFAULT_IGNORE_DIRS)
                    or (not is_dir and entry.name in DEFAULT_IGNORE_FILES)
                ):
                    continue
                if count >= max_items:
                    truncated = True
                    return
                count += 1
                suffix = "/" if is_dir else ""
                if resolved.is_file() and is_binary_or_asset(resolved):
                    suffix = f" [Asset, {_format_size(resolved.stat().st_size)}]"
                lines.append(f"{'  ' * depth}{entry.name}{suffix}")
                if is_dir and not entry.is_symlink():
                    visit(resolved, depth + 1)
            except PermissionError:
                continue

    visit(root, 1)
    if truncated:
        lines.append("... (항목 수 제한)")
    return "\n".join(lines)


MAX_REPO_FILES = 40
MAX_SYMBOLS_PER_FILE = 50
MAX_READ_LINES = 300
MAX_SEARCH_MATCHES = 30
MAX_SEARCH_LINE_LENGTH = 500
MAX_SCAN_FILES = 10000
MAX_SOURCE_BYTES = 1_000_000
_active_workspace: ContextVar[Path | None] = ContextVar("coding_workspace", default=None)


def set_active_workspace(root: str | Path):
    return _active_workspace.set(resolve_safe_path(root, "."))


def get_active_workspace() -> Path:
    root = _active_workspace.get()
    if root is None:
        raise RuntimeError("No coding workspace is bound")
    return root


def _project_files(root: Path, sub_dir: str):
    directory = resolve_safe_path(root, sub_dir)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    if any(part in DEFAULT_IGNORE_DIRS for part in directory.relative_to(root).parts):
        return
    scanned = 0
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in DEFAULT_IGNORE_DIRS and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            if name in DEFAULT_IGNORE_FILES:
                continue
            scanned += 1
            if scanned > MAX_SCAN_FILES:
                return
            try:
                path = resolve_safe_path(root, Path(current) / name)
                if path.is_file() and path.stat().st_size <= MAX_SOURCE_BYTES:
                    yield path
            except (OSError, RuntimeError):
                continue


def _python_symbols(content: str) -> list[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ["[SyntaxError] 파싱 불가"]
    symbols = []

    def visit(nodes, indent=""):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(base) for base in node.bases]
                bases.extend(ast.unparse(keyword) for keyword in node.keywords)
                suffix = f"({', '.join(bases)})" if bases else ""
                symbols.append(f"L{node.lineno} {indent}class {node.name}{suffix}:")
                visit(node.body, indent + "  ")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                symbols.append(f"L{node.lineno} {indent}{prefix} {node.name}({ast.unparse(node.args)}){returns}:")
            if len(symbols) > MAX_SYMBOLS_PER_FILE:
                return

    visit(tree.body)
    return symbols


_JS_DECLARATION = re.compile(
    r"^\s*(?:(?:export|default|declare|abstract)\s+)*"
    r"(?:(?:async\s+)?function\s+\w+|class\s+\w+|interface\s+\w+|type\s+\w+|"
    r"(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\([^\n]*?\)\s*(?::\s*[^=\n]+)?=>)"
)


def _javascript_symbols(content: str) -> list[str]:
    symbols = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if _JS_DECLARATION.match(line):
            signature = line.strip()
            if "=>" in signature:
                signature = signature.split("=>", 1)[0].rstrip() + " =>"
            elif "{" in signature:
                signature = signature.split("{", 1)[0].rstrip()
            symbols.append(f"L{line_number} {signature}")
            if len(symbols) > MAX_SYMBOLS_PER_FILE:
                break
    return symbols


@tool("list_project_tree")
def coding_list_project_tree(sub_dir: str = ".", max_depth: int = 3) -> str:
    """List the workspace directory tree, optionally starting at a subdirectory."""
    root = get_active_workspace()
    directory = resolve_safe_path(root, sub_dir)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    return list_project_tree(directory, max_depth=max_depth)


@tool
def get_repo_map(sub_dir: str = ".", max_files: int = 40) -> str:
    """Summarize Python and TypeScript/JavaScript declarations in the workspace."""
    root = get_active_workspace()
    if max_files < 1:
        raise ValueError("max_files must be positive")
    limit = min(max_files, MAX_REPO_FILES)
    sections = []
    for path in _project_files(root, sub_dir):
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            continue
        try:
            text = read_text_safe(root, path)
        except (OSError, UnicodeError, RuntimeError):
            continue
        if text.encoding is None:
            continue
        if len(sections) >= limit:
            sections.append("... (파일 수 제한)")
            break
        symbols = _python_symbols(text.content) if path.suffix.lower() == ".py" else _javascript_symbols(text.content)
        truncated = len(symbols) > MAX_SYMBOLS_PER_FILE
        symbols = symbols[:MAX_SYMBOLS_PER_FILE]
        if truncated:
            symbols.append("... (심볼 수 제한)")
        sections.append(f"{path.relative_to(root).as_posix()}:\n" + "\n".join(symbols or ["(선언 없음)"]))
    return "\n\n".join(sections) if sections else "코드 파일이 없습니다."


@tool
def read_file_lines(file_path: str, start_line: int = 1, end_line: int = 200) -> str:
    """Read a bounded line range from a workspace file with line numbers."""
    root = get_active_workspace()
    if start_line < 1 or end_line < start_line:
        raise ValueError("Line range must start at 1 or later and end at or after start")
    result = read_text_safe(root, file_path)
    if result.encoding is None:
        return result.content
    lines = result.content.splitlines()
    actual_start = min(start_line, len(lines) + 1)
    actual_end = min(end_line, start_line + MAX_READ_LINES - 1, len(lines))
    output = [f"Showing lines {actual_start}-{actual_end} of {len(lines)}"]
    output.extend(f"{index} | {lines[index - 1]}" for index in range(actual_start, actual_end + 1))
    if end_line - start_line + 1 > MAX_READ_LINES and actual_end < min(end_line, len(lines)):
        output.append(f"... (최대 {MAX_READ_LINES}줄로 제한됨)")
    return "\n".join(output)


@tool
def search_in_project(query: str, sub_dir: str = ".", file_ext: str = "", case_sensitive: bool = False) -> str:
    """Search workspace text files for literal matches and return bounded line results."""
    root = get_active_workspace()
    if not query:
        raise ValueError("Search query must not be empty")
    needle = query if case_sensitive else query.casefold()
    matches = []
    clipped = False
    for path in _project_files(root, sub_dir):
        if file_ext and path.suffix.lower() != file_ext.lower():
            continue
        try:
            result = read_text_safe(root, path)
        except (OSError, UnicodeError, RuntimeError):
            continue
        if result.encoding is None:
            continue
        for number, line in enumerate(result.content.splitlines(), 1):
            if needle not in (line if case_sensitive else line.casefold()):
                continue
            if len(matches) >= MAX_SEARCH_MATCHES:
                matches.append(f"... (최대 {MAX_SEARCH_MATCHES}건으로 제한됨)")
                if clipped:
                    matches.append(f"... (일부 줄을 {MAX_SEARCH_LINE_LENGTH}자로 제한함)")
                return "\n".join(matches)
            if len(line) > MAX_SEARCH_LINE_LENGTH:
                line = line[:MAX_SEARCH_LINE_LENGTH] + "..."
                clipped = True
            matches.append(f"{path.relative_to(root).as_posix()}:{number} | {line}")
    if clipped:
        matches.append(f"... (일부 줄을 {MAX_SEARCH_LINE_LENGTH}자로 제한함)")
    return "\n".join(matches) if matches else "검색 결과가 없습니다."


CODING_READ_TOOLS = [coding_list_project_tree, get_repo_map, read_file_lines, search_in_project]
CODING_READ_SCHEMAS = [convert_to_openai_tool(item) for item in CODING_READ_TOOLS]


def create_coding_read_tools(workspace_root: str | Path) -> list[StructuredTool]:
    """Bind independent tool instances to a workspace for use by a coding agent."""
    root = resolve_safe_path(workspace_root, ".")

    def bind(item):
        @wraps(item.func)
        def invoke(*args, **kwargs):
            token = _active_workspace.set(root)
            try:
                return item.func(*args, **kwargs)
            finally:
                _active_workspace.reset(token)

        return StructuredTool.from_function(func=invoke, name=item.name, description=item.description)

    return [bind(item) for item in CODING_READ_TOOLS]


class WorkspaceSnapshot(NamedTuple):
    existed: bool
    content: str
    encoding: str | None
    newline: str | None
    original_bytes: bytes | None


class WorkspaceSnapshotManager:
    """Keep the first pre-write state of each file until the current turn is undone."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = resolve_safe_path(workspace_root, ".")
        self._snapshots: dict[Path, WorkspaceSnapshot] = {}

    def begin_turn(self) -> None:
        """Discard the previous turn's snapshots before starting a new turn."""
        self._snapshots.clear()

    def capture_before_write(self, file_path: str | Path) -> None:
        path = resolve_safe_path(self.workspace_root, file_path)
        relative = path.relative_to(self.workspace_root)
        if relative in self._snapshots:
            return
        if path.exists():
            text = read_text_safe(self.workspace_root, path)
            if text.encoding is None:
                raise ValueError(f"Cannot snapshot an asset or binary file: {relative}")
            self._snapshots[relative] = WorkspaceSnapshot(
                True, text.content, text.encoding, text.newline, path.read_bytes()
            )
        else:
            self._snapshots[relative] = WorkspaceSnapshot(False, "", None, None, None)

    def undo_last_turn(self) -> list[str]:
        restored = []
        for relative, snapshot in list(self._snapshots.items()):
            path = resolve_safe_path(self.workspace_root, relative)
            if snapshot.existed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot.original_bytes)
            elif path.exists():
                path.unlink()
            restored.append(relative.as_posix())
            del self._snapshots[relative]
        return restored

    def get_unified_diff(self) -> str:
        changes = []
        for relative, snapshot in self._snapshots.items():
            path = resolve_safe_path(self.workspace_root, relative)
            current = read_text_safe(self.workspace_root, path).content if path.exists() else ""
            old_lines = snapshot.content.splitlines(keepends=True)
            new_lines = current.splitlines(keepends=True)
            diff = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"a/{relative.as_posix()}" if snapshot.existed else "/dev/null",
                tofile=f"b/{relative.as_posix()}" if path.exists() else "/dev/null",
            ))
            for line in diff:
                changes.append(line)
                if not line.endswith("\n"):
                    changes.append("\n\\ No newline at end of file\n")
            if not diff and snapshot.existed != path.exists():
                old_name = f"a/{relative.as_posix()}" if snapshot.existed else "/dev/null"
                new_name = f"b/{relative.as_posix()}" if path.exists() else "/dev/null"
                changes.append(f"--- {old_name}\n+++ {new_name}\n")
        return "".join(changes)


_active_snapshot_manager: ContextVar[WorkspaceSnapshotManager | None] = ContextVar(
    "coding_snapshot_manager", default=None
)


def _get_snapshot_manager() -> WorkspaceSnapshotManager:
    manager = _active_snapshot_manager.get()
    if manager is None or manager.workspace_root != get_active_workspace():
        raise RuntimeError("No snapshot manager is bound to the active workspace")
    return manager


def _syntax_error(path: Path, content: str) -> str | None:
    if path.suffix.lower() == ".py":
        try:
            ast.parse(content)
        except SyntaxError as error:
            return f"[SyntaxError] {error.msg} (L{error.lineno}); 파일을 저장하지 않았습니다."
    return None


def _normalized_lines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _occurrences(content: str, block: str) -> list[int]:
    positions = []
    start = 0
    while (position := content.find(block, start)) != -1:
        positions.append(position)
        start = position + 1
    return positions


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _adjust_indentation(new_block: str, old_indent: str, original_indent: str) -> str:
    adjusted = []
    for line in new_block.splitlines(keepends=True):
        if not line.strip():
            adjusted.append(line)
            continue
        indent = _indent(line)
        if indent.startswith(old_indent):
            replacement = original_indent + indent[len(old_indent):]
        elif old_indent.startswith(indent) and len(old_indent) - len(indent) <= len(original_indent):
            replacement = original_indent[:len(original_indent) - (len(old_indent) - len(indent))]
        else:
            raise ValueError("들여쓰기를 안전하게 보정할 수 없습니다. read_file_lines로 다시 확인하세요.")
        adjusted.append(replacement + line[len(indent):])
    return "".join(adjusted)


@tool
def replace_in_file(file_path: str, old_block: str, new_block: str) -> str:
    """Replace one unique code block in a workspace text file with syntax validation."""
    root = get_active_workspace()
    path = resolve_safe_path(root, file_path)
    original = read_text_safe(root, path)
    if original.encoding is None:
        return "바이너리/에셋 파일은 수정할 수 없습니다."
    if not old_block:
        return "빈 코드 블록은 치환할 수 없습니다. read_file_lines로 다시 확인하세요."
    content = _normalized_lines(original.content)
    old_block = _normalized_lines(old_block)
    new_block = _normalized_lines(new_block)
    positions = _occurrences(content, old_block)
    if len(positions) > 1:
        return f"동일한 코드 블록이 {len(positions)}곳에서 발견되었습니다. 앞뒤 문맥 줄을 더 포함해서 호출하세요."
    if positions:
        start = positions[0]
        end = start + len(old_block)
    else:
        lines = content.splitlines(keepends=True)
        wanted = old_block.splitlines()
        if not wanted:
            return "코드 블록을 찾을 수 없습니다. read_file_lines로 다시 확인하세요."
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        matches = [index for index in range(len(lines) - len(wanted) + 1)
                   if [line.strip() for line in lines[index:index + len(wanted)]]
                   == [line.strip() for line in wanted]]
        if len(matches) != 1:
            return f"공백 보정 일치 결과가 {len(matches)}곳입니다. read_file_lines로 정확한 줄을 다시 확인하세요."
        index = matches[0]
        start, end = offsets[index], offsets[index + len(wanted)]
        try:
            new_block = _adjust_indentation(new_block, _indent(wanted[0]), _indent(lines[index]))
        except ValueError as error:
            return str(error)
        if lines[index + len(wanted) - 1].endswith("\n") and new_block and not new_block.endswith("\n"):
            new_block += "\n"
    updated = content[:start] + new_block + content[end:]
    if updated == content:
        return "변경할 내용이 없습니다."
    error = _syntax_error(path, updated)
    if error:
        return error
    updated.encode(original.encoding)
    _get_snapshot_manager().capture_before_write(path)
    write_text_safe(root, path, updated)
    start_line = content.count("\n", 0, start) + 1
    end_line = start_line + max(new_block.count("\n") - int(new_block.endswith("\n")), 0)
    return f"{path.relative_to(root).as_posix()}: L{start_line}-L{end_line} 수정 완료"


@tool
def create_or_overwrite_file(file_path: str, content: str, allow_overwrite: bool = False) -> str:
    """Create a workspace text file or explicitly overwrite an existing one."""
    root = get_active_workspace()
    path = resolve_safe_path(root, file_path)
    if path.exists() and not allow_overwrite:
        return "이미 존재하는 파일입니다. 부분 수정은 replace_in_file을 사용하고, 전체 덮어쓰기가 반드시 필요하면 allow_overwrite=True로 호출하세요."
    if path.suffix.lower() in ASSET_EXTENSIONS or (path.exists() and is_binary_or_asset(path)) or "\x00" in content:
        return "바이너리/에셋 파일은 수정할 수 없습니다."
    error = _syntax_error(path, content)
    if error:
        return error
    if path.exists():
        original = read_text_safe(root, path)
        content.encode(original.encoding)
    else:
        content.encode("utf-8")
    _get_snapshot_manager().capture_before_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_safe(root, path, content)
    return f"{path.relative_to(root).as_posix()}: 저장 완료"


CODING_WRITE_TOOLS = [replace_in_file, create_or_overwrite_file]
CODING_WRITE_SCHEMAS = [convert_to_openai_tool(item) for item in CODING_WRITE_TOOLS]


class WorkspaceWriteTool(StructuredTool):
    snapshot_manager: Any = Field(exclude=True)


def create_coding_write_tools(
    workspace_root: str | Path, snapshot_manager: WorkspaceSnapshotManager | None = None,
) -> list[WorkspaceWriteTool]:
    """Create workspace-bound write tools sharing one accessible snapshot manager."""
    root = resolve_safe_path(workspace_root, ".")
    manager = snapshot_manager if snapshot_manager is not None else WorkspaceSnapshotManager(root)
    if manager.workspace_root != root:
        raise ValueError("Snapshot manager belongs to a different workspace")

    def bind(item):
        @wraps(item.func)
        def invoke(*args, **kwargs):
            workspace_token = _active_workspace.set(root)
            manager_token = _active_snapshot_manager.set(manager)
            try:
                return item.func(*args, **kwargs)
            finally:
                _active_snapshot_manager.reset(manager_token)
                _active_workspace.reset(workspace_token)

        return WorkspaceWriteTool.from_function(
            func=invoke, name=item.name, description=item.description, snapshot_manager=manager,
        )

    return [bind(item) for item in CODING_WRITE_TOOLS]