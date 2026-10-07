"""假设状态与调查预算的确定性边界，先保存真实失败。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from supportops.investigations.hypothesis_contracts import Hypothesis, HypothesisRequest, PlanStep
from supportops.investigations.hypothesis_validation import validate_hypotheses
from supportops.investigations.live_guard import LiveGate


def hypothesis(**change):
    return {
        "hypothesis_id": "H1",
        "cause": "下游等待超过超时阈值",
        "basis": "工单报超时，尚待观测。",
        "support_signal": "下游实测等待超出本次有效阈值",
        "refute_signal": "请求未进入下游并有其它失败信号",
        "missing_information": ["需要本次请求链"],
        "status": "proposed",
        "reason": "待检查",
        "support_citations": [],
        "refute_citations": [],
        **change,
    }


def evidence(origin="current_lab_observations"):
    return [
        {
            "evidence_id": "E1",
            "kind": "log",
            "text": "实际等待 2000 毫秒",
            "text_verified": True,
            "source": {"source_type": origin},
        }
    ]


def citation():
    return {"context_id": "E1", "evidence_id": "E1", "quote": "实际等待 2000 毫秒"}


def test_req1301_request_cannot_expand_live_scope():
    payload = {"index_id": str(uuid4()), "lab_run_id": str(uuid4())}
    for key in ("organization_id", "product_version", "run_id", "url", "control_token"):
        with pytest.raises(ValidationError):
            HypothesisRequest.model_validate({**payload, key: "untrusted"})
    assert HypothesisRequest.model_validate(payload).max_model_calls == 8


def test_req1302_steps_cannot_request_control_actions():
    step = {
        "step_id": "S1",
        "hypothesis_ids": ["H1"],
        "reason": "区分下游等待",
        "tool": "read_runtime_state",
        "arguments": {},
    }
    PlanStep.model_validate(step)
    for change in ({"tool": "release_pool"}, {"arguments": {"run_id": str(uuid4())}}):
        with pytest.raises(ValidationError):
            PlanStep.model_validate({**step, **change})


@pytest.mark.parametrize(
    "status,field", [("supported", "support_citations"), ("refuted", "refute_citations")]
)
def test_req1305_historical_citation_cannot_decide_current_cause(status, field):
    item = Hypothesis.model_validate(hypothesis(status=status, **{field: [citation()]}))
    with pytest.raises(ValueError, match="HYPOTHESIS_CURRENT_EVIDENCE_REQUIRED"):
        validate_hypotheses([item], evidence("historical_lab_failure"))
    checked = validate_hypotheses([item], evidence())
    assert checked[0]["status"] == status
    assert checked[0][field][0]["literal_verified"]


def test_req1305_conflicting_observations_remain_unresolved():
    item = Hypothesis.model_validate(
        hypothesis(
            status="unresolved", support_citations=[citation()], refute_citations=[citation()]
        )
    )
    assert validate_hypotheses([item], evidence())[0]["status"] == "unresolved"
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(
            hypothesis(
                status="supported", support_citations=[citation()], refute_citations=[citation()]
            )
        )


def test_req1305_missing_signal_does_not_refute_and_forged_quote_fails():
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(hypothesis(status="refuted"))
    item = Hypothesis.model_validate(
        hypothesis(status="supported", support_citations=[{**citation(), "quote": "不存在的原句"}])
    )
    with pytest.raises(ValueError, match="HYPOTHESIS_CITATION_INVALID"):
        validate_hypotheses([item], evidence())


def test_req1306_reserve_report_review_and_repeat_without_new_evidence():
    gate = LiveGate(6, 3, 240000)
    gate.model(reserve=2)
    with pytest.raises(ValueError, match="model_budget"):
        gate.model(reserve=2)
    gate.tool("search_knowledge", {"query": " RD_TIMEOUT  "}, "evidence-v1")
    with pytest.raises(ValueError, match="repeated_tool"):
        gate.tool("search_knowledge", {"query": "rd_timeout"}, "evidence-v1")
    gate.tool("search_knowledge", {"query": "RD_TIMEOUT"}, "evidence-v2")


def test_req1306_time_and_context_budget_are_code_boundaries():
    now = [0.0]
    gate = LiveGate(6, 8, 1000, clock=lambda: now[0])
    now[0] = 1.0
    with pytest.raises(ValueError, match="time_budget"):
        gate.tool("get_ticket", {}, "empty")
    gate = LiveGate(6, 8, 240000)
    with pytest.raises(ValueError, match="context_budget"):
        gate.result({"text": "中" * 16000})
