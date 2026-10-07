"""并行调查调度的实际行为契约，测试替身仅用于测试。"""

from uuid import uuid4

import pytest

from supportops.coordination.contracts import BoardRequest
from supportops.coordination.core import build_plan
from supportops.coordination.execution_core import (
    collect_evidence,
    initial_state,
    recover,
    reserve,
    validate_steps,
)


def plan():
    return build_plan(
        {"title": "启动失败", "description": "配置错误", "product_version": "1.0"},
        "startup",
        BoardRequest(request_id=uuid4(), index_id=uuid4(), lab_run_id=uuid4()),
    )


def test_req1903_global_and_private_atomic_reservation():
    p = plan()
    s = initial_state(p)
    for i in range(3):
        reserve(s, p, "documents", "model", f"m{i}", 1)
    with pytest.raises(ValueError, match="TASK_BUDGET"):
        reserve(s, p, "documents", "model", "extra", 1)
    assert s["usage"]["model_calls"] == 3
    assert len(s["operations"]) == 3


def test_req1902_role_tools_and_duplicate_proposals():
    p = plan()
    validate_steps(
        p["tasks"][0]["package"], [{"tool": "search_knowledge", "arguments": {"query": "配置"}}]
    )
    with pytest.raises(ValueError, match="ROLE"):
        validate_steps(
            p["tasks"][0]["package"], [{"tool": "read_startup_diagnostic", "arguments": {}}]
        )
    with pytest.raises(ValueError, match="DUPLICATE"):
        validate_steps(
            p["tasks"][1]["package"], [{"tool": "read_startup_diagnostic", "arguments": {}}] * 2
        )


def test_req1905_unknown_call_recovery_never_replays():
    p = plan()
    s = initial_state(p)
    reserve(s, p, "documents", "model", "plan", 1)
    recover(s)
    assert s["status"] == "uncertain"
    assert s["operations"][0]["status"] == "unknown"
    assert s["tasks"]["documents"]["status"] == "failed"


def test_req1905_saved_receipt_recovery_preserves_phase():
    s = initial_state(plan())
    s["tasks"]["documents"].update(status="completed", phase="done", report={"claims": []})
    recover(s)
    assert s["tasks"]["documents"]["status"] == "completed"
    assert s["status"] == "pending"


def test_req1904_same_identity_conflicting_source_is_rejected():
    a = {"evidence_id": "e1", "text": "原文甲", "product_version": "1.0"}
    assert collect_evidence([[a], [a]]) == [a]
    with pytest.raises(ValueError, match="EVIDENCE_IDENTITY"):
        collect_evidence([[a], [{**a, "text": "原文乙"}]])


def test_req1904_retrieval_rank_is_not_source_identity():
    a = {
        "evidence_id": "e1",
        "text": "原文甲",
        "product_version": "1.0",
        "rank": 1,
        "bm25_score": 2.5,
    }
    b = {**a, "rank": 2, "bm25_score": 1.5}
    assert collect_evidence([[a], [b]]) == [a]


def test_req1903_parent_time_and_total_cap_not_reset():
    p = plan()
    s = initial_state(p)
    s["deadline"] = 10
    with pytest.raises(ValueError, match="TIME_BUDGET"):
        reserve(s, p, "runtime", "model", "plan", 10)
    assert not s["operations"]
    p["budget"]["model_calls"] = 1
    reserve(s, p, "documents", "model", "plan", 9)
    with pytest.raises(ValueError, match="TOTAL_BUDGET"):
        reserve(s, p, "runtime", "model", "plan", 9)
    assert s["usage"]["model_calls"] == 1
