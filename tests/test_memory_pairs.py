"""配对条件与失败分母，避免只对成功样本声称记忆收益。"""

import pytest


def attempt(memory=False, status="completed"):
    return {
        "use_memory": memory,
        "initial_state_sha256": "same",
        "task_sha256": "task",
        "index_sha256": "index",
        "model": "fixed",
        "budget": {"max_model_calls": 8},
        "memory_sha256": "frozen" if memory else None,
        "response": {
            "status": status,
            "tool_calls": 3,
            "model_calls": 4,
            "duration_ms": 100,
            "has_current_evidence": status == "completed",
            "usage": {
                "known_model_calls": 3,
                "unknown_model_calls": 1,
                "input_tokens": 10,
                "output_tokens": 5,
            },
            "report": None,
        },
    }


@pytest.mark.parametrize(
    "field", ["initial_state_sha256", "task_sha256", "index_sha256", "model", "budget"]
)
def test_req1705_changed_condition_not_comparable(field):
    from supportops.skills.paired import compare

    off, on = attempt(), attempt(True)
    on[field] = "different"
    report = compare(off, on)
    assert report["comparable"] is False and field in report["differences"]


def test_req1705_failed_and_missing_stay_in_denominator():
    from supportops.skills.paired import aggregate

    result = aggregate(
        [attempt(), attempt(status="failed"), {"use_memory": False, "response": None}]
    )
    assert (
        result["attempted"] == 3 and result["completed"] == 1 and result["missing_responses"] == 1
    )
    assert result["known_model_calls"] == 6 and result["unknown_model_calls"] == 2
    assert result["cost_cny"] is None
