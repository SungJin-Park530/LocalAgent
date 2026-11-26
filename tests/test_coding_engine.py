from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coding_engine import _compact_coding_messages, run_coding_agent
from tools.coding_workspace import WorkspaceSnapshotManager


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.bound_names = []
        self.inputs = []

    def bind_tools(self, tools):
        self.bound_names = [item.name for item in tools]
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        return next(self.responses)


def test_plan_binds_only_read_tools(tmp_path):
    model = FakeModel([AIMessage(content="계획입니다")])
    events = list(run_coding_agent([HumanMessage(content="분석")], tmp_path, "plan", WorkspaceSnapshotManager(tmp_path), model=model))
    assert set(model.bound_names) == {"list_project_tree", "get_repo_map", "read_file_lines", "search_in_project"}
    assert events[-1] == {"type": "done", "phase": "plan", "stopped": False}


def test_compacts_only_old_long_read_results_without_mutation():
    old = ToolMessage(content="x" * 600, name="read_file_lines", tool_call_id="old")
    write = ToolMessage(content="y" * 600, name="replace_in_file", tool_call_id="write")
    recent = ToolMessage(content="z" * 600, name="search_in_project", tool_call_id="recent")
    messages = [old, write, HumanMessage(content="hello"), recent]
    result = _compact_coding_messages(messages, recent_window=1)
    assert "이전 탐색 결과 압축됨: read_file_lines" in result[0].content
    assert result[0].tool_call_id == "old" and old.content == "x" * 600
    assert result[1] is write and result[3] is recent


def test_breaker_stops_after_three_consecutive_failures(tmp_path):
    responses = [AIMessage(content="", tool_calls=[{"name": "read_file_lines", "args": {"file_path": f"missing{index}.py"}, "id": f"call{index}"}]) for index in range(3)]
    model = FakeModel(responses)
    events = list(run_coding_agent([HumanMessage(content="찾아줘")], tmp_path, "plan", WorkspaceSnapshotManager(tmp_path), model=model))
    assert len(model.inputs) == 3
    assert events[-1]["stopped"] is True
    summary = events[-2]["message"].content
    assert "연속 3회 실패" in summary and "missing0.py" in summary and "missing2.py" in summary


def test_plan_rejects_write_tool_even_if_model_calls_it(tmp_path):
    model = FakeModel([
        AIMessage(content="", tool_calls=[{"name": "create_or_overwrite_file", "args": {"file_path": "bad.py", "content": "pass"}, "id": "call1"}]),
        AIMessage(content="중단합니다"),
    ])
    events = list(run_coding_agent([HumanMessage(content="계획")], tmp_path, "plan", WorkspaceSnapshotManager(tmp_path), model=model))
    assert not (tmp_path / "bad.py").exists()
    assert any("허용되지 않은 도구" in str(event.get("message", "")) for event in events)


def test_act_uses_write_tools_and_snapshot_manager(tmp_path):
    manager = WorkspaceSnapshotManager(tmp_path)
    model = FakeModel([
        AIMessage(content="", tool_calls=[{"name": "create_or_overwrite_file", "args": {"file_path": "new.py", "content": "pass\n"}, "id": "call1"}]),
        AIMessage(content="완료"),
    ])
    list(run_coding_agent([HumanMessage(content="작성")], tmp_path, "act", manager, model=model))
    assert {"replace_in_file", "create_or_overwrite_file"} <= set(model.bound_names)
    assert (tmp_path / "new.py").exists()
    assert manager.undo_last_turn() == ["new.py"]


def test_breaker_stops_remaining_calls_in_same_batch(tmp_path):
    calls = [{"name": "read_file_lines", "args": {"file_path": f"missing{index}.py"}, "id": f"call{index}"} for index in range(3)]
    calls.append({"name": "create_or_overwrite_file", "args": {"file_path": "not_created.py", "content": "pass"}, "id": "fourth"})
    model = FakeModel([AIMessage(content="", tool_calls=calls)])
    events = list(run_coding_agent([HumanMessage(content="작업")], tmp_path, "act", WorkspaceSnapshotManager(tmp_path), model=model))
    assert events[-1]["stopped"] is True
    assert not (tmp_path / "not_created.py").exists()
    assert any(isinstance(event.get("message"), ToolMessage) and "중단되었습니다" in event["message"].content for event in events)


def test_success_resets_consecutive_failures(tmp_path):
    (tmp_path / "ok.py").write_text("pass\n", encoding="utf-8")
    paths = ["missing1.py", "missing2.py", "ok.py", "missing3.py", "missing4.py"]
    responses = [AIMessage(content="", tool_calls=[{"name": "read_file_lines", "args": {"file_path": path}, "id": f"call{index}"}]) for index, path in enumerate(paths)]
    responses.append(AIMessage(content="완료"))
    model = FakeModel(responses)
    events = list(run_coding_agent([HumanMessage(content="찾아줘")], tmp_path, "plan", WorkspaceSnapshotManager(tmp_path), model=model))
    assert len(model.inputs) == 6 and events[-1]["stopped"] is False


def test_recursion_limit_closes_pending_tool_calls(tmp_path):
    (tmp_path / "ok.py").write_text("pass\n", encoding="utf-8")
    responses = [AIMessage(content="", tool_calls=[{"name": "read_file_lines", "args": {"file_path": "ok.py"}, "id": f"call{index}"}]) for index in range(50)]
    model = FakeModel(responses)
    events = list(run_coding_agent([HumanMessage(content="반복")], tmp_path, "plan", WorkspaceSnapshotManager(tmp_path), model=model))
    assert events[-1]["stopped"] is True
    messages = [event["message"] for event in events if event["type"] == "message"]
    calls = {call["id"] for message in messages if isinstance(message, AIMessage) for call in message.tool_calls}
    results = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    assert calls == results
    assert "단계 수 제한" in messages[-1].content