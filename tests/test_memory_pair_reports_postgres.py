"""成对报告页面读取必须重新绑定当前组织的持久化调查。"""

import json

import pytest
from test_hypothesis_postgres import ask
from test_hypothesis_postgres import model as model
from test_memory_postgres import prepare, retrospective
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.chunks.chunking import digest

pytestmark = pytest.mark.integration


def report_file(context, embedding, model, tmp_path):
    auth, ticket, request, off = prepare(context, embedding, model)
    retrospective(context, auth, off)
    on = ask(context, auth, ticket, {**request, "use_memory": True}).json()
    budget = {
        k: off["input_snapshot"]["request"][k]
        for k in ("max_model_calls", "max_tool_calls", "time_budget_ms")
    }
    task = {"family_for_harness_only": "configuration", "ticket": ticket}
    attempts = [
        {
            "family_for_harness_only": "configuration",
            "use_memory": enabled,
            "task_sha256": digest(ticket),
            "index_sha256": body["input_snapshot"]["knowledge"]["corpus_sha256"],
            "model": body["usage"]["calls"][0]["requested_model"],
            "budget": budget,
            "initial_state": {"config": "same"},
            "initial_state_sha256": digest({"config": "same"}),
            "memory_sha256": digest(on["input_snapshot"]["memory"]) if enabled else None,
            "response": body,
        }
        for enabled, body in ((False, off), (True, on))
    ]
    path = tmp_path / "pair-report.json"
    value = {
        "mode": "pairs",
        "tasks": [task],
        "tasks_sha256": digest([task]),
        "attempts": attempts,
        "checks": "passed",
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return auth, path, value


def test_req1706_report_is_bound_and_org_scoped(context, embedding, model, tmp_path, monkeypatch):
    auth, path, value = report_file(context, embedding, model, tmp_path)
    import supportops.skills.pair_reports as reports

    monkeypatch.setattr(reports, "REPORT_PATH", path)
    response = context["client"].get("/api/memory-pairs", headers=auth)
    assert response.status_code == 200 and response.json()["pairs"][0]["comparable"]
    assert response.json()["groups"]["off"]["attempted"] == 1
    assert (
        context["client"]
        .get("/api/memory-pairs", headers=headers(context, "username_b"))
        .status_code
        == 404
    )


def test_req1706_report_tampering_not_accepted(context, embedding, model, tmp_path, monkeypatch):
    auth, path, value = report_file(context, embedding, model, tmp_path)
    import supportops.skills.pair_reports as reports

    monkeypatch.setattr(reports, "REPORT_PATH", path)
    value["attempts"][0]["response"]["status"] = "failed"
    path.write_text(json.dumps(value), encoding="utf-8")
    response = context["client"].get("/api/memory-pairs", headers=auth)
    assert response.status_code == 409
