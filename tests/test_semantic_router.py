import numpy as np
import sys
from types import SimpleNamespace

import engine
import router_semantic


class FakeModel:
    def encode(self, texts, **kwargs):
        vectors = {
            "query-files": [1.0, 0.0],
            "files-anchor": [0.8, 0.6],
            "chat-anchor": [0.0, 1.0],
        }
        return np.array([vectors[text] for text in texts], dtype=float)


def test_classify_intent_uses_best_anchor_and_threshold(monkeypatch):
    monkeypatch.setattr(
        router_semantic,
        "_load_model_and_anchors",
        lambda: (
            FakeModel(),
            {
                "files": np.array([[0.8, 0.6]]),
                "chat": np.array([[0.0, 1.0]]),
            },
        ),
    )

    assert router_semantic.classify_intent("query-files") == ("files", 0.8)


def test_classify_intent_falls_back_to_chat_below_threshold(monkeypatch):
    class LowScoreModel:
        def encode(self, texts, **kwargs):
            return np.array([[1.0, 0.0] for _ in texts], dtype=float)

    monkeypatch.setattr(
        router_semantic,
        "_load_model_and_anchors",
        lambda: (LowScoreModel(), {"files": np.array([[0.2, 0.98]])}),
    )

    route, score = router_semantic.classify_intent("unrelated")
    assert route == "chat"
    assert score == 0.2


def test_jina_task_argument_falls_back_for_standard_encoders():
    class StandardEncoder:
        def encode(
            self,
            texts,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        ):
            return [[1.0, 0.0] for _ in texts]

    assert router_semantic._encode(StandardEncoder(), ["hello"]) == [[1.0, 0.0]]


def test_model_and_anchor_embeddings_are_cached_on_cpu(monkeypatch):
    calls = {"models": 0, "encoded_texts": []}

    class Model:
        def encode(self, texts, **kwargs):
            calls["encoded_texts"].extend(texts)
            return np.array([[1.0, 0.0] for _ in texts])

    def create_model(model_name, trust_remote_code, device):
        calls["models"] += 1
        assert model_name == "jinaai/jina-embeddings-v3"
        assert trust_remote_code is True
        assert device == "cpu"
        return Model()

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=create_model),
    )
    monkeypatch.setattr(router_semantic, "_router_resources", None)

    first = router_semantic._load_model_and_anchors()
    second = router_semantic._load_model_and_anchors()

    assert first is second
    assert calls["models"] == 1
    assert calls["encoded_texts"] == [
        sentence
        for utterances in router_semantic.INTENT_ANCHORS.values()
        for sentence in utterances
    ]


def test_router_records_missing_selected_group(monkeypatch):
    from langchain_core.messages import HumanMessage

    monkeypatch.setattr(
        engine, "classify_intent", lambda query: ("files", 0.81)
    )
    result = engine.router_node({
        "messages": [HumanMessage(content="폴더 파일을 찾아줘")],
        "selected_tools": ["browser"],
    })

    assert result == {
        "route": "files",
        "route_score": 0.81,
        "missing_tool": "files",
    }


def test_selected_route_binds_group_tools_and_all_chat_utilities():
    active_tools = list(engine.TOOL_REGISTRY.values())
    selected_names = {
        schema["function"]["name"]
        for schema in engine.TOOL_GROUP_SCHEMAS["browser"]
    }

    target_names = {
        tool.name
        for tool in engine._select_target_tools("browser", ["browser"], active_tools)
    }

    assert selected_names <= target_names
    assert {tool.name for tool in engine.CHAT_TOOLS} <= target_names
    assert not any(
        schema["function"]["name"] in target_names
        for schema in engine.TOOL_GROUP_SCHEMAS["files"]
    )


def test_unavailable_group_route_binds_only_chat_utilities():
    active_tools = list(engine.TOOL_REGISTRY.values())

    target_names = {
        tool.name
        for tool in engine._select_target_tools("browser", ["files"], active_tools)
    }

    assert target_names == {tool.name for tool in engine.CHAT_TOOLS}


def test_time_route_binds_only_chat_utilities():
    active_tools = list(engine.TOOL_REGISTRY.values())

    target_names = {
        tool.name
        for tool in engine._select_target_tools("time", ["files"], active_tools)
    }

    assert target_names == {tool.name for tool in engine.CHAT_TOOLS}