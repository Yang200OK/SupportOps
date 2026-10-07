"""固定本轮报告只读展示；数据重新绑定数据库，不执行文件中的指令。"""

import json

from fastapi.encoders import jsonable_encoder

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.investigations.service import read
from supportops.settings import ROOT
from supportops.skills.paired import aggregate, compare
from supportops.tickets.service import read_ticket

REPORT_PATH = ROOT / "docs/verification/phase-6-round-3/pairs-qwen-plus-0728-attempt1.json"
MAX_REPORT_PATH = ROOT / "docs/verification/phase-6-round-3/pairs-qwen-max-attempt1.json"
LEGACY_REPORT_PATH = ROOT / "docs/verification/phase-6-round-3/pairs-attempt1.json"


def invalid():
    return ServiceError(409, "MEMORY_PAIR_REPORT_INVALID", "成对报告与任务或持久化调查不一致。")


def load(session, principal, cohort="current"):
    path = {"current": REPORT_PATH, "max": MAX_REPORT_PATH, "legacy": LEGACY_REPORT_PATH}[cohort]
    if not path.exists():
        return {"available": False, "status": "not_run"}
    try:
        with path.open("rb") as source:
            raw = source.read(4000001)
        if len(raw) > 4000000:
            raise invalid()
        report = json.loads(raw.decode("utf-8"))
        tasks = report.get("tasks", [])
        if not tasks:
            return {"available": False, "status": "preparing"}
        if report["mode"] != "pairs" or digest(tasks) != report["tasks_sha256"]:
            raise invalid()
        by_family = {}
        for task in tasks:
            family = task["family_for_harness_only"]
            if (
                family not in ("configuration", "pool", "cache", "downstream")
                or family in by_family
            ):
                raise invalid()
            ticket = read_ticket(session, principal, task["ticket"]["ticket_id"])
            if ticket.model_dump(mode="json") != task["ticket"]:
                raise invalid()
            by_family[family] = task
        attempts = report.get("attempts", [])
        assigned = set()
        for attempt in attempts:
            family, enabled = attempt["family_for_harness_only"], attempt["use_memory"]
            if (
                (family, enabled) in assigned
                or family not in by_family
                or type(enabled) is not bool
            ):
                raise invalid()
            assigned.add((family, enabled))
            if attempt["task_sha256"] != digest(by_family[family]["ticket"]):
                raise invalid()
            if (
                "initial_state" in attempt
                and digest(attempt["initial_state"]) != attempt["initial_state_sha256"]
            ):
                raise invalid()
            body = attempt.get("response")
            if body is None:
                continue
            original = jsonable_encoder(read(session, principal, body["investigation_id"]))
            if original != body:
                raise invalid()
            snapshot = original["input_snapshot"]
            request = snapshot["request"]
            if (
                snapshot["ticket"] != by_family[family]["ticket"]
                or snapshot["knowledge"]["corpus_sha256"] != attempt["index_sha256"]
                or request.get("use_memory", False) != enabled
                or request.get("use_skills", False)
                or request.get("use_published_skills", False)
                or any(request[k] != v for k, v in attempt["budget"].items())
                or any(c["requested_model"] != attempt["model"] for c in body["usage"]["calls"])
                or (enabled and digest(snapshot["memory"]) != attempt["memory_sha256"])
            ):
                raise invalid()
        pairs = []
        for family in by_family:
            rows = [a for a in attempts if a["family_for_harness_only"] == family]
            if len(rows) == 2:
                pairs.append(
                    {
                        "family_for_harness_only": family,
                        **compare(
                            next(a for a in rows if not a["use_memory"]),
                            next(a for a in rows if a["use_memory"]),
                        ),
                    }
                )
        return {
            "available": True,
            "complete": report.get("checks") == "passed",
            "tasks_sha256": report["tasks_sha256"],
            "pairs": pairs,
            "groups": {
                label: aggregate([a for a in attempts if a["use_memory"] == enabled])
                for label, enabled in (("off", False), ("on", True))
            },
            "investigations": [
                {
                    "family": a["family_for_harness_only"],
                    "use_memory": a["use_memory"],
                    "investigation_id": a["response"]["investigation_id"]
                    if a.get("response")
                    else None,
                }
                for a in attempts
            ],
            "model_calls_for_readback": 0,
            "human_semantic_reviewed": False,
            "model": report.get("model"),
            "limitation": "固定开发模板，每组每场景一次；非保留集、人工准确率或显著收益。",
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise invalid() from None
