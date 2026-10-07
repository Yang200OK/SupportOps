"""通过手算和失败分母验证检索指标。"""

import math

import pytest


def test_req608_graded_metrics_match_manual_calculation():
    from supportops.retrieval.evaluation import metrics

    actual = metrics(["wrong", "b", "a"], {"a": 3, "b": 1}, 3)
    ideal = 7 + 1 / math.log2(3)
    assert actual["recall"] == 1
    assert actual["mrr"] == 0.5
    assert actual["ndcg"] == pytest.approx((1 / math.log2(3) + 7 / 2) / ideal)


def test_req608_empty_hits_zero_and_duplicate_hits_rejected():
    from supportops.retrieval.evaluation import metrics

    assert metrics([], {"a": 2}, 5) == {"recall": 0, "mrr": 0, "ndcg": 0}
    with pytest.raises(ValueError):
        metrics(["a", "a"], {"a": 2}, 5)


def test_req608_failed_and_not_run_remain_in_denominator():
    from supportops.retrieval.evaluation import summarize

    rows = [
        {
            "status": "completed",
            "metrics": {"recall": 1, "mrr": 1, "ndcg": 1},
            "latency_ms": 10,
            "input_tokens": 5,
            "versions_correct": True,
        },
        {"status": "failed", "latency_ms": 30, "input_tokens": None},
        {"status": "not_run", "latency_ms": None, "input_tokens": None},
    ]
    actual = summarize(rows)
    assert actual["total"] == 3 and actual["completed"] == 1 and actual["failed"] == 1
    assert actual["macro_all"]["recall"] == pytest.approx(1 / 3)
    assert actual["p95_ms"] == 30 and actual["cost_cny"] is None
    assert actual["known_input_tokens"] == 5 and actual["unknown_usage_attempts"] == 1
