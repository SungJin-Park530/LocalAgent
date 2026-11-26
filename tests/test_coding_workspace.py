import pytest
from unittest.mock import MagicMock

from tools.coding_workspace import (
    CODING_READ_SCHEMAS,
    CODING_READ_TOOLS,
    create_coding_read_tools,
    MAX_TREE_ITEMS,
    list_project_tree,
    read_text_safe,
    resolve_safe_path,
    write_text_safe,
    WorkspaceSnapshotManager,
    CODING_WRITE_TOOLS,
    create_coding_write_tools,
)


def test_rejects_parent_escape_and_invalid_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(PermissionError):
        resolve_safe_path(root, "../outside.txt")
    with pytest.raises(FileNotFoundError):
        resolve_safe_path(root / "missing", ".")
    file_path = root / "file.txt"
    file_path.write_text("test", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        resolve_safe_path(file_path, ".")


def test_rejects_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")
    with pytest.raises(PermissionError):
        resolve_safe_path(root, "link/file.txt")
    assert "link" not in list_project_tree(root)


def test_tree_ignores_dependencies_and_supports_override(tmp_path):
    for name in (".venv", "node_modules", "__pycache__"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "hidden.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("pass", encoding="utf-8")
    tree = list_project_tree(tmp_path)
    assert "app.py" in tree
    assert all(name not in tree for name in (".venv", "node_modules", "__pycache__", "package-lock.json"))
    included = list_project_tree(tmp_path, include_ignored=True)
    assert all(name in included for name in (".venv", "node_modules", "__pycache__", "package-lock.json"))


def test_asset_read_and_tree_report_metadata(tmp_path):
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00")
    (tmp_path / "unknown.txt").write_bytes(b"abc\x00def")
    for name in ("image.png", "unknown.txt"):
        result = read_text_safe(tmp_path, name)
        assert result.content.startswith(f"[Asset/Binary] {name} (")
        assert result.encoding is None
        assert result.newline is None
        assert "바이너리/에셋" in result.content
        with pytest.raises(ValueError):
            write_text_safe(tmp_path, name, "text")
    assert "image.png [Asset, 5 B]" in list_project_tree(tmp_path)


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_preserves_crlf_and_encoding(tmp_path, encoding):
    path = tmp_path / "sample.txt"
    path.write_bytes("안녕\r\n세계\r\n".encode(encoding))
    result = read_text_safe(tmp_path, path)
    assert result.content == "안녕\r\n세계\r\n"
    assert result.encoding == encoding
    assert result.newline == "\r\n"
    write_text_safe(tmp_path, path, "변경\n내용\n")
    assert path.read_bytes() == "변경\r\n내용\r\n".encode(encoding)


def test_new_files_default_to_utf8_and_lf(tmp_path):
    write_text_safe(tmp_path, "new.txt", "안녕\r\n세계\r\n")
    assert (tmp_path / "new.txt").read_bytes() == "안녕\n세계\n".encode("utf-8")


def test_tree_depth_and_item_limits(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "nested.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    assert "nested.txt" not in list_project_tree(tmp_path, max_depth=1)
    assert "항목 수 제한" in list_project_tree(tmp_path, max_items=1)


def test_tree_item_limit_cannot_be_raised(tmp_path):
    for index in range(MAX_TREE_ITEMS + 1):
        (tmp_path / f"{index:04}.txt").touch()
    tree = list_project_tree(tmp_path, max_items=MAX_TREE_ITEMS + 100)
    assert len(tree.splitlines()) == MAX_TREE_ITEMS + 2
    assert "항목 수 제한" in tree


def test_coding_page_applies_workspace_and_displays_tree(tmp_path, monkeypatch):
    from views import coding

    class SessionState(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    streamlit = MagicMock()
    streamlit.session_state = SessionState()
    streamlit.text_input.return_value = str(tmp_path)
    streamlit.button.return_value = True
    (tmp_path / "app.py").write_text("pass", encoding="utf-8")
    monkeypatch.setattr(coding, "st", streamlit)
    coding.render_coding_page()
    assert streamlit.session_state.coding_workspace_root == str(tmp_path.resolve())
    assert streamlit.session_state.coding_selected_model in coding.MODEL_PROFILES
    assert "app.py" in streamlit.code.call_args.args[0]
    assert streamlit.success.call_count == 2


def test_coding_page_rejects_invalid_workspace_without_replacing_current(tmp_path, monkeypatch):
    from views import coding

    class SessionState(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    streamlit = MagicMock()
    streamlit.session_state = SessionState(coding_workspace_root=str(tmp_path))
    streamlit.text_input.return_value = str(tmp_path / "missing")
    streamlit.button.return_value = True
    monkeypatch.setattr(coding, "st", streamlit)
    coding.render_coding_page()
    assert streamlit.session_state.coding_workspace_root == str(tmp_path)
    streamlit.error.assert_called_once()


def test_coding_page_prompts_when_no_workspace(monkeypatch):
    from views import coding

    class SessionState(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    streamlit = MagicMock()
    streamlit.session_state = SessionState()
    streamlit.text_input.return_value = ""
    streamlit.button.return_value = False
    monkeypatch.setattr(coding, "st", streamlit)
    coding.render_coding_page()
    streamlit.info.assert_called_once()
    streamlit.code.assert_not_called()


@pytest.fixture
def coding_tools(tmp_path):
    return {item.name: item for item in create_coding_read_tools(tmp_path)}


def test_repo_map_python_and_typescript(tmp_path, coding_tools):
    (tmp_path / "sample.py").write_text(
        "class Example(Base):\n    def method(self, value: int) -> str:\n        return 'SECRET_BODY'\n\nasync def fetch(x=1):\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.tsx").write_text(
        "export interface User { name: string }\nexport function greet(name: string) { return 'SECRET_BODY'; }\nexport const run = (x: number): number => x + 1;\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    result = coding_tools["get_repo_map"].invoke({})
    for expected in ("L1 class Example(Base):", "L2   def method(self, value: int) -> str:",
                     "L5 async def fetch(x=1):", "L1 export interface User",
                     "L2 export function greet(name: string)", "L3 export const run = (x: number): number =>",
                     "[SyntaxError] 파싱 불가"):
        assert expected in result
    assert "SECRET_BODY" not in result


def test_read_file_lines_ranges_and_asset(tmp_path, coding_tools):
    (tmp_path / "source.py").write_text("\n".join(f"line {number}" for number in range(1, 401)), encoding="utf-8")
    read = coding_tools["read_file_lines"]
    result = read.invoke({"file_path": "source.py", "start_line": 12, "end_line": 15})
    assert "Showing lines 12-15 of 400" in result
    assert "12 | line 12" in result and "15 | line 15" in result
    assert "16 | line 16" not in result
    result = read.invoke({"file_path": "source.py", "end_line": 1000})
    assert "Showing lines 1-300 of 400" in result and "최대 300줄" in result
    assert "Showing lines 399-400 of 400" in read.invoke({"file_path": "source.py", "start_line": 399, "end_line": 999})
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00")
    assert "[Asset/Binary] image.png" in read.invoke({"file_path": "image.png"})


def test_search_ignores_assets_and_dependencies(tmp_path, coding_tools):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("find ME", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"find ME")
    (tmp_path / "app.py").write_text("first\nfind ME\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text("find ME", encoding="utf-8")
    search = coding_tools["search_in_project"]
    result = search.invoke({"query": "find me", "file_ext": ".py"})
    assert "app.py:2 | find ME" in result
    assert "hidden.py" not in result and "image.png" not in result and "app.ts" not in result
    assert "검색 결과가 없습니다" in search.invoke({"query": "find me", "case_sensitive": True})


@pytest.mark.parametrize("name,arguments", [
    ("list_project_tree", {"sub_dir": "../"}),
    ("get_repo_map", {"sub_dir": "../"}),
    ("read_file_lines", {"file_path": "../secret.txt"}),
    ("search_in_project", {"query": "secret", "sub_dir": "../"}),
])
def test_read_tools_reject_escape(coding_tools, name, arguments):
    with pytest.raises(PermissionError):
        coding_tools[name].invoke(arguments)


def test_tool_binding_isolated_and_not_registered(tmp_path):
    from tools import ALL_TOOLS, CHAT_TOOLS

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "only.txt").write_text("first", encoding="utf-8")
    (second / "only.txt").write_text("second", encoding="utf-8")
    first_tools = {item.name: item for item in create_coding_read_tools(first)}
    second_tools = {item.name: item for item in create_coding_read_tools(second)}
    assert "first" in first_tools["read_file_lines"].invoke({"file_path": "only.txt"})
    assert "second" in second_tools["read_file_lines"].invoke({"file_path": "only.txt"})
    assert "first" in first_tools["read_file_lines"].invoke({"file_path": "only.txt"})
    assert len(CODING_READ_TOOLS) == len(CODING_READ_SCHEMAS) == 4
    assert all("workspace_root" not in item.args for item in first_tools.values())
    assert not {item.name for item in CODING_READ_TOOLS} & {item.name for item in [*ALL_TOOLS, *CHAT_TOOLS]}


def test_repo_map_limits_and_ignored_folders(tmp_path, coding_tools):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.py").write_text("def hidden(): pass", encoding="utf-8")
    (tmp_path / "first.py").write_text("\n".join(f"def method_{index}(): pass" for index in range(60)), encoding="utf-8")
    (tmp_path / "second.py").write_text("def second(): pass", encoding="utf-8")
    result = coding_tools["get_repo_map"].invoke({"max_files": 1})
    assert "method_49" in result and "method_50" not in result
    assert "심볼 수 제한" in result and "파일 수 제한" in result
    assert "hidden" not in result


def test_search_result_and_line_limits(tmp_path, coding_tools):
    (tmp_path / "many.txt").write_text("\n".join("needle " + "x" * 600 for _ in range(31)), encoding="utf-8")
    result = coding_tools["search_in_project"].invoke({"query": "needle"})
    assert result.count("many.txt:") == 30
    assert "최대 30건" in result and "500자" in result


def test_list_tree_subdirectory(coding_tools, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("pass", encoding="utf-8")
    (tmp_path / "other.txt").write_text("other", encoding="utf-8")
    result = coding_tools["list_project_tree"].invoke({"sub_dir": "src"})
    assert "src/" in result and "app.py" in result and "other.txt" not in result


@pytest.fixture
def write_tools(tmp_path):
    manager = WorkspaceSnapshotManager(tmp_path)
    return {item.name: item for item in create_coding_write_tools(tmp_path, manager)}, manager


def test_replace_exact_and_duplicate(tmp_path, write_tools):
    tools, manager = write_tools
    path = tmp_path / "sample.py"
    path.write_text("value = 1\nother = 1\n", encoding="utf-8")
    replace = tools["replace_in_file"]
    assert "수정 완료" in replace.invoke({"file_path": "sample.py", "old_block": "value = 1", "new_block": "value = 2"})
    assert path.read_text(encoding="utf-8") == "value = 2\nother = 1\n"
    assert "2곳" in replace.invoke({"file_path": "sample.py", "old_block": " = ", "new_block": " = 3 #"})
    assert path.read_text(encoding="utf-8") == "value = 2\nother = 1\n"
    assert "-value = 1" in manager.get_unified_diff()


def test_fuzzy_replace_preserves_relative_indentation_and_newline(tmp_path, write_tools):
    tools, _ = write_tools
    path = tmp_path / "sample.py"
    path.write_bytes(b"def example():\r\n    if True:\r\n        return 1\r\n")
    result = tools["replace_in_file"].invoke({
        "file_path": "sample.py", "old_block": "if True:\n    return 1",
        "new_block": "if False:\n    if True:\n        return 2",
    })
    assert "수정 완료" in result
    assert path.read_bytes() == b"def example():\r\n    if False:\r\n        if True:\r\n            return 2\r\n"


def test_fuzzy_ambiguity_and_missing_are_rejected(tmp_path, write_tools):
    tools, _ = write_tools
    path = tmp_path / "sample.txt"
    path.write_text("  same\n    same\n", encoding="utf-8")
    replace = tools["replace_in_file"]
    assert "2곳" in replace.invoke({"file_path": "sample.txt", "old_block": " same", "new_block": "other"})
    assert "0곳" in replace.invoke({"file_path": "sample.txt", "old_block": "missing", "new_block": "other"})
    assert path.read_text(encoding="utf-8") == "  same\n    same\n"


def test_syntax_guard_and_no_snapshot_on_failure(tmp_path, write_tools):
    tools, manager = write_tools
    path = tmp_path / "sample.py"
    path.write_bytes(b"print(1)\r\n")
    result = tools["replace_in_file"].invoke({"file_path": "sample.py", "old_block": "print(1)", "new_block": "print(1"})
    assert "[SyntaxError]" in result and "L1" in result
    assert path.read_bytes() == b"print(1)\r\n"
    result = tools["create_or_overwrite_file"].invoke({"file_path": "new.py", "content": "def broken(:\n"})
    assert "[SyntaxError]" in result and not (tmp_path / "new.py").exists()
    assert manager.undo_last_turn() == []


def test_create_overwrite_protection_and_parent_creation(tmp_path, write_tools):
    tools, _ = write_tools
    create = tools["create_or_overwrite_file"]
    path = tmp_path / "src" / "new.py"
    assert "저장 완료" in create.invoke({"file_path": "src/new.py", "content": "value = 1\n"})
    assert "이미 존재하는 파일" in create.invoke({"file_path": "src/new.py", "content": "value = 2\n"})
    assert path.read_text(encoding="utf-8") == "value = 1\n"
    assert "저장 완료" in create.invoke({"file_path": "src/new.py", "content": "value = 2\n", "allow_overwrite": True})
    assert path.read_text(encoding="utf-8") == "value = 2\n"


def test_snapshot_diff_and_undo_preserve_original_bytes(tmp_path, write_tools):
    tools, manager = write_tools
    path = tmp_path / "original.txt"
    original = "원본\r\n두번째\r\n".encode("cp949")
    path.write_bytes(original)
    replace = tools["replace_in_file"]
    replace.invoke({"file_path": "original.txt", "old_block": "원본", "new_block": "변경"})
    replace.invoke({"file_path": "original.txt", "old_block": "변경", "new_block": "최종"})
    tools["create_or_overwrite_file"].invoke({"file_path": "nested/new.txt", "content": "새 파일\n"})
    diff = manager.get_unified_diff()
    assert "-원본" in diff and "+최종" in diff and "+새 파일" in diff
    assert set(manager.undo_last_turn()) == {"original.txt", "nested/new.txt"}
    assert path.read_bytes() == original
    assert not (tmp_path / "nested" / "new.txt").exists()
    assert manager.undo_last_turn() == []


def test_write_tools_block_assets_escape_and_wrong_manager(tmp_path, write_tools):
    tools, manager = write_tools
    (tmp_path / "image.png").write_bytes(b"image")
    assert "에셋" in tools["replace_in_file"].invoke({"file_path": "image.png", "old_block": "image", "new_block": "text"})
    assert "에셋" in tools["create_or_overwrite_file"].invoke({"file_path": "new.png", "content": "text"})
    with pytest.raises(PermissionError):
        tools["create_or_overwrite_file"].invoke({"file_path": "../escape.txt", "content": "text"})
    with pytest.raises(PermissionError):
        tools["replace_in_file"].invoke({"file_path": "../escape.txt", "old_block": "a", "new_block": "b"})
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        create_coding_write_tools(other, manager)


def test_write_tool_manager_available_and_separate_from_chat(tmp_path):
    from tools import ALL_TOOLS, CHAT_TOOLS

    tools = create_coding_write_tools(tmp_path)
    assert tools[0].snapshot_manager is tools[1].snapshot_manager
    assert all("workspace_root" not in item.args and "snapshot_manager" not in item.args for item in tools)
    assert not {item.name for item in CODING_WRITE_TOOLS} & {item.name for item in [*ALL_TOOLS, *CHAT_TOOLS]}


def test_snapshot_turn_boundary_and_diff_without_final_newline(tmp_path, write_tools):
    tools, manager = write_tools
    path = tmp_path / "sample.txt"
    path.write_bytes(b"first")
    replace = tools["replace_in_file"]
    replace.invoke({"file_path": "sample.txt", "old_block": "first", "new_block": "second"})
    assert "-first\n\\ No newline at end of file\n+second\n" in manager.get_unified_diff()
    manager.begin_turn()
    replace.invoke({"file_path": "sample.txt", "old_block": "second", "new_block": "third"})
    assert manager.undo_last_turn() == ["sample.txt"]
    assert path.read_bytes() == b"second"


def test_diff_reports_empty_new_file(tmp_path, write_tools):
    tools, manager = write_tools
    tools["create_or_overwrite_file"].invoke({"file_path": "empty.txt", "content": ""})
    assert "--- /dev/null\n+++ b/empty.txt\n" in manager.get_unified_diff()
    assert manager.undo_last_turn() == ["empty.txt"]
    assert not (tmp_path / "empty.txt").exists()