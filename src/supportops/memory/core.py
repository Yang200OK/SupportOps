"""确定性摘录：核对身份，不把历史模型意见升级为真相。"""

import re
from copy import deepcopy

from supportops.chunks.chunking import digest
from supportops.investigations.hypothesis_validation import CURRENT
from supportops.investigations.live_sources import verify_snapshot
from supportops.retrieval.contexts import text_hash

LIMITATION = "待审核历史排查方法；非本次证据、非动作批准，未证明唯一根因或经验收益。"
READONLY = {
    "search_knowledge",
    "read_runtime_state",
    "read_current_observations",
    "read_startup_diagnostic",
}


def validate_source(source):
    try:
        row = source["investigation"]
        output = row["output"]
        ticket = row["input_snapshot"]["ticket"]
        if (
            digest(row["input_snapshot"]) != row["input_sha256"]
            or digest(output) != row["output_sha256"]
            or output["status"] != row["status"]
            or ticket["ticket_id"] != row["ticket_id"]
        ):
            raise ValueError()
        events = output["events"]
        if not events or [e["sequence"] for e in events] != list(range(1, len(events) + 1)):
            raise ValueError()
        evidence = output["evidence"]
        by_id = {e["evidence_id"]: e for e in evidence}
        if len(by_id) != len(evidence):
            raise ValueError()
        for e in evidence:
            if not e["text_verified"] or (
                "text_sha256" in e and text_hash(e["text"]) != e["text_sha256"]
            ):
                raise ValueError()
        for tool in output["tool_results"]:
            if digest(tool["result"]) != tool["result_sha256"]:
                raise ValueError()
            if "snapshot" in tool["result"]:
                verify_snapshot(tool["result"])
            for item in tool["result"]["evidence"]:
                original = by_id.get(item["evidence_id"])
                # 同一冻结来源多次检索可以改变排名 / 分数，不可改变身份或原文。
                stable = (
                    "evidence_id",
                    "text",
                    "source",
                    "kind",
                    "product_version",
                    "text_verified",
                    "reference_url",
                )
                if original is None or any(original.get(k) != item.get(k) for k in stable):
                    raise ValueError()
        if output["tool_results"] and set(by_id) != {
            e["evidence_id"] for t in output["tool_results"] for e in t["result"]["evidence"]
        }:
            raise ValueError()
        for claim in (output.get("report") or {}).get("claims", []) + output["hypotheses"]:
            refs = claim.get("citations", []) + claim.get("support_citations", [])
            refs += claim.get("refute_citations", [])
            for ref in refs:
                e = by_id.get(ref["evidence_id"])
                if e is None or ref["context_id"] != e["evidence_id"]:
                    raise ValueError()
                start, quote = e["text"].find(ref["quote"]), ref["quote"]
                if (
                    not quote
                    or start < 0
                    or not ref.get("literal_verified")
                    or ref.get("start") != start
                    or ref.get("end") != start + len(quote)
                    or ref.get("context_text_sha256") != text_hash(e["text"])
                ):
                    raise ValueError()
        action = source["action"]
        if action is not None:
            if (
                action["investigation_id"] != row["investigation_id"]
                or action["ticket_id"] != row["ticket_id"]
                or action["status"]
                not in {"completed", "failed", "rejected", "cancelled", "uncertain"}
                or digest(action["proposal"]) != action["proposal_sha256"]
                or digest(action["checkpoint"]) != action["checkpoint_sha256"]
                or action["proposal"]["investigation_output_sha256"] != row["output_sha256"]
            ):
                raise ValueError()
            events = action["events"]
            if [e["sequence"] for e in events] != list(range(1, action["sequence"] + 1)):
                raise ValueError()
            approval = action["approval"]
            if approval and approval["proposal_sha256"] != action["proposal_sha256"]:
                raise ValueError()
            for name in ("action_receipt", "retest_receipt"):
                receipt = action["checkpoint"].get(name)
                if receipt is not None:
                    if (
                        not approval
                        or approval["decision"] != "approve"
                        or digest({k: v for k, v in receipt.items() if k != "sha256"})
                        != receipt["sha256"]
                    ):
                        raise ValueError()
                    expected_operation = (
                        "retest"
                        if name == "retest_receipt"
                        else action["proposal"]["choice"]["action"]
                    )
                    if receipt["result"]["operation"] != expected_operation or not any(
                        e["event"] == "receipt_saved"
                        and e.get("action_id") == receipt["action_id"]
                        and e.get("receipt_sha256") == receipt["sha256"]
                        and e.get("operation") == expected_operation
                        for e in events
                    ):
                        raise ValueError()
                    command = action["checkpoint"]["command"]
                    if command["operation"] == expected_operation:
                        if (
                            command["action_id"] != receipt["action_id"]
                            or digest(command) != receipt["command_sha256"]
                        ):
                            raise ValueError()
                    if name == "retest_receipt":
                        result = receipt["result"]
                        finished = [
                            o
                            for o in result["observations"]
                            if o.get("request_id") == command["request_id"]
                            and o.get("phase") == "retest"
                            and o.get("event") == "request_finished"
                        ]
                        passed = (
                            len(finished) == 1
                            and finished[0].get("status") == 200
                            and result["response"].get("status") == 200
                        )
                        if action["checkpoint"].get("retest_passed") != passed:
                            raise ValueError()
            if action["status"] == "completed" and any(
                not action["checkpoint"].get(k) for k in ("action_receipt", "retest_receipt")
            ):
                raise ValueError()
    except Exception:
        # 公开固定错误，不泄露原始参数；不得生成替代摘要。
        raise ValueError("MEMORY_SOURCE_INVALID") from None


def summarize(source):
    validate_source(source)
    row = source["investigation"]
    output, ticket = row["output"], row["input_snapshot"]["ticket"]
    summary = {
        "method": "extractive-events.v1",
        "status": row["status"],
        "stop_reason": output.get("stop_reason"),
        "event_count": len(output["events"]),
        "event_sha256": digest(output["events"]),
        "plan_count": len(output["plan_history"]),
        "steps": deepcopy(output["steps"]),
        "hypotheses": deepcopy(output["hypotheses"]),
        "claims": deepcopy((output.get("report") or {}).get("claims", [])),
        "missing_information": deepcopy(
            (output.get("report") or {}).get("missing_information", [])
        ),
        "original_usage": deepcopy(output["usage"]),
        "action": None,
        "human_semantic_reviewed": False,
        "current_incident_verified": False,
        "limitation": LIMITATION,
    }
    if source["action"] is not None:
        action = source["action"]
        summary["action"] = {
            "job_id": action["job_id"],
            "status": action["status"],
            "approval": deepcopy(action["approval"]),
            "event_count": action["sequence"],
            "retest_passed": action["checkpoint"].get("retest_passed"),
            "action_receipt_sha256": (action["checkpoint"].get("action_receipt") or {}).get(
                "sha256"
            ),
            "retest_receipt_sha256": (action["checkpoint"].get("retest_receipt") or {}).get(
                "sha256"
            ),
        }
    binding = row["input_snapshot"].get("lab_registration")
    checks = [
        {"step_id": s["step_id"], "tool": s["tool"], "reason": s["reason"][:240]}
        for s in output["steps"]
        if s["status"] == "completed" and s["tool"] in READONLY
    ]
    current = [e for e in output["evidence"] if e["source"].get("source_type") in CURRENT]
    if (
        row["workflow_version"] != "hypothesis-investigation.v1"
        or row["status"] != "completed"
        or not current
        or not binding
        or not checks
        or ticket["product_version"] is None
    ):
        return summary, None
    # 历史症状只是召回词，原因、修复效果与审批均不进入方法候选。
    candidate = {
        "title": ticket["title"][:120],
        "symptoms": ticket["description"][:400],
        "signals": sorted(set(re.findall(r"RD_[A-Z_]+", ticket["description"])))[:8],
        "product_version": ticket["product_version"],
        "environment": ticket["environment"],
        "mode": binding["mode"],
        "checks": checks[:6],
        "distinguishing_signals": [
            {k: h[k][:240] for k in ("support_signal", "refute_signal")}
            for h in output["hypotheses"][:3]
        ],
        "source_investigation_id": row["investigation_id"],
        "source_evidence": [
            {
                "evidence_id": e["evidence_id"],
                "text_sha256": text_hash(e["text"]),
                "reference_url": e["reference_url"],
                "source_type": e["source"]["source_type"],
            }
            for e in current
        ][:8],
        "source_type": "unreviewed_experience_candidate",
        "original_ticket_source_type": ticket["source_type"],
        "human_semantic_reviewed": False,
        "current_incident_verified": False,
        "limitation": LIMITATION,
    }
    return summary, candidate
