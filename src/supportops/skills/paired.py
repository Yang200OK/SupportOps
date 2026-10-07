"""小样本开发比较保留全部尝试，流程完成和语义判断分别报告。"""


def metrics(attempt):
    response = attempt.get("response")
    if response is None:
        return {
            "status": "missing_response",
            "current_evidence": None,
            "review_not_supported": None,
        }
    claims = (response.get("report") or {}).get("claims", [])
    return {
        "status": response["status"],
        "tool_calls": response["tool_calls"],
        "model_calls": response["model_calls"],
        "duration_ms": response["duration_ms"],
        "current_evidence": response["has_current_evidence"],
        "review_not_supported": sum(c["support"]["verdict"] != "supported" for c in claims)
        if claims
        else None,
        "usage": response["usage"],
        "claim_count": len(claims),
        "human_semantic_reviewed": False,
    }


def compare(off, on):
    fields = ("initial_state_sha256", "task_sha256", "index_sha256", "model", "budget")
    differences = [
        field
        for field in fields
        if off.get(field) is None or on.get(field) is None or off.get(field) != on.get(field)
    ]
    if off["use_memory"] or not on["use_memory"] or not on.get("memory_sha256"):
        differences.append("memory_assignment")
    return {
        "comparable": not differences,
        "differences": differences,
        "off": metrics(off),
        "on": metrics(on),
        "gold_accuracy": None,
        "limitation": "开发模板小样本；模型评审非人工金标准，单次比较不证明因果或显著收益。",
    }


def aggregate(attempts):
    responses = [a["response"] for a in attempts if a.get("response") is not None]
    return {
        "attempted": len(attempts),
        "completed": sum(r["status"] == "completed" for r in responses),
        "failed_or_stopped": sum(r["status"] != "completed" for r in responses),
        "missing_responses": len(attempts) - len(responses),
        "unknown_transport_attempts": sum(
            a.get("status") == "request_started" and a.get("response") is None for a in attempts
        ),
        "current_evidence_attempts": sum(bool(r["has_current_evidence"]) for r in responses),
        "known_model_calls": sum(r["usage"]["known_model_calls"] for r in responses),
        "unknown_model_calls": sum(r["usage"]["unknown_model_calls"] for r in responses),
        "known_input_tokens": sum(r["usage"]["input_tokens"] for r in responses),
        "known_output_tokens": sum(r["usage"]["output_tokens"] for r in responses),
        "duration_ms_sum": sum(r["duration_ms"] for r in responses),
        "cost_cny": None,
        "human_gold_accuracy": None,
    }
