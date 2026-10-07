"""引用存在与语义支持分开验证，测试数据不代表真实回答质量。"""

import copy

import pytest
from pydantic import ValidationError

from supportops.rag.contracts import AnswerDraft, AnswerRequest, SupportReview
from supportops.rag.validation import bind_citations, bind_reviews


def draft():
    return {
        "claims": [
            {
                "claim_id": "C1",
                "kind": "fact",
                "text": "超时应检查日志。",
                "citations": [{"context_id": "E1", "evidence_id": "E1", "quote": "检查日志"}],
            }
        ],
        "missing_information": ["请提供当前日志。"],
    }


def evidence():
    return [
        {
            "context_id": "E1",
            "kind": "child",
            "text": "超时需要检查日志。",
            "text_verified": True,
            "anchor_evidence_ids": ["E1"],
        }
    ]


def test_req903_quote_span_and_original_identity_are_bound_by_code():
    actual = bind_citations(AnswerDraft.model_validate(draft()), evidence())
    cite = actual[0]["citations"][0]
    assert cite["start"] == 4 and cite["end"] == 8
    assert cite["literal_verified"] and cite["context_id"] == "E1"


@pytest.mark.parametrize(
    "field,value",
    [("context_id", "invented"), ("evidence_id", "invented"), ("quote", "已确认数据库断开")],
)
def test_req903_invented_identity_or_quote_is_rejected(field, value):
    body = draft()
    body["claims"][0]["citations"][0][field] = value
    with pytest.raises(ValueError):
        bind_citations(AnswerDraft.model_validate(body), evidence())


def test_req903_unverified_context_cannot_be_cited():
    rows = evidence()
    rows[0]["text_verified"] = False
    with pytest.raises(ValueError):
        bind_citations(AnswerDraft.model_validate(draft()), rows)


@pytest.mark.parametrize(
    "change", ["no_citations", "duplicate", "root_cause", "extra", "empty_quote"]
)
def test_req902_draft_has_no_uncited_fact_or_arbitrary_root_cause(change):
    body = draft()
    if change == "no_citations":
        body["claims"][0]["citations"] = []
    elif change == "duplicate":
        body["claims"].append(copy.deepcopy(body["claims"][0]))
    elif change == "empty_quote":
        body["claims"][0]["citations"][0]["quote"] = " "
    else:
        body[change] = "攻击指令"
    with pytest.raises(ValidationError):
        AnswerDraft.model_validate(body)


def test_req904_semantic_unsupported_is_separate_from_literal_existence():
    claims = bind_citations(AnswerDraft.model_validate(draft()), evidence())
    review = SupportReview.model_validate(
        {"items": [{"claim_id": "C1", "verdict": "unsupported", "reason": "引文没有支持该结论。"}]}
    )
    actual = bind_reviews(claims, review)
    assert actual[0]["citations"][0]["literal_verified"]
    assert actual[0]["support"]["verdict"] == "unsupported"
    assert not actual[0]["support"]["human_reviewed"]


@pytest.mark.parametrize("ids", [[], ["C2"], ["C1", "C1"]])
def test_req904_review_must_cover_exact_claim_set(ids):
    with pytest.raises((ValueError, ValidationError)):
        review = SupportReview.model_validate(
            {
                "items": [
                    {"claim_id": key, "verdict": "supported", "reason": "符合原文"} for key in ids
                ]
            }
        )
        bind_reviews(bind_citations(AnswerDraft.model_validate(draft()), evidence()), review)


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "foreign"},
        {"evidence": []},
        {"product_version": None},
        {"top_k": 9},
        {"context_budget_chars": 12001},
    ],
)
def test_req901_server_owns_scope_evidence_and_answer_budget(changes):
    with pytest.raises(ValidationError):
        AnswerRequest.model_validate({"query": "故障排查", "product_version": "1.1", **changes})
