"""字面约束、预算和证据冲突不是模型语义准确率。"""

import pytest
from pydantic import ValidationError

from supportops.rag.budget import Budget, BudgetStop
from supportops.rag.contracts import AnswerDraft
from supportops.rag.guided_contracts import Decision, GuidedRequest, Rewrite
from supportops.rag.guided_validation import guard_query, merge_hits, validate_conflicts
from supportops.rag.validation import bind_citations


def test_req1001_rewrite_keeps_error_codes_keys_and_numeric_values():
    original = "RelayDesk 1.1 RD_TIMEOUT，delivery_timeout_ms=2000；不要猜根因。"
    guard_query("1.1 RD_TIMEOUT delivery_timeout_ms 2000 核对配置", original, "1.1")
    with pytest.raises(ValueError):
        guard_query("1.1 RD_TIMEOUT delivery_timeout_ms 3000", original, "1.1")
    with pytest.raises(ValueError):
        guard_query("1.1 RD_TIMEOUT timeout 2000", original, "1.1")


def test_req1001_new_query_identifiers_require_observed_evidence():
    with pytest.raises(ValueError):
        guard_query("RD_TIMEOUT fake_key", "RD_TIMEOUT", "1.1")
    guard_query("RD_TIMEOUT db_pool_wait_ms", "RD_TIMEOUT", "1.1", "db_pool_wait_ms=1500")


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "other"},
        {"model": "other"},
        {"evidence": []},
        {"max_searches": 3},
        {"max_model_calls": 10},
        {"time_budget_ms": 240001},
        {"top_k": 6},
        {"clarification": "x" * 1001},
    ],
)
def test_req1001_scope_and_budget_do_not_belong_to_model_or_client_evidence(changes):
    with pytest.raises(ValidationError):
        GuidedRequest.model_validate({"query": "RD_TIMEOUT", "product_version": "1.1", **changes})


def test_req1002_unknown_version_is_a_valid_clarification_request():
    assert (
        GuidedRequest.model_validate(
            {"query": "怎么排查？", "product_version": None}
        ).product_version
        is None
    )
    assert Rewrite.model_validate(
        {"action": "clarify", "query": None, "questions": ["什么现象？"], "reason": "信息不足"}
    )


@pytest.mark.parametrize(
    "body",
    [
        {
            "action": "search_more",
            "next_query": None,
            "questions": [],
            "conflicts": [],
            "reason": "缺少证据",
        },
        {
            "action": "conflict",
            "next_query": None,
            "questions": [],
            "conflicts": [],
            "reason": "有冲突",
        },
        {
            "action": "answer",
            "next_query": "私自改范围",
            "questions": [],
            "conflicts": [],
            "reason": "回答",
        },
    ],
)
def test_req1004_model_decision_has_consistent_action_fields(body):
    with pytest.raises(ValidationError):
        Decision.model_validate(body)


def hit(key, text="原文"):
    return {
        "evidence_id": key,
        "text": text,
        "kind": "document",
        "product_version": "1.1",
        "source": {"document_id": key},
        "rank": 1,
    }


def test_req1003_merge_keeps_identity_and_search_ranks_without_duplicate_evidence():
    items, added = merge_hits([hit("A")], [hit("A"), hit("B")], 2)
    assert [h["evidence_id"] for h in items] == ["A", "B"] and added == 1
    assert items[0]["search_ranks"] == [{"search": 1, "rank": 1}, {"search": 2, "rank": 1}]
    with pytest.raises(ValueError):
        merge_hits([hit("A")], [hit("A", "被篡改")], 2)


def test_req1005_budget_stops_without_issuing_more_calls_and_tracks_actual_usage():
    now = [0.0]
    budget = Budget(3, 1000, clock=lambda: now[0])
    budget.record("rewrite", {"input_tokens": 10, "output_tokens": 2}, calls=1)
    budget.before(2)
    with pytest.raises(BudgetStop, match="model_budget"):
        budget.before(3)
    now[0] = 1.1
    with pytest.raises(BudgetStop, match="time_budget"):
        budget.before(1)
    assert budget.usage()["known_model_calls"] == 1 and budget.usage()["input_tokens"] == 10


def test_req1004_conflict_quotes_are_bound_and_cross_phase_logs_are_rejected():
    windows = [
        {
            "context_id": "A",
            "text": "默认 3000 ms",
            "anchor_evidence_ids": ["A"],
            "text_verified": True,
        },
        {
            "context_id": "B",
            "text": "默认 5000 ms",
            "anchor_evidence_ids": ["B"],
            "text_verified": True,
        },
    ]
    conflict = {
        "topic": "默认时长",
        "reason": "同版本资料数值不同。",
        "citations": [
            {"context_id": key, "evidence_id": key, "quote": w["text"]}
            for key, w in zip(["A", "B"], windows)
        ],
    }
    actual = validate_conflicts([conflict], windows, [hit("A"), hit("B")])
    assert actual[0]["citations"][0]["literal_verified"]
    logs = [
        {
            **hit(key),
            "kind": "log",
            "source": {
                "run_id": "R",
                "event": {
                    "phase": phase,
                    "event": "observed",
                    "request_id": "Q",
                    "service": "relaydesk",
                },
            },
        }
        for key, phase in [("A", "baseline"), ("B", "failure")]
    ]
    with pytest.raises(ValueError):
        validate_conflicts([conflict], windows, logs)


def test_req1006_windows_line_endings_are_not_silently_repaired():
    window = {
        "context_id": "A",
        "text": "alpha_key=1\r\nbeta_key=2",
        "anchor_evidence_ids": ["A"],
        "text_verified": True,
    }

    def draft(quote):
        return AnswerDraft.model_validate(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "kind": "fact",
                        "text": "配置片段。",
                        "citations": [{"context_id": "A", "evidence_id": "A", "quote": quote}],
                    }
                ],
                "missing_information": ["实际配置。"],
            }
        )

    assert bind_citations(draft(window["text"]), [window])[0]["citations"][0]["literal_verified"]
    with pytest.raises(ValueError, match="引文不存在"):
        bind_citations(draft("alpha_key=1\nbeta_key=2"), [window])
