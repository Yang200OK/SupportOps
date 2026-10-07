"""固定摘录投影与结构回归；语义正确性仍需另行评测。"""

import json
from copy import deepcopy

from supportops.chunks.chunking import digest
from supportops.memory.core import READONLY

LIMITATION = "已审批发布的历史检查方法；非本次证据、非动作批准，未经人工根因或收益审定。"


def method_body(candidate):
    # 来源身份另存于发布记录，模型只看到方法，不能将历史证据 ID 当现场引用。
    return {
        "schema_version": "published-guidance.v1",
        **{
            k: deepcopy(candidate[k])
            for k in (
                "title",
                "signals",
                "product_version",
                "environment",
                "mode",
                "checks",
                "distinguishing_signals",
            )
        },
        "role": "planning_guidance_only",
        "human_semantic_reviewed": False,
        "limitation": LIMITATION,
    }


def regression(body, expected):
    checks = {
        "source_projection": body == expected,
        "readonly_tools": bool(body.get("checks"))
        and all(
            set(c) == {"step_id", "tool", "reason"} and c["tool"] in READONLY
            for c in body.get("checks", [])
        ),
        "scope": body.get("product_version") in ("1.0", "1.1", "2.0")
        and body.get("environment") == "local_lab"
        and body.get("mode") in ("startup", "online")
        and all(body.get(k) == expected[k] for k in ("product_version", "environment", "mode")),
        "context_limit": len(json.dumps(body, ensure_ascii=False)) <= 4000,
        "matching_signal": bool(body.get("signals"))
        and all(isinstance(s, str) and s.startswith("RD_") for s in body.get("signals", [])),
        "current_evidence": body.get("role") == "planning_guidance_only"
        and not (
            {"source_evidence", "source_investigation_id", "claims", "permissions", "actions"}
            & body.keys()
        ),
    }
    return {
        "suite": "publication-structural.v1",
        "method_sha256": digest(body),
        "checks": [{"name": k, "passed": v} for k, v in checks.items()],
        "passed": all(checks.values()),
        "model_calls": 0,
        "semantic_effectiveness_verified": False,
        "limitation": "只验证投影、范围、只读结构及预算；运行控制由应用回归验证，不证明语义收益。",
    }
