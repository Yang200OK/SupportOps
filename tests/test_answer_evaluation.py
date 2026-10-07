"""用反例区分规则覆盖、引用存在与语义支持。"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from supportops.evaluations.answers import Dataset, Labels, Metrics, Report, Task, score, summarize


def test_req1102_failure_and_not_run_remain_in_denominator():
    rows = [
        {
            "status": "completed",
            "latency_ms": 12,
            "metrics": {
                "outcome_match": True,
                "rule_fact_coverage": 1.0,
                "literal_citations": 1,
                "all_versions_match": True,
            },
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "known_model_calls": 2,
                "unknown_usage_calls": 0,
            },
        },
        {
            "status": "failed",
            "latency_ms": 3,
            "metrics": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "known_model_calls": 0,
                "unknown_usage_calls": 1,
            },
        },
        {"status": "not_run", "latency_ms": None, "metrics": None, "usage": None},
    ]
    result = summarize(rows)
    assert result["total"] == 3 and result["failed"] == 1 and result["not_run"] == 1
    assert result["outcome_match_all"] == 1 / 3
    assert result["rule_fact_coverage_all"] == 1 / 3
    assert result["unknown_usage_calls"] == 1 and result["cost_cny"] is None


def test_req1102_claim_rule_cannot_borrow_another_claim_or_quote():
    body = {
        "status": "reviewed",
        "product_version": "1.1",
        "claims": [
            {"text": "connect_timeout_ms", "citations": [], "support": {"verdict": "supported"}},
            {"text": "3000", "citations": [], "support": {"verdict": "supported"}},
        ],
        "contexts": [],
        "retrieval": {"items": []},
        "current_incident_verified": False,
    }
    label = {
        "expected_status": "reviewed",
        "facts": [["connect_timeout_ms", "3000"]],
        "forbidden_patterns": ["EVAL_INJECTED"],
        "required_evidence_ids": [],
    }
    result = score(body, {"product_version": "1.1"}, label)
    assert result["rule_fact_coverage"] == 0
    assert result["model_supported_claims"] == 2
    assert result["uncited_claims"] == 2
    body["claims"][0]["text"] = "EVAL_INJECTED"
    assert not score(body, {"product_version": "1.1"}, label)["forbidden_output_absent"]


def test_req1102_wrong_version_and_fake_literal_flag_are_not_trusted():
    body = {
        "status": "reviewed",
        "product_version": "1.1",
        "claims": [
            {
                "text": "参数为 3000",
                "citations": [
                    {
                        "context_id": "w",
                        "evidence_id": "e",
                        "quote": "伪造",
                        "start": 0,
                        "end": 2,
                        "literal_verified": True,
                        "context_text_sha256": "0" * 64,
                    }
                ],
                "support": {"verdict": "supported"},
            }
        ],
        "contexts": [{"context_id": "w", "text": "原句", "anchor_evidence_ids": ["e"]}],
        "retrieval": {"items": [{"evidence_id": "e", "product_version": "2.0"}]},
        "current_incident_verified": False,
    }
    result = score(
        body,
        {"product_version": "1.1"},
        {
            "expected_status": "reviewed",
            "facts": [],
            "forbidden_patterns": [],
            "required_evidence_ids": ["e"],
        },
    )
    assert result["literal_citations"] == 0 and result["invalid_citations"] == 1
    assert not result["all_versions_match"]


def data():
    root = Path(__file__).resolve().parents[1] / "data/evaluations/answers-v1"
    return json.loads((root / "dataset.json").read_text(encoding="utf-8"))


def test_req1101_frozen_labels_hash_and_input_separation():
    value = data()
    dataset = Dataset.model_validate(value)
    root = Path(__file__).resolve().parents[1] / "data/evaluations/answers-v1"
    labels = Labels.model_validate_json((root / "labels.json").read_text(encoding="utf-8"))
    labels.bind(dataset)
    with pytest.raises(ValueError):
        Task.model_validate({**value["tasks"][0], "expected": {"status": "reviewed"}})
    changed = deepcopy(value)
    changed["tasks"][0]["request"]["query"] += "改变输入"
    with pytest.raises(ValueError):
        labels.bind(Dataset.model_validate(changed))
    changed = deepcopy(value)
    changed["tasks"][0]["split"] = "holdout"
    with pytest.raises(ValueError):
        Dataset.model_validate(changed)
    assert len(dataset.tasks) == 16


def test_req1102_inconsistent_metric_counts_are_rejected():
    body = {
        "status": "no_evidence",
        "product_version": "1.1",
        "claims": [],
        "contexts": [],
        "retrieval": {"items": []},
        "current_incident_verified": False,
    }
    metrics = score(
        body,
        {"product_version": "1.1"},
        {
            "expected_status": "no_evidence",
            "facts": [],
            "required_evidence_ids": [],
            "forbidden_patterns": [],
        },
    )
    metrics["model_supported_claims"] = 2
    with pytest.raises(ValueError):
        Metrics.model_validate(metrics)


def test_req1102_unit_rule_cannot_match_only_identifier_suffix():
    root = Path(__file__).resolve().parents[1] / "data/evaluations/answers-v2"
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))
    label = next(x for x in labels["labels"] if x["task_id"] == "timeout-1.1")
    body = {
        "status": "reviewed",
        "product_version": "1.1",
        "claims": [
            {
                "text": "delivery_timeout_ms 默认值是 3000。",
                "citations": [],
                "support": {"verdict": "supported"},
            }
        ],
        "contexts": [],
        "retrieval": {"items": []},
        "current_incident_verified": False,
    }
    assert score(body, {"product_version": "1.1"}, label)["rule_fact_coverage"] == 0.5


def report():
    value = data()
    return {
        "schema_version": "supportops.answer-report.v1",
        "dataset_sha256": "0" * 64,
        "labels_sha256": "1" * 64,
        "corpus_sha256": value["corpus_sha256"],
        "index_id": value["index_id"],
        "main_model": "main",
        "light_model": "light",
        "embedding_model": "embedding",
        "parameters": "temperature=0;max_tokens=3500/2000;no_retry",
        "transport": "test_protocol",
        "task_ids": [t["task_id"] for t in value["tasks"]],
        "attempts": [
            {
                "task_id": t["task_id"],
                "split": t["split"],
                "category": t["category"],
                "status": "not_run",
                "latency_ms": None,
                "error_code": None,
                "metrics": None,
                "usage": None,
                "reference_readback": None,
            }
            for t in value["tasks"]
        ],
    }


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "not_run_usage", "paid_without_latency", "human_gold"]
)
def test_req1102_report_cannot_hide_denominator_or_fake_measurements(change):
    value = report()
    if change == "missing":
        value["attempts"].pop()
    elif change == "duplicate":
        value["attempts"].append(value["attempts"][0])
    elif change == "not_run_usage":
        value["attempts"][0]["usage"] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "known_model_calls": 0,
            "unknown_usage_calls": 0,
            "cost_cny": None,
        }
    elif change == "paid_without_latency":
        value["attempts"][0]["status"] = "completed"
    else:
        value["human_reviewed"] = True
    with pytest.raises(ValueError):
        Report.model_validate(value)
