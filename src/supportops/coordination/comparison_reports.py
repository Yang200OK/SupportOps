"""固定本轮报告按组织重新绑定持久化记录，读取不调用模型。"""

import json
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.coordination import execution_service
from supportops.coordination import service as boards
from supportops.coordination.comparison import (
    PROTOCOL_DIFFERENCES,
    aggregate,
    compare,
    initial_state,
    metrics,
)
from supportops.investigations import service as investigations
from supportops.settings import ROOT
from supportops.tickets.service import read_ticket

REPORT_PATH = ROOT / "docs/verification/phase-7-round-3/pairs-attempt1.json"
CAPS = {"tool_calls": 6, "model_calls": 8, "time_budget_ms": 240000, "context_chars": 16000}
FAMILIES = ("configuration", "pool", "cache", "downstream")


def invalid():
    return ServiceError(
        409, "COORDINATION_COMPARISON_INVALID", "比较报告与原始准备或持久化记录不一致。"
    )


def require(condition):
    if not condition:
        raise invalid()


def read_json(path, limit=8000000):
    with path.open("rb") as file:
        raw = file.read(limit + 1)
    require(len(raw) <= limit)
    return json.loads(raw.decode("utf-8"))


def bind(session, principal, item, task):
    require(item["task_sha256"] == digest(task["ticket"]))
    require(item["budget"] == CAPS and item["arm"] in ("single", "multi"))
    body = item.get("response")
    if body is None:
        require(item.get("metrics") is None)
        return
    arm = item["arm"]
    if arm == "single":
        original = investigations.read(session, principal, body["investigation_id"])
        snapshot = original["input_snapshot"]
        request = snapshot["request"]
        require(original["workflow_version"] == "hypothesis-investigation.v1")
        require(
            not any(
                request.get(k, False) for k in ("use_memory", "use_skills", "use_published_skills")
            )
        )
        require(
            all(
                c["requested_model"] == c["returned_model"] == item["model"]
                for c in original["usage"]["calls"]
            )
        )
    else:
        row = execution_service.get(session, principal, body["execution_id"])
        original = execution_service.view(session, row)
        snapshot = boards.get_board(session, principal, row.board_id).input_snapshot
        request = snapshot["request"]
        require(row.binding["model"]["main_model"] == item["model"])
        require(original["budget"] == CAPS)
        # 原始已知回执记录模型身份；测试替身不伪造付费原始响应。
        require(
            all(
                o["usage"]["requested_model"] == o["usage"]["returned_model"] == item["model"]
                for o in original["operations"]
                if o["kind"] == "model" and o["usage"] is not None
            )
        )
    require(jsonable_encoder(original) == body)
    require(snapshot["ticket"] == task["ticket"])
    require(snapshot["knowledge"]["corpus_sha256"] == item["index_sha256"])
    require(request["lab_run_id"] == item["lab_run_id"])
    require(
        all(
            request[k] == CAPS[v]
            for k, v in (
                ("max_tool_calls", "tool_calls"),
                ("max_model_calls", "model_calls"),
                ("time_budget_ms", "time_budget_ms"),
            )
        )
    )
    prepared = (ROOT / Path(item["prepared_path"])).resolve()
    require(prepared.is_relative_to(REPORT_PATH.parent.resolve()) and prepared.suffix == ".json")
    lab = read_json(prepared, 1000000)
    require(
        digest(lab) == item["prepared_sha256"]
        and lab["registration"] == snapshot["lab_registration"]
        and lab["lab_run_id"] == request["lab_run_id"]
    )
    state = initial_state(lab)
    require(state == item["initial_state"] and digest(state) == item["initial_state_sha256"])
    require(type(item["duration_ms"]) is int and item["duration_ms"] >= 0)
    require(item["metrics"] == metrics(arm, body, item["duration_ms"]))
    require(
        item["metrics"]["model_calls"] <= CAPS["model_calls"]
        and item["metrics"]["tool_calls"] <= CAPS["tool_calls"]
    )


def load(session, principal):
    if not REPORT_PATH.exists():
        return {"available": False, "status": "not_run"}
    try:
        report = read_json(REPORT_PATH)
        tasks = report["tasks"]
        if not tasks:
            return {"available": False, "status": "preparing"}
        require(report["workflow"] == "coordination-comparison.v1" and report["budget"] == CAPS)
        require(digest(tasks) == report["tasks_sha256"])
        require(len(tasks) == len(FAMILIES) and {t["family"] for t in tasks} == set(FAMILIES))
        require(len({t["ticket"]["ticket_id"] for t in tasks}) == len(tasks))
        by_family = {}
        for task in tasks:
            family = task["family"]
            require(family in FAMILIES and family not in by_family)
            require(
                read_ticket(session, principal, task["ticket"]["ticket_id"]).model_dump(mode="json")
                == task["ticket"]
            )
            by_family[family] = task
        assigned = set()
        attempts = report["attempts"]
        for item in attempts:
            assignment = (item["family"], item["arm"])
            require(
                assignment not in assigned
                and item["family"] in by_family
                and item["model"] == report["model"]
            )
            assigned.add(assignment)
            bind(session, principal, item, by_family[item["family"]])
        pairs = []
        if report["checks"] == "attempts_finished":
            require(assigned == {(f, a) for f in FAMILIES for a in ("single", "multi")})
        for family in by_family:
            rows = [a for a in attempts if a["family"] == family]
            if len(rows) == 2:
                pairs.append(
                    {
                        "family": family,
                        **compare(
                            next(a for a in rows if a["arm"] == "single"),
                            next(a for a in rows if a["arm"] == "multi"),
                        ),
                    }
                )
        return {
            "available": True,
            "complete": report["checks"] == "attempts_finished" and len(assigned) == 2 * len(tasks),
            "model": report["model"],
            "budget": CAPS,
            "tasks_sha256": report["tasks_sha256"],
            "pairs": pairs,
            "groups": {
                arm: aggregate([a for a in attempts if a["arm"] == arm])
                for arm in ("single", "multi")
            },
            "records": [
                {
                    "family": a["family"],
                    "arm": a["arm"],
                    "status": a["metrics"]["status"] if a.get("metrics") else a["status"],
                    "path": (
                        f"/api/investigations/{a['response']['investigation_id']}"
                        if a["arm"] == "single"
                        else f"/api/coordination-executions/{a['response']['execution_id']}"
                    )
                    if a.get("response")
                    else None,
                    "stop_reason": (a["response"].get("stop_reason") or a["response"].get("error"))
                    if a.get("response")
                    else a.get("error_code"),
                }
                for a in attempts
            ],
            "protocol_differences": PROTOCOL_DIFFERENCES,
            "model_calls_for_readback": 0,
            "human_semantic_reviewed": False,
            "isolated_topology_comparison": False,
            "limitation": (
                "四类开发模板每臂一次；同总预算的完整流程比较，协议差异未消融。"
                "费用未对账，模型评审非人工准确率，未证明显著收益。"
            ),
        }
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, StopIteration):
        raise invalid() from None
