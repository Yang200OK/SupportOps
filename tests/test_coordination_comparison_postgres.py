"""只读比较必须绑定真实 PostgreSQL 两条流程，不运行付费模型。"""

import json
from copy import deepcopy

import pytest
import test_coordination_execution_postgres as coordination_helpers
import test_hypothesis_postgres as hypothesis_helpers
from sqlalchemy.orm import Session
from test_coordination_execution_postgres import advance, start
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.chunks.chunking import digest
from supportops.coordination.comparison import initial_state, metrics
from supportops.coordination.models import Board

pytestmark = pytest.mark.integration


@pytest.fixture
def coordination_model(monkeypatch):
    return coordination_helpers.model.__wrapped__(monkeypatch)


@pytest.fixture
def single_model(monkeypatch):
    return hypothesis_helpers.model.__wrapped__(monkeypatch)


@pytest.fixture(autouse=True)
def measured_coordination(coordination_model, monkeypatch):
    from supportops.coordination import executor

    original = executor.chat_json

    def measured(provider, *args):
        result, usage = original(provider, *args)
        return result, {
            **usage,
            "requested_model": provider.settings.main_model,
            "returned_model": provider.settings.main_model,
        }

    monkeypatch.setattr(executor, "chat_json", measured)


def prepare(context, tmp_path):
    auth, board, _, _, execution = start(context)
    with Session(context["admin"]) as session:
        snapshot = deepcopy(session.get(Board, board["board_id"]).input_snapshot)
    execution = advance(context, auth, execution)
    execution = advance(context, auth, execution)
    ticket = snapshot["ticket"]
    request = {
        k: snapshot["request"][k]
        for k in ("index_id", "lab_run_id", "max_tool_calls", "max_model_calls", "time_budget_ms")
    }
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations", headers=auth, json=request
    )
    assert response.status_code == 201
    single = response.json()
    assert single["status"] == execution["status"] == "completed"
    lab = {"registration": snapshot["lab_registration"], "lab_run_id": request["lab_run_id"]}
    path = tmp_path / "pairs.json"
    prepared = path.with_name("prepare.json")
    prepared.write_text(json.dumps(lab), encoding="utf-8")
    state = initial_state(lab)
    tasks = [{"family": "configuration", "ticket": ticket}]
    budget = execution["budget"]
    model = single["usage"]["calls"][0]["requested_model"]
    value = {
        "workflow": "coordination-comparison.v1",
        "model": model,
        "budget": budget,
        "tasks": tasks,
        "tasks_sha256": digest(tasks),
        "checks": "attempts_finished",
        "attempts": [],
    }
    for arm, body in (("single", single), ("multi", execution)):
        value["attempts"].append(
            {
                "family": "configuration",
                "arm": arm,
                "task_sha256": digest(ticket),
                "model": model,
                "index_sha256": snapshot["knowledge"]["corpus_sha256"],
                "budget": budget,
                "initial_state": state,
                "initial_state_sha256": digest(state),
                "prepared_path": str(prepared),
                "prepared_sha256": digest(lab),
                "lab_run_id": request["lab_run_id"],
                "response": body,
                "duration_ms": 50,
                "metrics": metrics(arm, body, 50),
                "status": "completed",
            }
        )
    path.write_text(json.dumps(value), encoding="utf-8")
    return auth, path, value


def test_req2004_bound_report_readback_and_org_scope(
    context, embedding, coordination_model, single_model, tmp_path, monkeypatch
):
    auth, path, value = prepare(context, tmp_path)
    import supportops.coordination.comparison_reports as reports

    monkeypatch.setattr(reports, "REPORT_PATH", path)
    monkeypatch.setattr(reports, "FAMILIES", ("configuration",))
    response = context["client"].get("/api/coordination-comparison", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["pairs"][0]["same_total_budget_conditions"]
    assert response.json()["model_calls_for_readback"] == 0
    assert response.json()["groups"]["single"]["current_evidence_attempts"] == 1
    assert response.json()["groups"]["multi"]["current_evidence_attempts"] == 1
    assert (
        context["client"]
        .get("/api/coordination-comparison", headers=headers(context, "username_b"))
        .status_code
        == 404
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "response",
        "metrics",
        "budget",
        "duplicate",
        "state",
        "prepared",
        "model",
        "cohort",
        "missing_attempt",
    ],
)
def test_req2004_tampering_rejected(
    context, embedding, coordination_model, single_model, tmp_path, monkeypatch, tamper
):
    auth, path, value = prepare(context, tmp_path)
    import supportops.coordination.comparison_reports as reports

    monkeypatch.setattr(reports, "REPORT_PATH", path)
    if tamper != "cohort":
        monkeypatch.setattr(reports, "FAMILIES", ("configuration",))
    item = value["attempts"][0]
    if tamper == "response":
        item["response"]["status"] = "failed"
    elif tamper == "metrics":
        item["metrics"]["input_tokens"] = 0
    elif tamper == "budget":
        item["budget"]["model_calls"] = 24
    elif tamper == "duplicate":
        value["attempts"].append(deepcopy(item))
    elif tamper == "state":
        item["initial_state"]["exit_code"] = 0
        item["initial_state_sha256"] = digest(item["initial_state"])
    elif tamper == "prepared":
        item["prepared_path"] = str(tmp_path / "outside.json")
    elif tamper == "missing_attempt":
        value["attempts"].pop()
    elif tamper == "cohort":
        pass
    else:
        item["model"] = "other-model"
    path.write_text(json.dumps(value), encoding="utf-8")
    response = context["client"].get("/api/coordination-comparison", headers=auth)
    assert response.status_code == 409
