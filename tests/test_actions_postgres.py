"""真实 PostgreSQL 检查点 / 批准 / 隔离，模型和实验效果替身分开计。"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_ticket_postgres import context as context
from test_ticket_postgres import create_ticket, headers

from supportops.actions import service
from supportops.chunks.chunking import digest
from supportops.investigations.hypothesis_contracts import LabRegistration
from supportops.investigations.live_service import register_lab_run
from supportops.investigations.live_sources import capture
from supportops.investigations.models import Investigation
from supportops.models.provider import ModelFailure

pytestmark = pytest.mark.integration


@pytest.fixture
def prepared(context, monkeypatch):
    auth = headers(context)
    ticket = create_ticket(context, auth)
    started = datetime.now(timezone.utc) - timedelta(seconds=2)
    binding = LabRegistration(
        run_id=uuid4(),
        instance_id=uuid4(),
        receiver_instance_id=uuid4(),
        product_version="1.1",
        request_ids=[uuid4()],
        started_at=started,
        expires_at=started + timedelta(minutes=10),
        mode="online",
    )
    identity = uuid4()

    def response(request):
        if request.url.path == "/diagnostics/state":
            return httpx.Response(
                200,
                json=(
                    {
                        "service": "relaydesk",
                        "instance_id": str(binding.instance_id),
                        "config": {"product_version": "1.1", "delivery_timeout_ms": 100},
                        "checked_out": 0,
                    }
                    if request.url.port == 8101
                    else {
                        "service": "receiver",
                        "instance_id": str(binding.receiver_instance_id),
                        "active_target": "current",
                        "delay_ms": 350,
                    }
                ),
            )
        return httpx.Response(
            200,
            json=[
                {
                    "request_id": str(binding.request_ids[0]),
                    "observed_at": started.isoformat(),
                    "phase": "failure",
                    "service": "relaydesk",
                    "product_version": "1.1",
                    "event": "request_finished",
                    "status": 504,
                    "error_code": "RD_TIMEOUT",
                    "elapsed_ms": 102.1,
                }
            ]
            if request.url.port == 8101
            else [],
        )

    with Session(context["admin"]) as session, session.begin():
        registration = register_lab_run(session, context["organization_a"], binding)
        lab_id = registration.id
        value = capture(
            binding,
            lab_id,
            identity,
            "read_current_observations",
            httpx.Client(transport=httpx.MockTransport(response)),
        )
        snapshot = {"request": {"lab_run_id": str(lab_id)}, "ticket": ticket}
        output = {"status": "completed", "tool_results": [{"result": value}], "events": []}
        session.add(
            Investigation(
                id=identity,
                organization_id=context["organization_a"],
                requester_id=context["user_a"],
                ticket_id=UUID(ticket["ticket_id"]),
                workflow_version="hypothesis-investigation.v1",
                status="completed",
                input_snapshot=snapshot,
                input_sha256=digest(snapshot),
                output=output,
                output_sha256=digest(output),
            )
        )
    monkeypatch.setattr(service, "capture", lambda *args: value)
    monkeypatch.setattr(
        service,
        "before_state",
        lambda *args: {
            "instance_id": str(binding.instance_id),
            "config": {"product_version": "1.1"},
            "checked_out": 0,
        },
    )

    class Model:
        fault = False

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, body):
            if self.fault is True:
                raise ModelFailure("MODEL_TIMEOUT")
            data = json.loads(body["messages"][1]["content"])
            choice = {
                "action": "set_timeout",
                "timeout_ms": 800,
                "reason": "本次超时，建议有限实验验证",
                "evidence_ids": [data["current_evidence"][0]["evidence_id"]],
            }
            if self.fault == "citation":
                choice["evidence_ids"] = ["live:foreign"]
            return {
                "model": self.settings.main_model,
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(choice)}}],
            }, 1

    monkeypatch.setattr(service, "Provider", Model)
    effects = []
    receipts = {}

    def remote(command, **options):
        if command.action_id in receipts:
            return receipts[command.action_id]
        effects.append(command.operation)
        result = {
            "operation": command.operation,
            "after": {
                "instance_id": str(command.instance_id),
                "config": {"product_version": "1.1"},
                "checked_out": 0,
            },
        }
        if command.operation == "retest":
            result |= {
                "response": {"status": 200},
                "observations": [
                    {
                        "request_id": str(command.request_id),
                        "phase": "retest",
                        "event": "request_finished",
                        "status": 200,
                    }
                ],
            }
        receipt = {
            "action_id": str(command.action_id),
            "command_sha256": digest(command.model_dump(mode="json")),
            "result": result,
        }
        receipt["sha256"] = digest(receipt)
        receipts[command.action_id] = receipt
        return receipt

    monkeypatch.setattr(service, "remote", remote)
    return {
        "auth": auth,
        "ticket": ticket,
        "investigation": str(identity),
        "effects": effects,
        "Model": Model,
        "remote": remote,
        "receipts": receipts,
    }


def proposal(context, prepared, key=None):
    return context["client"].post(
        f"/api/investigations/{prepared['investigation']}/action-proposals",
        headers=prepared["auth"],
        json={"request_id": str(key or uuid4())},
    )


def decision(context, prepared, body, value="approve"):
    return context["client"].post(
        f"/api/actions/{body['job_id']}/decision",
        headers=prepared["auth"],
        json={"decision": value, "proposal_sha256": body["proposal_sha256"]},
    )


def test_req1402_approval_is_separate_and_request_id_is_idempotent(context, prepared):
    key = uuid4()
    first = proposal(context, prepared, key).json()
    second = proposal(context, prepared, key).json()
    assert first["job_id"] == second["job_id"]
    assert first["status"] == "pending"
    assert prepared["effects"] == []
    client = context["client"]
    path = f"/api/actions/{first['job_id']}"
    assert client.post(path + "/advance", headers=prepared["auth"]).status_code == 409
    bad = client.post(
        path + "/decision",
        headers=prepared["auth"],
        json={"decision": "approve", "proposal_sha256": "0" * 64},
    )
    assert bad.json()["error"]["code"] == "ACTION_PROPOSAL_CHANGED"
    assert decision(context, prepared, first).json()["status"] == "approved"
    assert decision(context, prepared, first).json()["status"] == "approved"
    assert prepared["effects"] == []
    assert (
        client.post(path + "/advance", headers=prepared["auth"]).json()["status"] == "action_done"
    )
    final = client.post(path + "/advance", headers=prepared["auth"]).json()
    assert final["status"] == "completed" and final["checkpoint"]["retest_passed"]
    assert client.post(path + "/advance", headers=prepared["auth"]).json() == final
    assert prepared["effects"] == ["set_timeout", "retest"]


def test_req1404_lost_http_after_effect_resumes_same_receipt(context, prepared, monkeypatch):
    body = proposal(context, prepared).json()
    decision(context, prepared, body)
    remote = prepared["remote"]

    def lost(command, **options):
        remote(command)
        raise httpx.ReadTimeout("模拟回执丢失")

    monkeypatch.setattr(service, "remote", lost)
    path = f"/api/actions/{body['job_id']}/advance"
    result = context["client"].post(path, headers=prepared["auth"]).json()
    assert result["status"] == "action_running"
    assert result["checkpoint"]["error"] == "ACTION_TRANSPORT_INTERRUPTED"
    monkeypatch.setattr(service, "remote", remote)
    result = context["client"].post(path, headers=prepared["auth"]).json()
    assert result["status"] == "action_done"
    assert prepared["effects"] == ["set_timeout"]


def test_req1405_cancel_prevents_retest_and_reject_never_executes(context, prepared):
    body = proposal(context, prepared).json()
    assert decision(context, prepared, body, "reject").json()["status"] == "rejected"
    path = f"/api/actions/{body['job_id']}"
    assert (
        context["client"].post(path + "/advance", headers=prepared["auth"]).json()["status"]
        == "rejected"
    )
    body = proposal(context, prepared).json()
    decision(context, prepared, body)
    path = f"/api/actions/{body['job_id']}"
    context["client"].post(path + "/advance", headers=prepared["auth"])
    assert (
        context["client"].post(path + "/cancel", headers=prepared["auth"]).json()["status"]
        == "cancelled"
    )
    context["client"].post(path + "/advance", headers=prepared["auth"])
    assert prepared["effects"] == ["set_timeout"]


def test_req1406_sse_replay_and_org_scope(context, prepared):
    body = proposal(context, prepared).json()
    decision(context, prepared, body, "reject")
    path = f"/api/actions/{body['job_id']}"
    client = context["client"]
    stream = client.get(path + "/events", headers=prepared["auth"])
    assert stream.status_code == 200
    assert "id: 1" in stream.text and "human_reject" in stream.text
    replay = client.get(path + "/events", headers=prepared["auth"] | {"Last-Event-ID": "2"})
    assert "id: 1\n" not in replay.text and "id: 3\n" in replay.text
    foreign = headers(context, "username_b")
    for endpoint in (path, path + "/events"):
        assert client.get(endpoint, headers=foreign).status_code == 404
        assert client.get(endpoint).status_code == 401
    for operation in ("advance", "cancel", "decision"):
        options = (
            {"json": {"decision": "approve", "proposal_sha256": body["proposal_sha256"]}}
            if operation == "decision"
            else {}
        )
        assert client.post(path + "/" + operation, headers=foreign, **options).status_code == 404
    assert prepared["effects"] == []


def test_req1401_model_failure_is_saved_with_unknown_usage(context, prepared):
    prepared["Model"].fault = True
    body = proposal(context, prepared).json()
    assert body["status"] == "failed"
    assert body["checkpoint"]["error"] == "MODEL_TIMEOUT"
    assert body["checkpoint"]["usage"]["unknown_model_calls"] == 1


def test_req1402_expired_and_revoked_approval_cannot_execute(context, prepared):
    body = proposal(context, prepared).json()
    decision(context, prepared, body)
    context["client"].post("/api/auth/logout", headers=prepared["auth"])
    new_auth = headers(context)
    result = context["client"].post(f"/api/actions/{body['job_id']}/advance", headers=new_auth)
    assert result.status_code == 401
    assert prepared["effects"] == []
    body = proposal(context, prepared | {"auth": new_auth}).json()
    with context["admin"].begin() as connection:
        connection.execute(
            text("UPDATE action_jobs SET expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": body["job_id"]},
        )
    result = decision(context, prepared | {"auth": new_auth}, body)
    assert result.json()["error"]["code"] == "ACTION_APPROVAL_INVALID"


def test_req1405_concurrent_advance_and_cancel_preserve_already_sent_receipt(
    context, prepared, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    body = proposal(context, prepared).json()
    decision(context, prepared, body)
    entered, release = Event(), Event()

    def blocked(command, **options):
        entered.set()
        assert release.wait(5)
        return prepared["remote"](command)

    monkeypatch.setattr(service, "remote", blocked)
    path = f"/api/actions/{body['job_id']}"
    client = context["client"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, path + "/advance", headers=prepared["auth"])
        assert entered.wait(5)
        duplicate = client.post(path + "/advance", headers=prepared["auth"])
        assert duplicate.json()["error"]["code"] == "ACTION_BUSY"
        cancelled = client.post(path + "/cancel", headers=prepared["auth"]).json()
        assert cancelled["status"] == "cancel_requested"
        release.set()
        result = future.result().json()
    assert result["status"] == "cancelled"
    assert result["checkpoint"]["action_receipt"]
    assert prepared["effects"] == ["set_timeout"]
    assert client.post(path + "/advance", headers=prepared["auth"]).json()["status"] == "cancelled"


def test_req1404_unknown_experiment_effect_is_terminal(context, prepared, monkeypatch):
    body = proposal(context, prepared).json()
    decision(context, prepared, body)

    def unknown(*args, **options):
        raise service.fail("ACTION_UNCERTAIN", "真实执行结果未知")

    monkeypatch.setattr(service, "remote", unknown)
    path = f"/api/actions/{body['job_id']}/advance"
    result = context["client"].post(path, headers=prepared["auth"]).json()
    assert result["status"] == "uncertain"
    assert context["client"].post(path, headers=prepared["auth"]).json() == result
    assert prepared["effects"] == []


def test_req1406_session_revocation_terminates_open_sse(context, prepared, monkeypatch):
    body = proposal(context, prepared).json()
    authenticate = service.authenticate
    checks = []

    def revoke_after_read(session, principal, session_id=None):
        authenticate(session, principal, session_id)
        checks.append(1)
        if len(checks) == 2:
            with context["admin"].begin() as connection:
                connection.execute(
                    text("UPDATE auth_sessions SET revoked_at=now() WHERE id=:id"),
                    {"id": str(principal.session_id)},
                )

    monkeypatch.setattr(service, "authenticate", revoke_after_read)
    result = context["client"].get(
        f"/api/actions/{body['job_id']}/events", headers=prepared["auth"]
    )
    assert "event: stream_error" in result.text
    assert "AUTHENTICATION_REQUIRED" in result.text
    assert prepared["effects"] == []


def test_req1401_foreign_citation_stops_with_known_usage(context, prepared):
    prepared["Model"].fault = "citation"
    body = proposal(context, prepared).json()
    assert body["status"] == "failed"
    assert body["checkpoint"]["error"] == "MODEL_RESPONSE_INVALID"
    assert body["checkpoint"]["usage"]["known_model_calls"] == 1
    assert prepared["effects"] == []


def test_req1402_database_freezes_proposal_and_approval(context, prepared):
    from sqlalchemy.exc import DBAPIError

    body = proposal(context, prepared).json()
    decision(context, prepared, body)
    with pytest.raises(DBAPIError):
        with context["admin"].begin() as connection:
            connection.execute(
                text("UPDATE action_jobs SET proposal='{}'::jsonb WHERE id=:id"),
                {"id": body["job_id"]},
            )
    from sqlalchemy import create_engine

    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE action_approvals SET decision='reject' WHERE job_id=:id"),
                    {"id": body["job_id"]},
                )
    finally:
        engine.dispose()


def test_req1403_failed_retest_does_not_claim_repair(context, prepared, monkeypatch):
    body = proposal(context, prepared).json()
    decision(context, prepared, body)

    def retest_fails(command, **options):
        receipt = prepared["remote"](command)
        if command.operation == "retest":
            receipt["result"]["response"]["status"] = 503
            receipt["result"]["observations"][0]["status"] = 503
            receipt["sha256"] = digest({k: v for k, v in receipt.items() if k != "sha256"})
        return receipt

    monkeypatch.setattr(service, "remote", retest_fails)
    path = f"/api/actions/{body['job_id']}/advance"
    context["client"].post(path, headers=prepared["auth"])
    final = context["client"].post(path, headers=prepared["auth"]).json()
    assert final["status"] == "completed"
    assert final["checkpoint"]["retest_passed"] is False
    assert final["checkpoint"]["current_incident_verified"] is False
    assert final["checkpoint"]["human_semantic_reviewed"] is False
