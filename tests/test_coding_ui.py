from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from views import coding
from tools.coding_workspace import create_coding_write_tools


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def make_streamlit(root):
    streamlit = MagicMock()
    streamlit.session_state = SessionState(coding_workspace_root=str(root))
    streamlit.text_input.return_value = str(root)
    streamlit.radio.return_value = coding.PLAN_WORKFLOW
    streamlit.button.return_value = False
    streamlit.chat_input.return_value = None
    return streamlit


def test_plan_approval_runs_act_with_plan_context(tmp_path, monkeypatch):
    streamlit = make_streamlit(tmp_path)
    streamlit.chat_input.return_value = "코드를 변경해줘"
    monkeypatch.setattr(coding, "st", streamlit)
    calls = []

    def run(messages, root, phase, manager, profile):
        calls.append((phase, list(messages), root))
        yield {"type": "message", "message": AIMessage(content="승인할 계획" if phase == "plan" else "수정 완료")}
        yield {"type": "done", "phase": phase, "stopped": False}

    monkeypatch.setattr(coding, "run_coding_agent", run)
    coding.render_coding_page()
    state = streamlit.session_state.coding_workspace_states[str(tmp_path)]
    assert state["pending_plan"] is True
    assert calls[0][0] == "plan"
    streamlit.chat_input.return_value = None
    streamlit.button.side_effect = lambda label, **kwargs: label.startswith("✅")
    coding.render_coding_page()
    assert calls[1][0] == "act"
    assert any(isinstance(message, AIMessage) and message.content == "승인할 계획" for message in calls[1][1])
    assert isinstance(calls[1][1][-1], HumanMessage) and "승인" in calls[1][1][-1].content
    assert state["pending_plan"] is False


def test_auto_act_and_workspace_conversations_are_isolated(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    streamlit = make_streamlit(first)
    streamlit.radio.return_value = coding.AUTO_WORKFLOW
    streamlit.chat_input.return_value = "즉시 수정"
    monkeypatch.setattr(coding, "st", streamlit)
    phases = []

    def run(messages, root, phase, manager, profile):
        phases.append(phase)
        yield {"type": "message", "message": AIMessage(content="완료")}
        yield {"type": "done", "phase": phase, "stopped": False}

    monkeypatch.setattr(coding, "run_coding_agent", run)
    coding.render_coding_page()
    streamlit.session_state.coding_workspace_root = str(second)
    streamlit.chat_input.return_value = None
    coding.render_coding_page()
    assert phases == ["act"]
    assert len(streamlit.session_state.coding_workspace_states[str(first)]["messages"]) == 2
    assert streamlit.session_state.coding_messages == []


def test_interrupted_tool_calls_are_closed(tmp_path, monkeypatch):
    streamlit = make_streamlit(tmp_path)
    streamlit.chat_input.return_value = "분석"
    monkeypatch.setattr(coding, "st", streamlit)

    def run(*args):
        yield {"type": "message", "message": AIMessage(content="", tool_calls=[{"name": "read_file_lines", "args": {"file_path": "x.py"}, "id": "call1"}])}
        raise RuntimeError("모델 연결 오류")

    monkeypatch.setattr(coding, "run_coding_agent", run)
    coding.render_coding_page()
    messages = streamlit.session_state.coding_messages
    assert isinstance(messages[-2], ToolMessage) and messages[-2].tool_call_id == "call1"
    assert "모델 연결 오류" in messages[-1].content
    assert not streamlit.session_state.coding_workspace_states[str(tmp_path)]["pending_plan"]


def test_undo_button_restores_workspace_file(tmp_path, monkeypatch):
    streamlit = make_streamlit(tmp_path)
    monkeypatch.setattr(coding, "st", streamlit)
    coding.render_coding_page()
    manager = streamlit.session_state.coding_workspace_states[str(tmp_path)]["snapshot_manager"]
    tools = {item.name: item for item in create_coding_write_tools(tmp_path, manager)}
    tools["create_or_overwrite_file"].invoke({"file_path": "new.py", "content": "pass\n"})
    streamlit.button.side_effect = lambda label, **kwargs: label.startswith("↩️")
    coding.render_coding_page()
    assert not (tmp_path / "new.py").exists()
    assert manager.get_unified_diff() == ""
    assert any("복원된 파일" in str(call) for call in streamlit.success.call_args_list)