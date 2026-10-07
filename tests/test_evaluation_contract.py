"""评测格式验证覆盖分组泄漏、答案隔离和完整分母。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from supportops.evaluations.contracts import EvaluationDataset, EvaluationReport


def dataset():
    return {
        "schema_version": "supportops.evaluation-dataset.v1",
        "dataset_id": "format-demo",
        "tasks": [
            {
                "task_id": "dev-01",
                "source_group": "config-01",
                "split": "dev",
                "input": {
                    "title": "超时",
                    "description": "RD_TIMEOUT",
                    "product": "relaydesk",
                    "product_version": "1.1",
                    "environment": "local_lab",
                    "source_type": "synthetic_case",
                },
                "expected": {"outcome": "needs_evidence", "evidence_ids": []},
            },
            {
                "task_id": "holdout-01",
                "source_group": "config-02",
                "split": "holdout",
                "input": {
                    "title": "版本缺失",
                    "description": "RD_TIMEOUT",
                    "product": "relaydesk",
                    "product_version": None,
                    "environment": "local_lab",
                    "source_type": "synthetic_case",
                },
                "expected": {"outcome": "needs_clarification", "evidence_ids": []},
            },
        ],
    }


def test_req205_model_projection_excludes_expected_and_partition():
    parsed = EvaluationDataset.model_validate(dataset())
    projected = parsed.tasks[0].model_input()
    assert projected == {
        "task_id": "dev-01",
        "input": parsed.tasks[0].input.model_dump(mode="json"),
    }
    assert (
        "expected" not in projected and "split" not in projected and "source_group" not in projected
    )


@pytest.mark.parametrize("change", ["duplicate", "leak", "extra", "empty"])
def test_req205_rejects_duplicates_group_leakage_and_unknown_fields(change):
    payload = deepcopy(dataset())
    if change == "duplicate":
        payload["tasks"][1]["task_id"] = "dev-01"
    elif change == "leak":
        payload["tasks"][1]["source_group"] = "config-01"
    elif change == "extra":
        payload["tasks"][0]["input"]["root_cause"] = "hidden answer"
    else:
        payload["tasks"] = []
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(payload)


def test_req205_digest_changes_with_annotations_and_is_canonical():
    payload = dataset()
    original = EvaluationDataset.model_validate(payload)
    reordered = {key: payload[key] for key in reversed(payload)}
    assert original.digest() == EvaluationDataset.model_validate(reordered).digest()
    payload["tasks"][0]["expected"]["evidence_ids"] = ["source:1"]
    assert original.digest() != EvaluationDataset.model_validate(payload).digest()


def report():
    return {
        "schema_version": "supportops.evaluation-report.v1",
        "dataset_sha256": "a" * 64,
        "model": "explicit-model",
        "parameters": {"temperature": 0},
        "knowledge_sha256": None,
        "task_ids": ["t1", "t2"],
        "attempts": [
            {
                "task_id": "t1",
                "status": "completed",
                "latency_ms": 20,
                "input_tokens": 10,
                "output_tokens": 2,
                "cost_cny": None,
                "error_code": None,
            },
            {
                "task_id": "t2",
                "status": "failed",
                "latency_ms": 30,
                "input_tokens": None,
                "output_tokens": None,
                "cost_cny": None,
                "error_code": "MODEL_TIMEOUT",
            },
        ],
    }


def test_req206_failed_attempts_remain_in_denominator_and_unknown_cost_is_null():
    value = EvaluationReport.model_validate(report())
    assert value.summary() == {"total": 2, "completed": 1, "failed": 1, "not_run": 0}
    assert value.attempts[0].cost_cny is None


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "negative", "infinite", "failure_without_code"]
)
def test_req206_rejects_incomplete_denominator_or_invalid_measurements(change):
    value = report()
    if change == "missing":
        value["attempts"].pop()
    elif change == "duplicate":
        value["attempts"][1]["task_id"] = "t1"
    elif change == "negative":
        value["attempts"][0]["input_tokens"] = -1
    elif change == "infinite":
        value["attempts"][0]["latency_ms"] = float("inf")
    else:
        value["attempts"][1]["error_code"] = None
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(value)
