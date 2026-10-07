"""固定排序索引和父块采用真实 PostgreSQL，模型替身仅验证契约。"""

import pytest
from test_chunks_postgres import setup
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index, search
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


@pytest.fixture
def reranker(monkeypatch):
    from supportops.retrieval import advanced

    calls = []

    class Ranking:
        fail = False

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def rerank_texts(self, query, texts):
            from supportops.models.provider import ModelFailure

            calls.append((query, texts))
            if Ranking.fail:
                raise ModelFailure("MODEL_TIMEOUT")
            return {
                "indices": list(reversed(range(len(texts)))),
                "scores": [0.5] * len(texts),
                "requested_model": self.settings.rerank_model,
                "returned_model": None,
                "input_tokens": 17,
                "latency_ms": 1,
                "cost_cny": None,
            }

    monkeypatch.setattr(advanced, "Provider", Ranking)
    return Ranking, calls


def test_req802_rerank_mapping_ties_and_usage(context, reranker):
    auth = headers(context)
    record, _ = index(context, auth)
    actual = search(context, auth, record, rerank=True, rerank_pool=20).json()
    assert actual["rerank_usage"]["input_tokens"] == 17
    assert [h["evidence_id"] for h in actual["items"]] == sorted(
        h["evidence_id"] for h in actual["items"]
    )
    assert all(h["rerank_score"] == 0.5 and h["retrieval_rank"] for h in actual["items"])
    assert len(reranker[1]) == 1 and actual["usage"]["input_tokens"] == 22


def test_req803_failure_no_downgrade_and_empty_no_calls(context, reranker, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    reranker[0].fail = True
    response = search(context, auth, record, mode="bm25", query="RD_TIMEOUT", rerank=True)
    assert response.status_code == 503 and response.json()["error"]["code"] == "MODEL_TIMEOUT"
    assert response.json()["stage"] == "rerank"
    assert response.json()["usage"]["unknown_usage_calls"] == 1
    before = (len(reranker[1]), len(embedding[1]))
    response = search(context, auth, record, product_version="2.0", rerank=True)
    assert response.status_code == 200 and response.json()["items"] == []
    assert before == (len(reranker[1]), len(embedding[1]))


def test_req804_parent_expansion_preserves_child_and_deduplicates(context):
    auth = headers(context)
    revision, path = setup(
        context, auth, ("# Connection\n\n" + "RD_TIMEOUT 核对下游日志与实际配置。" * 20).encode()
    )
    snapshot = (
        context["client"]
        .post(path, headers=auth, json={"max_chars": 128, "overlap_chars": 20})
        .json()
    )
    record = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
        )
        .json()
    )
    original = search(context, auth, record, top_k=5).json()
    expanded = search(context, auth, record, top_k=5, expand_parent=True).json()
    assert [h["evidence_id"] for h in expanded["items"]] == [
        h["evidence_id"] for h in original["items"]
    ]
    assert [h["text"] for h in expanded["items"]] == [h["text"] for h in original["items"]]
    assert len(expanded["contexts"]) == 1
    context_item = expanded["contexts"][0]
    assert all(h["evidence_id"] in context_item["anchor_evidence_ids"] for h in expanded["items"])
    assert "".join(context_item["text"].splitlines()).startswith("# Connection")
    assert not context_item["support_verified"] and context_item["text_verified"]


@pytest.mark.parametrize(
    "changes",
    [
        {"rerank": "true"},
        {"expand_parent": 1},
        {"rerank_pool": True},
        {"rerank_pool": 41},
        {"top_k": 5, "rerank_pool": 1},
        {"context_budget_chars": True},
    ],
)
def test_req802_invalid_options_are_rejected(context, changes):
    auth = headers(context)
    record, _ = index(context, auth)
    assert search(context, auth, record, **changes).status_code == 422


def test_req805_budget_failure_preserves_paid_usage_and_no_partial_context(context):
    auth = headers(context)
    _, path = setup(context, auth, ("# Connection\n\n" + "RD_TIMEOUT config " * 100).encode())
    snapshot = (
        context["client"]
        .post(path, headers=auth, json={"max_chars": 128, "overlap_chars": 20})
        .json()
    )
    record = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
        )
        .json()
    )
    response = search(
        context,
        auth,
        record,
        top_k=5,
        expand_parent=True,
        parent_max_chars=128,
        context_budget_chars=128,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "CONTEXT_BUDGET_EXCEEDED" and body["stage"] == "context"
    assert body["usage"]["input_tokens"] == 5 and "items" not in body
    valid = search(
        context,
        auth,
        record,
        top_k=5,
        expand_parent=True,
        parent_max_chars=256,
        context_budget_chars=2000,
    ).json()
    assert valid["context_chars"] <= 2000 and all(len(c["text"]) <= 256 for c in valid["contexts"])
    for hit in valid["items"]:
        window = next(
            c for c in valid["contexts"] if hit["evidence_id"] in c["anchor_evidence_ids"]
        )
        for span in hit["source"]["spans"]:
            assert window["start"] <= span["start"] < span["end"] <= window["end"]


def test_req803_other_org_cannot_rerank_or_expand(context, reranker, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    before = (len(reranker[1]), len(embedding[1]))
    response = search(
        context, headers(context, "username_b"), record, rerank=True, expand_parent=True
    )
    assert response.status_code == 404 and before == (len(reranker[1]), len(embedding[1]))


def test_req802_forty_item_pool_does_not_change_public_top_k_limit(context, reranker):
    auth = headers(context)
    _, path = setup(context, auth, ("# Connection\n\n" + "RD_TIMEOUT config " * 500).encode())
    snapshot = (
        context["client"]
        .post(path, headers=auth, json={"max_chars": 128, "overlap_chars": 20})
        .json()
    )
    record = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
        )
        .json()
    )
    result = search(context, auth, record, top_k=5, rerank=True, rerank_pool=40).json()
    assert (
        len(reranker[1][0][1]) == 40
        and result["rerank_candidates"] == 40
        and len(result["items"]) == 5
    )
