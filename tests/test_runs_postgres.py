"""运行记录使用真实数据库验证共享、隔离、快照与权限。"""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_ticket_postgres import context as context
from test_ticket_postgres import create_ticket, headers

pytestmark = pytest.mark.integration


def start(context, auth, version="1.1"):
    ticket = create_ticket(context, auth, version)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/runs", headers=auth, json={"kind": "intake_check"}
    )
    assert response.status_code == 201
    return ticket, response.json()


def test_req202_run_snapshot_events_and_unknown_model_usage_are_persisted(context):
    auth = headers(context)
    ticket, run = start(context, auth)
    assert run["kind"] == "intake_check" and run["status"] == "succeeded"
    assert run["input_snapshot"]["ticket"]["ticket_id"] == ticket["ticket_id"]
    canonical = json.dumps(
        run["input_snapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert run["input_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert [item["event"] for item in run["events"]] == [
        "run_created",
        "input_checked",
        "run_finished",
    ]
    assert run["model"] is None and run["usage"] is None and run["cost_cny"] is None
    assert run["duration_ms"] >= 0
    saved = context["client"].get(f"/api/runs/{run['run_id']}", headers=auth)
    assert saved.status_code == 200 and saved.json() == run


def test_req202_missing_version_is_blocked_without_root_cause(context):
    _, run = start(context, headers(context), None)
    assert run["status"] == "blocked"
    assert run["output"]["missing_fields"] == ["product_version"]
    assert "root_cause" not in run["output"]


def test_req203_same_org_shared_cross_org_and_missing_are_identical(context):
    auth = headers(context)
    ticket, run = start(context, auth)
    colleague = headers(context, "colleague")
    other = headers(context, "username_b")
    client = context["client"]
    assert client.get(f"/api/runs/{run['run_id']}", headers=colleague).status_code == 200
    foreign = client.get(f"/api/runs/{run['run_id']}", headers=other)
    missing = client.get(f"/api/runs/{uuid4()}", headers=other)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert client.get("/api/runs", headers=other).json()["total"] == 0
    assert (
        client.get(f"/api/runs?ticket_id={ticket['ticket_id']}", headers=other).status_code == 404
    )
    assert (
        client.post(
            f"/api/tickets/{ticket['ticket_id']}/runs", headers=other, json={"kind": "intake_check"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("field", ["organization_id", "requester_id", "status", "model", "output"])
def test_req204_rejects_forged_run_fields_without_write(context, field):
    auth = headers(context)
    ticket = create_ticket(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/runs",
        headers=auth,
        json={"kind": "intake_check", field: "forged"},
    )
    assert response.status_code == 422
    assert context["client"].get("/api/runs", headers=auth).json()["total"] == 0


def test_req203_run_rls_and_immutable_permissions(context):
    _, run = start(context, headers(context))
    engine = create_engine(context["app_url"], pool_size=1, max_overflow=0, hide_parameters=True)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM runs")) == 0
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(context["organization_a"])},
            )
            assert str(connection.scalar(text("SELECT id FROM runs"))) == run["run_id"]
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM runs")) == 0
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE runs SET status='blocked'"))
    finally:
        engine.dispose()


def test_req203_cross_org_run_reference_is_rejected_by_database(context):
    start(context, headers(context))
    with pytest.raises(DBAPIError):
        with context["admin"].begin() as connection:
            connection.execute(
                text("UPDATE runs SET organization_id=:org WHERE organization_id=:original"),
                {"org": context["organization_b"], "original": context["organization_a"]},
            )


def test_req205_authenticated_evaluation_validation_never_returns_answers(context):
    from test_evaluation_contract import dataset

    auth = headers(context)
    response = context["client"].post("/api/evaluations/validate", json=dataset(), headers=auth)
    assert response.status_code == 200
    assert response.json()["total"] == 2 and response.json()["holdout"] == 1
    assert "expected" not in response.json()
    assert context["client"].get("/api/evaluations/schema", headers=auth).status_code == 200


def test_req209_new_routes_require_authentication(context):
    assert context["client"].get("/api/runs").status_code == 401
    assert context["client"].get("/api/evaluations/schema").status_code == 401


def test_req202_failed_response_rolls_back_run_and_events(context, monkeypatch):
    from supportops.runs import service

    auth = headers(context)
    ticket = create_ticket(context, auth)

    def cannot_serialize(record):
        raise RuntimeError("构造响应序列化失败。")

    monkeypatch.setattr(service, "view", cannot_serialize)
    with pytest.raises(RuntimeError, match="构造响应序列化失败"):
        context["client"].post(
            f"/api/tickets/{ticket['ticket_id']}/runs",
            headers=auth,
            json={"kind": "intake_check"},
        )
    assert context["client"].get("/api/runs", headers=auth).json()["total"] == 0


def test_req203_runs_pagination_and_ticket_filter_are_stable(context):
    auth = headers(context)
    ticket, first = start(context, auth)
    _, second = start(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/runs", headers=auth, json={"kind": "intake_check"}
    )
    third = response.json()
    client = context["client"]
    page_one = client.get("/api/runs?limit=2", headers=auth).json()
    page_two = client.get("/api/runs?offset=2&limit=2", headers=auth).json()
    assert page_one["total"] == page_two["total"] == 3
    assert [item["run_id"] for item in page_one["items"]] == [third["run_id"], second["run_id"]]
    assert [item["run_id"] for item in page_two["items"]] == [first["run_id"]]
    filtered = client.get(f"/api/runs?ticket_id={ticket['ticket_id']}", headers=auth).json()
    assert filtered["total"] == 2
    assert all(item["ticket_id"] == ticket["ticket_id"] for item in filtered["items"])
    assert client.get("/api/runs?limit=0", headers=auth).status_code == 422
