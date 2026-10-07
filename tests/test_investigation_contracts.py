"""只读调查的权限、预算与证据门槛先定义反例。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from supportops.investigations.contracts import BaselineDraft, Decision, InvestigationRequest
from supportops.investigations.guard import Gate


def test_req1201_request_cannot_set_organization_or_version():
    for field in ("organization_id", "product_version", "root_cause", "command"):
        with pytest.raises(ValidationError):
            InvestigationRequest.model_validate({"index_id": str(uuid4()), field: "2.0"})


@pytest.mark.parametrize("value", [0, 4, True, "3"])
def test_req1204_tool_budget_is_strict(value):
    with pytest.raises(ValidationError):
        InvestigationRequest(index_id=uuid4(), max_tool_calls=value)


def test_req1202_tool_parameters_cannot_expand_scope():
    for args in ({"query": "RD_TIMEOUT", "organization_id": "evil"}, {"query": ""}):
        with pytest.raises(ValidationError):
            Decision(action="search_knowledge", arguments=args, reason="测试")
    with pytest.raises(ValidationError):
        Decision(action="shell", arguments={"command": "whoami"}, reason="测试")
    with pytest.raises(ValidationError):
        Decision(
            action="read_observations", arguments={"experiment_id": str(uuid4())}, reason="测试"
        )


def test_req1204_repeat_and_budget_stop_before_dispatch():
    gate = Gate(max_tools=3, max_models=5, time_ms=180000)
    gate.tool("get_ticket", {})
    gate.tool("search_knowledge", {"query": "RD_TIMEOUT"})
    with pytest.raises(ValueError, match="repeated_tool"):
        gate.tool("search_knowledge", {"query": "RD_TIMEOUT timeout"})
    gate.tool("read_observations", {})
    with pytest.raises(ValueError, match="tool_budget"):
        gate.tool("other", {})


def test_req1204_clock_and_context_are_bounded():
    now = [0.0]
    gate = Gate(3, 5, 1000, clock=lambda: now[0])
    now[0] = 1.1
    with pytest.raises(ValueError, match="time_budget"):
        gate.model()
    gate = Gate(3, 5, 1000)
    with pytest.raises(ValueError, match="context_budget"):
        gate.result({"text": "a" * 12001})


def test_req1205_generated_report_has_hard_six_claim_limit():
    claims = [
        {
            "claim_id": f"C{i}",
            "kind": "fact",
            "text": "测试原文事实",
            "citations": [{"context_id": "E1", "evidence_id": "E1", "quote": "测试"}],
        }
        for i in range(1, 8)
    ]
    with pytest.raises(ValidationError):
        BaselineDraft(claims=claims, missing_information=["需补充当前日志。"])
