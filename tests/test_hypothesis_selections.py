"""应用从已取证原文绑定引文；不修补模型输出或伪造支持。"""

import pytest
from pydantic import ValidationError
from test_hypothesis_contracts import hypothesis

from supportops.investigations.selections import bind_plan, plan_schema, quote_catalog


def evidence():
    return [
        {
            "evidence_id": "E1",
            "text": '{"checked_out":2,"error_code":"RD_POOL_WAIT"}',
            "kind": "log",
            "text_verified": True,
            "source": {"source_type": "current_lab_observations"},
        },
        {
            "evidence_id": "D1",
            "text": "RD_POOL_WAIT 是等待上限触发。",
            "kind": "document",
            "text_verified": True,
            "source": {},
        },
    ]


def payload():
    h = hypothesis(status="supported", support_citations=[{"evidence_id": "E1", "quote_index": 0}])
    return {
        "hypotheses": [h],
        "steps": [],
        "action": "finish",
        "next_step_id": None,
        "reason": "本次观测支持候选",
    }


def test_req1305_application_binds_exact_source_fragment_without_llm_copy():
    rows = evidence()
    catalog = quote_catalog(rows)
    choice = plan_schema(rows).model_validate(payload())
    bound = bind_plan(choice, catalog)
    assert bound.hypotheses[0].support_citations[0].quote == rows[0]["text"]
    assert bound.hypotheses[0].support_citations[0].context_id == "E1"


def test_req1305_plan_schema_restricts_cause_refs_to_current_ids():
    value = payload()
    value["hypotheses"][0]["support_citations"][0]["evidence_id"] = "D1"
    with pytest.raises(ValidationError):
        plan_schema(evidence()).model_validate(value)


def test_req1305_invalid_selection_stops_without_quote_substitution():
    value = payload()
    value["hypotheses"][0]["support_citations"][0]["quote_index"] = 12
    choice = plan_schema(evidence()).model_validate(value)
    with pytest.raises(ValueError, match="HYPOTHESIS_CITATION_INVALID"):
        bind_plan(choice, quote_catalog(evidence()))


def test_req1302_known_causes_frozen_but_new_candidate_can_use_remaining_slot():
    existing = payload()["hypotheses"]
    value = payload()
    new = hypothesis(hypothesis_id="H2", cause="新观测提示另一原因")
    value["hypotheses"].append(new)
    selected = plan_schema(evidence(), existing).model_validate(value)
    assert len(selected.hypotheses) == 2
    value["hypotheses"][0]["cause"] = "偷偷替换旧原因"
    with pytest.raises(ValidationError):
        plan_schema(evidence(), existing).model_validate(value)
