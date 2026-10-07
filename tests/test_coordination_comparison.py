"""同总预算开发比较必须保留失败与协议差异。"""

from copy import deepcopy

from supportops.coordination.comparison import aggregate, compare


def attempt(arm, status="completed"):
    return {
        "arm": arm,
        "task_sha256": "task",
        "index_sha256": "index",
        "model": "fixed",
        "budget": {
            "tool_calls": 6,
            "model_calls": 8,
            "context_chars": 16000,
            "time_budget_ms": 240000,
        },
        "initial_state_sha256": "state",
        "metrics": {
            "status": status,
            "model_calls": 6,
            "tool_calls": 3,
            "known_calls": 6,
            "unknown_calls": 0,
            "input_tokens": 100,
            "output_tokens": 20,
            "duration_ms": 10,
            "current_evidence": True,
            "claim_count": 2,
            "review_not_supported": 1,
        },
    }


def test_req2001_2003_equal_caps_do_not_prove_topology_benefit():
    left, right = attempt("single"), attempt("multi")
    result = compare(left, right)
    assert result["same_total_budget_conditions"]
    assert result["isolated_topology_comparison"] is False
    assert result["protocol_differences"] and result["human_gold_accuracy"] is None
    right["budget"]["model_calls"] = 24
    assert not compare(left, right)["same_total_budget_conditions"]


def test_req2002_failed_and_missing_attempts_remain_in_denominator():
    rows = [attempt("single"), attempt("single", "failed"), attempt("single")]
    rows[-1]["metrics"] = None
    rows[-1]["status"] = "request_started"
    result = aggregate(rows)
    assert result["attempted"] == 3 and result["completed"] == 1
    assert result["failed_or_stopped"] == 1 and result["missing_responses"] == 1
    assert result["unknown_transport_attempts"] == 1
    assert result["known_calls"] == 12 and result["input_tokens"] == 200
    assert result["human_gold_accuracy"] is None and result["cost_cny"] is None


def test_req2001_missing_condition_and_wrong_assignment_rejected():
    left, right = attempt("single"), attempt("multi")
    changed = deepcopy(right)
    changed.pop("initial_state_sha256")
    assert "initial_state_sha256" in compare(left, changed)["differences"]
    changed["arm"] = "single"
    assert "arm_assignment" in compare(left, changed)["differences"]
