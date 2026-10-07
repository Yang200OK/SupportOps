"""标签只供离线评测；失败与未执行不从分母中删除。"""

import math


def metrics(hits: list[str], relevance: dict[str, int], k: int) -> dict:
    if (
        k < 1
        or len(set(hits)) != len(hits)
        or not relevance
        or any(type(g) is not int or not 1 <= g <= 3 for g in relevance.values())
    ):
        raise ValueError("排名必须唯一，相关标签必须为 1 至 3。")
    top = hits[:k]
    matched = [i for i, evidence in enumerate(top, 1) if evidence in relevance]
    dcg = sum((2 ** relevance.get(e, 0) - 1) / math.log2(i + 1) for i, e in enumerate(top, 1))
    ideal = sum(
        (2**grade - 1) / math.log2(i + 1)
        for i, grade in enumerate(sorted(relevance.values(), reverse=True)[:k], 1)
    )
    return {
        "recall": len(matched) / len(relevance),
        "mrr": 1 / matched[0] if matched else 0,
        "ndcg": dcg / ideal,
    }


def summarize(attempts: list[dict]) -> dict:
    if not attempts:
        raise ValueError("必须记录全部任务。")
    completed = [r for r in attempts if r["status"] == "completed"]
    measured = sorted(r["latency_ms"] for r in attempts if r["latency_ms"] is not None)
    returned_hits = [hit for row in completed for hit in row.get("result", {}).get("items", [])]
    return {
        "total": len(attempts),
        **{
            status: sum(r["status"] == status for r in attempts)
            for status in ("completed", "failed", "not_run")
        },
        "macro_all": {
            name: sum(r["metrics"][name] for r in completed) / len(attempts)
            for name in ("recall", "mrr", "ndcg")
        },
        "version_correct_completed": (
            sum(r["versions_correct"] for r in completed) / len(completed)
        )
        if completed
        else None,
        "p95_ms": measured[math.ceil(len(measured) * 0.95) - 1] if measured else None,
        "returned_hits": len(returned_hits),
        "version_match_rate": (
            sum(
                hit["product_version"] == row["request"]["product_version"]
                for row in completed
                for hit in row.get("result", {}).get("items", [])
            )
            / len(returned_hits)
            if returned_hits
            else None
        ),
        "known_input_tokens": sum(
            r["input_tokens"] for r in attempts if r["input_tokens"] is not None
        ),
        "unknown_usage_attempts": sum(
            r["input_tokens"] is None and r["status"] != "not_run" for r in attempts
        ),
        "cost_cny": None,
    }
