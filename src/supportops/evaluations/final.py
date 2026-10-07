"""最终评测的冻结、全网格与分层汇总；标签从不进入运行请求。"""

import math
from collections import Counter

from supportops.chunks.chunking import digest

VARIANTS = {
    "retrieval": ("vector", "bm25", "rrf", "rrf-parent", "rrf-rerank", "rrf-rerank-parent"),
    "answers": ("direct", "guided"),
    "agents": ("single", "memory", "multi"),
}
STATES = {"not_run", "preparing", "request_started", "completed", "failed", "stopped"}
CONDITIONS = (
    "task_id",
    "repeat",
    "task_sha256",
    "index_sha256",
    "model",
    "budget",
    "initial_state_sha256",
)


def bind_manifest(manifest):
    if manifest.get("sha256") != digest({k: v for k, v in manifest.items() if k != "sha256"}):
        raise ValueError("冻结摘要不匹配。")


def grid(manifest, suite):
    # 第二次逆序，第一次各题轮转先后顺序；全部槽位在第一笔收费前存在。
    rows = []
    variants = VARIANTS[suite]
    for repeat in range(1, manifest["repeats"] + 1):
        for ordinal, task in enumerate(manifest["tasks"][suite]):
            shift = ordinal % len(variants)
            order = variants[shift:] + variants[:shift]
            if repeat % 2 == 0:
                order = tuple(reversed(order))
            for variant in order:
                rows.append(
                    {
                        "task_id": task["task_id"],
                        "repeat": repeat,
                        "variant": variant,
                        "status": "not_run",
                        "duration_ms": None,
                        "response": None,
                        "usage": None,
                        "quality": None,
                        "error_code": None,
                    }
                )
    return rows


def validate_report(manifest, report):
    bind_manifest(manifest)
    if report["manifest_sha256"] != manifest["sha256"]:
        raise ValueError("报告不属于当前冻结。")

    def key(r):
        return r["task_id"], r["repeat"], r["variant"]

    expected = [key(r) for r in grid(manifest, report["suite"])]
    actual = [key(r) for r in report["attempts"]]
    if actual != expected or any(r["status"] not in STATES for r in report["attempts"]):
        raise ValueError("报告必须保留完整有序网格，不能删减、重复或替换。")
    for row in report["attempts"]:
        if row["status"] == "completed" and (row["response"] is None or row["quality"] is None):
            raise ValueError("完成槽位必须有原响应和指标。")
        if row["status"] == "not_run" and any(
            row[k] is not None for k in ("response", "duration_ms", "usage", "quality")
        ):
            raise ValueError("未运行槽位不能包含测量值。")


def usage(suite, body, http_status):
    paid = body.get("usage")
    if paid is None:
        return None
    if suite == "retrieval" and "known_model_calls" not in paid:
        stages = [body.get("retrieval_usage", paid)]
        if "rerank_usage" in body:
            stages.append(body["rerank_usage"])
        return {
            "known_calls": sum(bool(s.get("model_called")) for s in stages),
            "unknown_calls": 0,
            "input_tokens": sum(s["input_tokens"] for s in stages),
            "output_tokens": 0,
        }
    return {
        "known_calls": paid["known_model_calls"],
        "unknown_calls": paid["unknown_usage_calls"],
        "input_tokens": paid.get("input_tokens") or 0,
        "output_tokens": paid.get("output_tokens") or 0,
    }


def paired_conditions(left, right):
    differences = [k for k in CONDITIONS if left.get(k) is None or left.get(k) != right.get(k)]
    return {"matched": not differences, "differences": differences}


def summarize(rows):
    if not rows:
        raise ValueError("不能汇总空分母。")
    complete = [r for r in rows if r["status"] == "completed"]
    keys = sorted({k for r in complete for k in (r.get("quality") or {})})
    times = sorted(r["duration_ms"] for r in rows if r.get("duration_ms") is not None)
    paid = [r["usage"] for r in rows if r.get("usage") is not None]
    return {
        "total": len(rows),
        **{s: sum(r["status"] == s for r in rows) for s in sorted(STATES)},
        "quality_all": {
            k: sum((r.get("quality") or {}).get(k) or 0 for r in complete) / len(rows) for k in keys
        },
        **{
            k: sum(p[k] for p in paid)
            for k in ("known_calls", "unknown_calls", "input_tokens", "output_tokens")
        },
        "unknown_pipeline_attempts": sum(
            r["status"] not in ("not_run", "preparing") and r.get("usage") is None for r in rows
        ),
        "p95_ms_all_attempts": times[math.ceil(len(times) * 0.95) - 1] if times else None,
        "duration_ms_all_attempts": sum(times),
        "errors": dict(Counter(r["error_code"] for r in rows if r.get("error_code"))),
        "cost_cny": None,
        "human_gold_accuracy": None,
        "human_reviewed": False,
    }
