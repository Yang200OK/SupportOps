"""纯调度规则；持久化层在行锁内预留每次调用。"""

import json
from copy import deepcopy

from supportops.chunks.chunking import digest
from supportops.investigations.hypothesis_contracts import LIVE_ARGUMENTS

TERMINAL = {"completed", "failed", "cancelled", "uncertain"}


def initial_state(plan):
    return {
        "status": "pending",
        "deadline": None,
        "lease": None,
        "lease_until": None,
        "tasks": {
            t["task_id"]: {
                "status": "pending",
                "phase": "plan",
                "steps": [],
                "next_tool": 0,
                "evidence": [],
                "results": [],
                "report": None,
                "usage": {"model_calls": 0, "tool_calls": 0, "context_chars": 0},
            }
            for t in plan["tasks"]
        },
        "usage": {"model_calls": 0, "tool_calls": 0, "context_chars": 0},
        "operations": [],
        "result": None,
        "error": None,
    }


def task_package(plan, task):
    return next(t["package"] for t in plan["tasks"] if t["task_id"] == task)


def reserve(state, plan, task, kind, key, now):
    if state["status"] in TERMINAL:
        raise ValueError("EXECUTION_TERMINAL")
    if state["deadline"] is not None and now >= state["deadline"]:
        raise ValueError("TIME_BUDGET_EXHAUSTED")
    if any(o["task"] == task and o["key"] == key for o in state["operations"]):
        raise ValueError("OPERATION_ALREADY_RESERVED")
    counter = kind + "_calls"
    cap = counter
    if state["usage"][counter] >= plan["budget"][cap]:
        raise ValueError("TOTAL_BUDGET_EXHAUSTED")
    if state["tasks"][task]["usage"][counter] >= task_package(plan, task)["budget"][cap]:
        raise ValueError("TASK_BUDGET_EXHAUSTED")
    state["usage"][counter] += 1
    state["tasks"][task]["usage"][counter] += 1
    state["operations"].append(
        {
            "task": task,
            "kind": kind,
            "key": key,
            "status": "started",
            "started_at": now,
            "usage": None,
            "raw_response": None,
        }
    )


def recover(state):
    for op in state["operations"]:
        if op["status"] == "started":
            op["status"] = "unknown"
            state["tasks"][op["task"]]["status"] = "failed"
            state["status"] = "uncertain"
            state["error"] = "EXTERNAL_RECEIPT_UNKNOWN"
    for task in state["tasks"].values():
        if task["status"] == "running":
            task["status"] = "pending"
    state["lease"] = state["lease_until"] = None
    if state["status"] == "running":
        state["status"] = "pending"


def validate_steps(package, steps):
    if not steps or len(steps) > package["budget"]["tool_calls"]:
        raise ValueError("TASK_BUDGET_EXHAUSTED")
    seen = set()
    for step in steps:
        if step["tool"] not in package["tools"]:
            raise ValueError("COORDINATION_ROLE_DENIED")
        LIVE_ARGUMENTS[step["tool"]].model_validate(step["arguments"])
        # 同一查询的外围空白不得绕开重复工具建议门槛。
        args = {k: v.strip() if isinstance(v, str) else v for k, v in step["arguments"].items()}
        identity = digest({"tool": step["tool"], "arguments": args})
        if identity in seen:
            raise ValueError("DUPLICATE_TOOL_PROPOSAL")
        seen.add(identity)


def collect_evidence(groups):
    by_id = {}
    rank_fields = {
        "rank",
        "vector_rank",
        "bm25_rank",
        "cosine_similarity",
        "bm25_score",
        "rrf_score",
    }
    for group in groups:
        for item in group:
            identity = item["evidence_id"]
            # 同一固定来源可被不同查询命中；请求内排序分数不是来源正文或身份。
            if identity in by_id and (
                {k: v for k, v in by_id[identity].items() if k not in rank_fields}
                != {k: v for k, v in item.items() if k not in rank_fields}
            ):
                raise ValueError("EVIDENCE_IDENTITY_CONFLICT")
            if identity not in by_id:
                by_id[identity] = deepcopy(item)
    return list(by_id.values())


def result_chars(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
