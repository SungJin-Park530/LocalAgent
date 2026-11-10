"""CPU sentence-embedding based intent classification."""

from threading import Lock

from config.intent_routes import INTENT_ANCHORS
from config.settings import (
    ROUTER_DEVICE,
    ROUTER_EMBEDDING_MODEL,
    ROUTER_SIMILARITY_THRESHOLD,
)

_resource_lock = Lock()
_router_resources = None


def _encode(model, texts: list[str]):
    options = {
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    try:
        return model.encode(texts, task="text-matching", **options)
    except TypeError as error:
        if "task" not in str(error):
            raise
        return model.encode(texts, **options)


def _load_model_and_anchors():
    global _router_resources
    if _router_resources is None:
        with _resource_lock:
            if _router_resources is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    ROUTER_EMBEDDING_MODEL,
                    trust_remote_code=True,
                    device=ROUTER_DEVICE,
                )
                categories = list(INTENT_ANCHORS)
                sentences = [
                    sentence
                    for items in INTENT_ANCHORS.values()
                    for sentence in items
                ]
                vectors = _encode(model, sentences)

                anchor_vectors = {}
                offset = 0
                for category, utterances in zip(categories, INTENT_ANCHORS.values()):
                    anchor_vectors[category] = vectors[offset : offset + len(utterances)]
                    offset += len(utterances)
                _router_resources = (model, anchor_vectors)
    return _router_resources


def classify_intent(query: str) -> tuple[str, float]:
    """Return the best intent and its cosine similarity to an anchor."""
    if not query.strip():
        print("[SemanticRouter] query='' -> route='chat' (score=0.00)", flush=True)
        return "chat", 0.0

    model, anchor_vectors = _load_model_and_anchors()
    query_vector = _encode(model, [query])[0]
    best_route = "chat"
    best_score = float("-inf")

    for category, vectors in anchor_vectors.items():
        category_score = max(
            sum(float(left) * float(right) for left, right in zip(query_vector, anchor))
            for anchor in vectors
        )
        if category_score > best_score:
            best_route = category
            best_score = category_score

    if best_score < ROUTER_SIMILARITY_THRESHOLD:
        best_route = "chat"

    score = float(best_score)
    print(
        f"[SemanticRouter] query={query!r} -> route={best_route!r} "
        f"(score={score:.2f})",
        flush=True,
    )
    return best_route, score