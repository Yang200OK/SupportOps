"""发布方法的确定性范围和防越权门槛，不冒充模型效果评测。"""

from copy import deepcopy

import pytest
from test_memory import source

from supportops.memory.core import summarize


def candidate():
    return summarize(source())[1]


def test_req1701_method_does_not_promote_history_or_authority():
    from supportops.skills.publication_core import method_body

    value = candidate()
    body = method_body(value)
    assert body["checks"] == value["checks"]
    assert "source_evidence" not in body and "source_investigation_id" not in body
    assert body["role"] == "planning_guidance_only"
    assert body["human_semantic_reviewed"] is False


@pytest.mark.parametrize("fault", ["tool", "extra", "version", "budget", "signal"])
def test_req1703_regression_rejects_corrupted_method(fault):
    from supportops.skills.publication_core import method_body, regression

    expected = method_body(candidate())
    changed = deepcopy(expected)
    if fault == "tool":
        changed["checks"][0]["tool"] = "release_pool"
    elif fault == "extra":
        changed["permissions"] = ["shell"]
    elif fault == "version":
        changed["product_version"] = "2.0"
    elif fault == "budget":
        changed["checks"][0]["reason"] = "过长" * 4000
    else:
        changed["signals"] = []
    result = regression(changed, expected)
    assert result["passed"] is False
    assert any(not c["passed"] for c in result["checks"])


def test_req1703_valid_method_has_named_checks():
    from supportops.skills.publication_core import method_body, regression

    body = method_body(candidate())
    report = regression(body, body)
    assert report["passed"]
    assert {c["name"] for c in report["checks"]} >= {
        "source_projection",
        "readonly_tools",
        "scope",
        "context_limit",
        "current_evidence",
    }


def test_req1704_default_protocol_keeps_publications_off():
    from uuid import uuid4

    from supportops.investigations.hypothesis_contracts import HypothesisRequest

    assert HypothesisRequest(index_id=uuid4()).use_published_skills is False
