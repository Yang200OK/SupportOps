"""真实 PostgreSQL 校验分步隔离与停止；模型替身仅验证协议。"""

import json
from uuid import uuid4

import pytest
from test_documents_postgres import payload, post
from test_rag_postgres import answer_model as answer_model
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.models.provider import ModelFailure
from supportops.rag import guided

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def light_model(monkeypatch):
    calls = []

    class Model:
        rewrite_action = "retrieve"
        action = "answer"
        next_query = None
        failure_stage = None
        invalid = False
        invalid_quote = False

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, body):
            assert body["model"] == self.settings.light_model
            data = json.loads(body["messages"][1]["content"])
            stage = data["stage"]
            calls.append((stage, data))
            if stage == Model.failure_stage:
                raise ModelFailure("MODEL_TIMEOUT")
            if stage == "rewrite":
                value = {
                    "action": Model.rewrite_action,
                    "query": data["original_query"]
                    + (" " + data["clarification"] if data["clarification"] else "")
                    if Model.rewrite_action == "retrieve"
                    else None,
                    "questions": ["请提供具体错误现象。"]
                    if Model.rewrite_action == "clarify"
                    else [],
                    "reason": "测试规划。",
                }
            else:
                value = {
                    "action": Model.action,
                    "next_query": Model.next_query,
                    "questions": ["请补充当前环境。"] if Model.action == "clarify" else [],
                    "conflicts": [],
                    "reason": "测试证据决策。",
                }
                if Model.action == "conflict":
                    value["conflicts"] = [
                        {
                            "topic": "默认值冲突",
                            "reason": "同版本文档默认值不同。",
                            "citations": [
                                {
                                    "context_id": c["context_id"],
                                    "evidence_id": c["anchor_evidence_ids"][0],
                                    "quote": "错误原文" if Model.invalid_quote else c["text"],
                                }
                                for c in data["contexts"][:2]
                            ],
                        }
                    ]
            if Model.invalid:
                value["organization_id"] = "cannot_change_scope"
            return {
                "model": self.settings.light_model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            }, 1

    monkeypatch.setattr(guided, "Provider", Model)
    return Model, calls


def ask(context, auth, record, **changes):
    return context["client"].post(
        f"/api/retrieval/indexes/{record['index_id']}/guided-answer",
        headers=auth,
        json={"query": "RD_TIMEOUT", "product_version": "1.1", "mode": "bm25", **changes},
    )


def test_req1002_unknown_version_scope_and_empty_stop_before_models(
    context, light_model, answer_model, embedding
):
    auth = headers(context)
    record, _ = index(context, auth)
    before = len(embedding[1])
    body = ask(context, auth, record, product_version=None).json()
    assert body["status"] == "needs_clarification" and body["stop_reason"] == "version_required"
    assert body["usage"]["known_model_calls"] == 0 and body["questions"]
    assert (
        ask(context, headers(context, "username_b"), record, product_version=None).status_code
        == 404
    )
    assert ask(context, {}, record).status_code == 401
    body = ask(context, auth, record, document_ids=[str(uuid4())]).json()
    assert body["status"] == "no_evidence" and body["stop_reason"] == "empty_scope"
    assert not light_model[1] and not answer_model[1] and len(embedding[1]) == before


def test_req1001_original_and_supplement_reach_generation_and_reuse_review(
    context, light_model, answer_model
):
    auth = headers(context)
    record, _ = index(context, auth)
    response = ask(context, auth, record, clarification="timeout_ms=2000", expand_parent=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered" and body["original_query"] == "RD_TIMEOUT"
    assert body["answer"]["query"] == "RD_TIMEOUT" and not body["persisted"]
    assert answer_model[1][0][1]["clarification"] == "timeout_ms=2000"
    assert answer_model[1][0][1]["query"] == "RD_TIMEOUT"
    assert body["usage"]["known_model_calls"] == 4 and body["usage"]["input_tokens"] == 40
    assert len(body["retrieval"]["items"]) <= 5 and response.headers["cache-control"] == "no-store"
    assert body["answer"]["claims"][0]["citations"][0]["literal_verified"]


@pytest.mark.parametrize("stage", ["rewrite", "decision_1"])
def test_req1002_clarification_never_generates_answer(context, light_model, answer_model, stage):
    auth = headers(context)
    record, _ = index(context, auth)
    if stage == "rewrite":
        light_model[0].rewrite_action = "clarify"
    else:
        light_model[0].action = "clarify"
    body = ask(context, auth, record).json()
    assert body["status"] == "needs_clarification" and body["questions"]
    assert body["answer"] is None and not answer_model[1]


@pytest.mark.parametrize(
    "stop,changes,next_query",
    [
        ("repeated_query", {}, "RD_TIMEOUT"),
        ("search_budget", {"max_searches": 1}, "RD_TIMEOUT timeout_ms"),
        ("no_new_evidence", {}, "RD_TIMEOUT timeout_ms"),
    ],
)
def test_req1005_repeat_count_and_no_new_stops_are_explicit(
    context, light_model, stop, changes, next_query
):
    auth = headers(context)
    record, _ = index(context, auth)
    light_model[0].action, light_model[0].next_query = "search_more", next_query
    body = ask(context, auth, record, **changes).json()
    assert body["status"] == "limited_answer" and body["stop_reason"] == stop
    assert body["answer"] is not None
    assert len(body["retrieval"]["searches"]) == (2 if stop == "no_new_evidence" else 1)
    assert len({h["evidence_id"] for h in body["retrieval"]["items"]}) == len(
        body["retrieval"]["items"]
    )


def two_documents(context, auth):
    sets = []
    for title, text in [
        ("default", "默认 timeout_ms=3000。"),
        ("conflict", "默认 timeout_ms=5000。"),
    ]:
        r = post(
            context, auth, payload(("# Timeout\n\n" + text).encode(), source_key=title, title=title)
        ).json()
        response = context["client"].post(
            f"/api/documents/{r['document_id']}/revisions/{r['revision_id']}/chunk-sets",
            headers=auth,
            json={},
        )
        assert response.status_code == 200, response.text
        sets.append(response.json()["chunk_set_id"])
    response = context["client"].post(
        "/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": sets}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("bad", [False, True])
def test_req1004_same_version_conflict_requires_exact_quotes_and_never_generates(
    context, light_model, answer_model, bad
):
    auth = headers(context)
    record = two_documents(context, auth)
    light_model[0].action, light_model[0].invalid_quote = "conflict", bad
    response = ask(context, auth, record, query="timeout_ms")
    body = response.json()
    if bad:
        assert response.status_code == 502 and body["error"]["code"] == "GUIDED_EVIDENCE_INVALID"
        assert "answer" not in body
    else:
        assert (
            response.status_code == 200 and body["status"] == "conflict" and body["answer"] is None
        )
        assert len(body["conflicts"][0]["citations"]) == 2
        assert all(c["literal_verified"] for c in body["conflicts"][0]["citations"])
    assert not answer_model[1]


def test_req1003_additional_retrieval_adds_evidence_with_original_scope(context, light_model):
    auth = headers(context)
    record = two_documents(context, auth)
    light_model[0].action, light_model[0].next_query = "search_more", "conflict"
    body = ask(context, auth, record, query="default", top_k=1).json()
    assert body["status"] == "limited_answer" and len(body["retrieval"]["items"]) == 2
    assert all(
        h["product_version"] == "1.1" and h["kind"] == "document"
        for h in body["retrieval"]["items"]
    )
    assert [t["new_evidence"] for t in body["trace"] if t["stage"] == "retrieval"] == [1, 1]


def test_req1005_model_budget_reserves_generation_pair(context, light_model, answer_model):
    auth = headers(context)
    record, _ = index(context, auth)
    body = ask(context, auth, record, max_model_calls=3).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "model_budget"
    assert (
        body["usage"]["known_model_calls"] == 2 and body["answer"] is None and not answer_model[1]
    )


def test_req1005_too_short_budget_never_starts_fixed_timeout_retrieval(
    context, light_model, answer_model, embedding
):
    auth = headers(context)
    record, _ = index(context, auth)
    before = len(embedding[1])
    body = ask(context, auth, record, mode="vector", time_budget_ms=1000).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "time_budget"
    assert body["answer"] is None and not answer_model[1] and len(embedding[1]) == before


def test_req1001_changed_identifiers_are_rejected_before_retrieval(
    context, light_model, answer_model
):
    auth = headers(context)
    record, _ = index(context, auth)
    light_model[0].action, light_model[0].next_query = "search_more", "RD_TIMEOUT invented_key"
    response = ask(context, auth, record)
    assert (
        response.status_code == 502
        and response.json()["error"]["code"] == "GUIDED_EVIDENCE_INVALID"
    )
    assert response.json()["usage"]["known_model_calls"] == 2
    assert not answer_model[1]


@pytest.mark.parametrize(
    "invalid,stage,known,unknown",
    [(True, None, 1, 0), (False, "rewrite", 0, 1), (False, "decision_1", 1, 1)],
)
def test_req1007_paid_invalid_and_timeout_usage_do_not_hide_failure(
    context, light_model, answer_model, invalid, stage, known, unknown
):
    auth = headers(context)
    record, _ = index(context, auth)
    light_model[0].invalid, light_model[0].failure_stage = invalid, stage
    response = ask(context, auth, record)
    body = response.json()
    assert response.status_code in (502, 503) and "answer" not in body
    assert (
        body["usage"]["known_model_calls"] == known
        and body["usage"]["unknown_usage_calls"] == unknown
    )
    assert "cannot_change_scope" not in response.text and not answer_model[1]


def test_req1007_main_failure_preserves_light_usage_without_partial_answer(context, answer_model):
    auth = headers(context)
    record, _ = index(context, auth)
    answer_model[0].failure_stage = "verification"
    response = ask(context, auth, record)
    body = response.json()
    assert response.status_code == 503 and body["stage"] == "verification" and "answer" not in body
    assert body["usage"]["known_model_calls"] == 3 and body["usage"]["unknown_usage_calls"] == 1


def test_req1005_late_main_completion_keeps_usage_but_does_not_return_answer(context, monkeypatch):
    from supportops.rag.budget import Budget

    now = [0.0]
    monkeypatch.setattr(guided, "Budget", lambda calls, ms: Budget(calls, ms, clock=lambda: now[0]))
    original_answer = guided.answers.answer

    def late_answer(*args, **kwargs):
        result = original_answer(*args, **kwargs)
        now[0] = 5.0
        return result

    monkeypatch.setattr(guided.answers, "answer", late_answer)
    auth = headers(context)
    record, _ = index(context, auth)
    body = ask(context, auth, record, time_budget_ms=4000).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "time_budget"
    assert body["answer"] is None and body["usage"]["known_model_calls"] == 4


def test_req1007_database_failure_after_paid_rewrite_keeps_usage(context, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    auth = headers(context)
    record, _ = index(context, auth)

    def failed_search(*args, **kwargs):
        raise DBAPIError("private_statement", None, RuntimeError("private_database_error"))

    monkeypatch.setattr(guided.advanced, "search", failed_search)
    response = ask(context, auth, record)
    body = response.json()
    assert response.status_code == 503 and body["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert body["usage"]["known_model_calls"] == 1 and "answer" not in body
    assert "private" not in response.text
