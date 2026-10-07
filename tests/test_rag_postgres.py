"""真实数据库核对范围与来源；回答替身只存在于测试侧。"""

import json
from uuid import uuid4

import pytest
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.models.provider import ModelFailure
from supportops.rag import service

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def answer_model(monkeypatch):
    calls = []

    class Model:
        failure_stage = None
        invalid_quote = False
        verdict = "supported"

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, body):
            data = json.loads(body["messages"][1]["content"])
            stage = "generation" if "contexts" in data else "verification"
            calls.append((stage, data))
            if stage == Model.failure_stage:
                raise ModelFailure("MODEL_TIMEOUT")
            if stage == "generation":
                row = data["contexts"][0]
                value = {
                    "claims": [
                        {
                            "claim_id": "C1",
                            "kind": "check",
                            "text": "请检查 Connection 信息。",
                            "citations": [
                                {
                                    "context_id": row["context_id"],
                                    "evidence_id": row["anchor_evidence_ids"][0],
                                    "quote": "不存在的引文"
                                    if Model.invalid_quote
                                    else row["text"][:30],
                                }
                            ],
                        }
                    ],
                    "missing_information": ["当前实际日志。"],
                }
            else:
                value = {
                    "items": [
                        {"claim_id": "C1", "verdict": Model.verdict, "reason": "测试核对原因。"}
                    ]
                }
            return {
                "model": self.settings.main_model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            }, 1

    monkeypatch.setattr(service, "Provider", Model)
    return Model, calls


def ask(context, auth, record, **changes):
    return context["client"].post(
        f"/api/retrieval/indexes/{record['index_id']}/answer",
        headers=auth,
        json={"query": "Connection", "product_version": "1.1", **changes},
    )


def test_req901_actual_database_citations_scope_and_usage(context, answer_model):
    auth = headers(context)
    record, _ = index(context, auth)
    response = ask(context, auth, record, expand_parent=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "reviewed" and not body["persisted"]
    assert body["usage"]["input_tokens"] == 25 and body["usage"]["output_tokens"] == 14
    assert body["usage"]["known_model_calls"] == 3
    assert len(answer_model[1]) == 2
    assert answer_model[1][0][1]["contexts"][0]["source_kind"] == "document"
    assert answer_model[1][1][1]["evidence_contexts"]
    citation = body["claims"][0]["citations"][0]
    window = next(c for c in body["contexts"] if c["context_id"] == citation["context_id"])
    assert window["text"][citation["start"] : citation["end"]] == citation["quote"]
    assert body["claims"][0]["support"]["verdict"] == "supported"
    assert not body["current_incident_verified"] and not body["human_reviewed"]
    assert response.headers["cache-control"] == "no-store"


def test_req901_foreign_org_and_no_session_fail_before_model(context, answer_model, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    before = len(embedding[1])
    assert ask(context, headers(context, "username_b"), record).status_code == 404
    assert ask(context, {}, record).status_code == 401
    assert not answer_model[1] and len(embedding[1]) == before
    assert ask(context, headers(context, "colleague"), record).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [{"product_version": "2.0"}, {"source_kinds": ["log"]}, {"document_ids": [str(uuid4())]}],
)
def test_req905_empty_scope_does_not_call_any_model(context, answer_model, embedding, changes):
    auth = headers(context)
    record, _ = index(context, auth)
    before = len(embedding[1])
    response = ask(context, auth, record, **changes)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_evidence" and body["claims"] == []
    assert body["usage"]["known_model_calls"] == 0
    assert not answer_model[1] and len(embedding[1]) == before


@pytest.mark.parametrize("stage,known", [("generation", 1), ("verification", 2)])
def test_req905_stage_failure_does_not_return_partial_answer(context, answer_model, stage, known):
    auth = headers(context)
    record, _ = index(context, auth)
    answer_model[0].failure_stage = stage
    response = ask(context, auth, record)
    assert response.status_code == 503
    body = response.json()
    assert body["stage"] == stage and "claims" not in body
    assert body["usage"]["known_model_calls"] == known
    assert body["usage"]["unknown_usage_calls"] == 1
    assert len(answer_model[1]) == known


def test_req903_fake_quote_fails_after_generation_before_review(context, answer_model):
    auth = headers(context)
    record, _ = index(context, auth)
    answer_model[0].invalid_quote = True
    response = ask(context, auth, record)
    body = response.json()
    assert response.status_code == 502 and body["stage"] == "citations"
    assert body["error"]["code"] == "ANSWER_EVIDENCE_INVALID"
    assert body["usage"]["input_tokens"] == 15 and body["usage"]["unknown_usage_calls"] == 0
    assert len(answer_model[1]) == 1


def test_req904_unsupported_retains_explicit_review_without_claiming_current_cause(
    context, answer_model
):
    auth = headers(context)
    record, _ = index(context, auth)
    answer_model[0].verdict = "unsupported"
    body = ask(context, auth, record).json()
    assert body["claims"][0]["support"]["verdict"] == "unsupported"
    assert not body["current_incident_verified"]
    assert body["claims"][0]["citations"][0]["literal_verified"]
