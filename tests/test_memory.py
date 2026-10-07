"""摘录边界与完整性反例；不以文本摘要冒充模型质量验证。"""

from copy import deepcopy
from uuid import uuid4

import pytest

from supportops.chunks.chunking import digest
from supportops.retrieval.contexts import text_hash


def source(status="completed"):
    text = '{"error_code":"RD_CONFIG_INVALID"}'
    evidence = {
        "evidence_id": "live:one",
        "text": text,
        "text_sha256": text_hash(text),
        "text_verified": True,
        "source": {"source_type": "current_startup_diagnostic"},
        "reference_url": "/api/investigations/one/evidence/live:one",
    }
    snapshot = {
        "ticket": {
            "ticket_id": str(uuid4()),
            "title": "配置启动失败",
            "description": text,
            "product_version": "1.1",
            "environment": "local_lab",
            "source_type": "synthetic_case",
        },
        "lab_registration": {"mode": "startup", "product_version": "1.1"},
    }
    output = {
        "status": status,
        "stop_reason": None,
        "events": [
            {"sequence": 1, "event": "investigation_started"},
            {"sequence": 2, "event": "investigation_finished"},
        ],
        "evidence": [evidence],
        "tool_results": [],
        "plan_history": [],
        "steps": [
            {
                "step_id": "S1",
                "tool": "read_startup_diagnostic",
                "reason": "核对本次启动的拒绝信号",
                "status": "completed",
            }
        ],
        "hypotheses": [],
        "report": None,
        "usage": {},
        "has_current_evidence": True,
    }
    return {
        "investigation": {
            "investigation_id": str(uuid4()),
            "ticket_id": snapshot["ticket"]["ticket_id"],
            "workflow_version": "hypothesis-investigation.v1",
            "status": status,
            "input_snapshot": snapshot,
            "input_sha256": digest(snapshot),
            "output": output,
            "output_sha256": digest(output),
        },
        "action": None,
    }


def test_req1602_extract_only_methods_and_no_root_cause_promotion():
    from supportops.memory.core import summarize

    original = source()
    summary, candidate = summarize(original)
    assert summary["method"] == "extractive-events.v1"
    assert summary["event_count"] == 2
    assert candidate["checks"][0]["tool"] == "read_startup_diagnostic"
    assert candidate["human_semantic_reviewed"] is False
    assert candidate["current_incident_verified"] is False
    assert candidate["product_version"] == "1.1" and candidate["mode"] == "startup"
    assert original == deepcopy(original)


@pytest.mark.parametrize("status", ["failed", "stopped", "needs_clarification", "no_evidence"])
def test_req1602_noncompleted_sources_have_no_candidate(status):
    from supportops.memory.core import summarize

    summary, candidate = summarize(source(status))
    assert summary["status"] == status and candidate is None


@pytest.mark.parametrize("fault", ["input", "output", "events", "text", "quote", "duplicate"])
def test_req1601_source_corruption_rejected(fault):
    from supportops.memory.core import summarize

    value = source()
    row = value["investigation"]
    if fault == "input":
        row["input_snapshot"]["ticket"]["title"] = "漂移"
    elif fault == "output":
        row["output"]["status"] = "failed"
    else:
        output = row["output"]
        if fault == "events":
            output["events"].pop(0)
        elif fault == "text":
            output["evidence"][0]["text"] = "漂移"
        elif fault == "quote":
            output["report"] = {
                "claims": [
                    {
                        "citations": [
                            {"evidence_id": "live:one", "context_id": "live:one", "quote": "不存在"}
                        ]
                    }
                ]
            }
        else:
            output["evidence"].append(deepcopy(output["evidence"][0]))
        row["output_sha256"] = digest(output)
    with pytest.raises(ValueError, match="MEMORY_SOURCE_INVALID"):
        summarize(value)


def test_req1602_no_current_evidence_does_not_produce_candidate():
    from supportops.memory.core import summarize

    value = source()
    row = value["investigation"]
    row["output"]["evidence"][0]["source"]["source_type"] = "document"
    row["output_sha256"] = digest(row["output"])
    assert summarize(value)[1] is None


def action_source():
    value = source()
    row = value["investigation"]
    command = {"operation": "retest", "action_id": str(uuid4()), "request_id": str(uuid4())}
    proposal = {
        "choice": {"action": "release_pool"},
        "investigation_output_sha256": row["output_sha256"],
    }
    approval = {"decision": "approve", "proposal_sha256": digest(proposal)}
    receipt = {
        "action_id": command["action_id"],
        "command_sha256": digest(command),
        "result": {
            "operation": "retest",
            "response": {"status": 200},
            "observations": [
                {
                    "request_id": command["request_id"],
                    "phase": "retest",
                    "event": "request_finished",
                    "status": 200,
                }
            ],
        },
    }
    receipt["sha256"] = digest(receipt)
    action_receipt = {
        "action_id": str(uuid4()),
        "command_sha256": "0" * 64,
        "result": {"operation": "release_pool"},
    }
    action_receipt["sha256"] = digest(action_receipt)
    checkpoint = {
        "command": command,
        "action_receipt": action_receipt,
        "retest_receipt": receipt,
        "retest_passed": True,
    }
    events = [
        {
            "sequence": i,
            "event": "receipt_saved",
            "operation": r["result"]["operation"],
            "action_id": r["action_id"],
            "receipt_sha256": r["sha256"],
        }
        for i, r in enumerate([action_receipt, receipt], 1)
    ]
    value["action"] = {
        "job_id": str(uuid4()),
        "investigation_id": row["investigation_id"],
        "ticket_id": row["ticket_id"],
        "status": "completed",
        "proposal": proposal,
        "proposal_sha256": digest(proposal),
        "approval": approval,
        "checkpoint": checkpoint,
        "checkpoint_sha256": digest(checkpoint),
        "sequence": 2,
        "events": events,
    }
    return value


def test_req1601_approved_retest_excerpt_is_not_root_cause_proof():
    from supportops.memory.core import summarize

    summary, candidate = summarize(action_source())
    assert summary["action"]["retest_passed"] is True
    assert summary["current_incident_verified"] is False
    assert "action" not in candidate and "root_cause" not in candidate


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "proposal",
        "approval",
        "events",
        "receipt",
        "command",
        "retest_result",
        "missing_receipt",
    ],
)
def test_req1601_action_corruption_rejected(fault):
    from supportops.memory.core import summarize

    value = action_source()
    action = value["action"]
    if fault == "scope":
        action["investigation_id"] = str(uuid4())
    elif fault == "proposal":
        action["proposal"]["investigation_output_sha256"] = "0" * 64
        action["proposal_sha256"] = digest(action["proposal"])
        action["approval"]["proposal_sha256"] = action["proposal_sha256"]
    elif fault == "approval":
        action["approval"]["decision"] = "reject"
    elif fault == "events":
        action["events"].pop(0)
    elif fault == "receipt":
        action["checkpoint"]["retest_receipt"]["result"]["response"]["status"] = 500
    elif fault == "command":
        action["checkpoint"]["command"]["request_id"] = str(uuid4())
    elif fault == "retest_result":
        action["checkpoint"]["retest_passed"] = False
    else:
        del action["checkpoint"]["action_receipt"]
    action["checkpoint_sha256"] = digest(action["checkpoint"])
    with pytest.raises(ValueError, match="MEMORY_SOURCE_INVALID"):
        summarize(value)


def test_req1601_retrieval_rank_changes_keep_same_source_identity():
    from supportops.memory.core import summarize

    value = source()
    row = value["investigation"]
    evidence = row["output"]["evidence"][0]
    first, second = {"evidence": [{**evidence, "rank": 1}]}, {"evidence": [{**evidence, "rank": 2}]}
    row["output"]["tool_results"] = [
        {"result": result, "result_sha256": digest(result)} for result in (first, second)
    ]
    row["output_sha256"] = digest(row["output"])
    assert summarize(value)[1] is not None
