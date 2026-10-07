"""完整工作流的同总预算开发比较；所有尝试纳入分母。"""

PROTOCOL_DIFFERENCES = [
    "单图最多三段 BM25；多 Agent 每次一段，同版本来源索引相同。",
    "单图串行重规划与原因假设；多 Agent 私有角色报告、转交、归并和独立评审。",
    "单图输出上限按阶段 2000～3000；多 Agent 每次 2600，调用上限均为 8。",
    "单图累计工具响应计字符；多 Agent 压缩公共元数据并计入归并转交，有静态子配额。",
]


def compare(single, multi):
    fields = ("task_sha256", "index_sha256", "model", "budget", "initial_state_sha256")
    differences = [k for k in fields if single.get(k) is None or single.get(k) != multi.get(k)]
    if single["arm"] != "single" or multi["arm"] != "multi":
        differences.append("arm_assignment")
    if single.get("metrics") is None or multi.get("metrics") is None:
        differences.append("response_missing")
    return {
        "same_total_budget_conditions": not differences,
        "differences": differences,
        "isolated_topology_comparison": False,
        "protocol_differences": PROTOCOL_DIFFERENCES,
        "single": single.get("metrics"),
        "multi": multi.get("metrics"),
        "human_gold_accuracy": None,
    }


def aggregate(attempts):
    values = [a["metrics"] for a in attempts if a.get("metrics") is not None]
    return {
        "attempted": len(attempts),
        "completed": sum(v["status"] == "completed" for v in values),
        "failed_or_stopped": sum(v["status"] != "completed" for v in values),
        "missing_responses": len(attempts) - len(values),
        "unknown_transport_attempts": sum(
            a.get("status") == "request_started" and a.get("metrics") is None for a in attempts
        ),
        **{
            k: sum(v[k] for v in values)
            for k in (
                "known_calls",
                "unknown_calls",
                "input_tokens",
                "output_tokens",
                "duration_ms",
                "model_calls",
                "tool_calls",
            )
        },
        "current_evidence_attempts": sum(v["current_evidence"] for v in values),
        "claim_count": sum(v["claim_count"] for v in values),
        "review_not_supported": sum(v["review_not_supported"] or 0 for v in values),
        "cost_cny": None,
        "human_gold_accuracy": None,
    }


def metrics(arm, body, duration_ms):
    if arm == "single":
        report, usage = body.get("report") or {}, body["usage"]
        evidence = body["evidence"]
        counts = {
            "known_calls": usage["known_model_calls"],
            "unknown_calls": usage["unknown_model_calls"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
        }
        models, tools = body["model_calls"], body["tool_calls"]
    else:
        report, counts = body.get("result") or {}, body["model_accounting"]
        evidence = [e for task in body["tasks"].values() for e in task["evidence"]]
        models, tools = body["usage"]["model_calls"], body["usage"]["tool_calls"]
        counts = {
            k: counts[k] for k in ("known_calls", "unknown_calls", "input_tokens", "output_tokens")
        }
    claims = report.get("claims", [])
    return {
        "status": body["status"],
        "model_calls": models,
        "tool_calls": tools,
        **counts,
        "duration_ms": duration_ms,
        "current_evidence": any(
            e["source"].get("source_type", "").startswith("current_") for e in evidence
        ),
        "claim_count": len(claims),
        "literal_verified_citations": sum(
            c.get("literal_verified", False) for claim in claims for c in claim["citations"]
        ),
        "review_not_supported": sum(
            c.get("support", {}).get("verdict") != "supported" for c in claims
        )
        if claims
        else None,
    }


def initial_state(lab):
    """去除新实例 / 请求 / 时间身份，保留故障设置；原始准备文件仍完整保存。"""
    if lab["registration"]["mode"] == "startup":
        boot = lab["registration"]["boot"]
        return {
            "mode": "startup",
            "exit_code": boot["exit_code"],
            "observation": {
                k: v
                for k, v in boot["observation"].items()
                if k not in ("request_id", "observed_at", "elapsed_ms")
            },
        }
    product, receiver = lab["actual_states"]
    return {
        "mode": "online",
        "config": product["config"],
        "checked_out": product["checked_out"],
        "active_target": receiver["active_target"],
        "delay_ms": receiver["delay_ms"],
    }
