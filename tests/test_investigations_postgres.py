"""真实 PostgreSQL 与独立 MCP 进程；模型替身仅验证控制契约。"""

import json

import anyio
import pytest
from mcp.types import Tool
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_experiments_postgres import payload as experiment_payload
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index
from test_ticket_postgres import context as context
from test_ticket_postgres import create_ticket, headers

from supportops.chunks.chunking import digest
from supportops.investigations import mcp_client, runner
from supportops.investigations.contracts import Scope, tool_manifest
from supportops.models.provider import ModelFailure

pytestmark = pytest.mark.integration


@pytest.fixture
def model(monkeypatch):
    calls = []

    class Model:
        repeat = False
        fail = False
        invalid_quote = False
        injection = False

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, body):
            data = json.loads(body["messages"][1]["content"])
            calls.append(data)
            if Model.fail:
                raise ModelFailure("MODEL_TIMEOUT")
            if "tools_used" in data:
                used = data["tools_used"]
                action = (
                    "search_knowledge"
                    if Model.repeat or "search_knowledge" not in used
                    else "read_observations"
                    if data["scope"]["has_experiment"] and "read_observations" not in used
                    else "finish"
                )
                value = {
                    "action": action,
                    "arguments": {"query": "RD_TIMEOUT"} if action == "search_knowledge" else {},
                    "reason": "测试决策",
                }
                if Model.injection:
                    value["arguments"]["organization_id"] = "evil"
            elif "query" in data:
                c = data["contexts"][0]
                value = {
                    "claims": [
                        {
                            "claim_id": "C1",
                            "kind": "fact",
                            "text": "资料记载 RD_TIMEOUT。",
                            "citations": [
                                {
                                    "context_id": c["context_id"],
                                    "evidence_id": c["anchor_evidence_ids"][0],
                                    "quote": "伪造引文"
                                    if Model.invalid_quote
                                    else c["text"].splitlines()[-1],
                                }
                            ],
                        }
                    ],
                    "missing_information": ["需要当前现场日志。"],
                }
            else:
                value = {
                    "items": [
                        {
                            "claim_id": c["claim_id"],
                            "verdict": "supported",
                            "reason": "测试模型支持",
                        }
                        for c in data["claims"]
                    ]
                }
            return {
                "model": self.settings.main_model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            }, 1

    monkeypatch.setattr(runner, "Provider", Model)
    return Model, calls


def setup(context, auth):
    ticket = create_ticket(context, auth)
    record, _ = index(context, auth)
    return ticket, {"index_id": record["index_id"]}


def ask(context, auth, ticket, payload):
    return context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/investigations", headers=auth, json=payload
    )


def test_req1203_real_mcp_historical_observations_and_req1206_readback(context, embedding, model):
    auth = headers(context)
    ticket, request = setup(context, auth)
    exp = (
        context["client"]
        .post("/api/experiments/import", headers=auth, json=experiment_payload())
        .json()
    )
    response = ask(context, auth, ticket, {**request, "experiment_id": exp["experiment_id"]})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed", body
    assert body["tool_calls"] == 3 and body["usage"]["known_model_calls"] == 5
    assert body["mcp"]["transport"] == "stdio" and body["mcp"]["initialization"]["protocolVersion"]
    assert body["report"]["claims"][0]["citations"][0]["literal_verified"]
    logs = [e for e in body["evidence"] if e["kind"] == "log"]
    assert logs and all(e["source"]["event"]["phase"] == "failure" for e in logs)
    assert not body["current_incident_verified"] and body["persisted"]
    path = f"/api/investigations/{body['investigation_id']}"
    assert context["client"].get(path, headers=auth).json() == body
    assert context["client"].get(path, headers=headers(context, "username_b")).status_code == 404
    listing = (
        context["client"]
        .get(f"/api/tickets/{ticket['ticket_id']}/investigations", headers=auth)
        .json()
    )
    assert listing["total"] == 1 and listing["items"][0] == body
    assert (
        context["client"]
        .post(f"/api/tickets/{ticket['ticket_id']}/runs", headers=auth, json={})
        .status_code
        == 201
    )


@pytest.mark.parametrize(
    "change,expected",
    [({"max_tool_calls": 1}, "tool_budget"), ({"max_model_calls": 1}, "model_budget")],
)
def test_req1204_budget_persists_without_partial_report(
    context, embedding, model, change, expected
):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    body = ask(context, auth, ticket, {**payload, **change}).json()
    assert body["status"] == "stopped" and body["stop_reason"] == expected, body
    assert body["report"] is None and body["persisted"]


@pytest.mark.parametrize(
    "fault,expected,unknown",
    [
        ("repeat", "repeated_tool", 0),
        ("fail", "MODEL_TIMEOUT", 1),
        ("invalid_quote", "ANSWER_EVIDENCE_INVALID", 0),
        ("injection", "MODEL_RESPONSE_INVALID", 0),
    ],
)
def test_req1202_failure_and_scope_injection_are_recorded(
    context, embedding, model, fault, expected, unknown
):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    setattr(model[0], fault, True)
    body = ask(context, auth, ticket, payload).json()
    assert body["stop_reason"] == expected, body
    assert body["report"] is None and body["usage"]["unknown_model_calls"] == unknown
    assert body["usage"]["known_model_calls"] == len(model[1]) - unknown


def test_req1201_auth_version_and_other_organization_stop_before_model(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    assert ask(context, {}, ticket, payload).status_code == 401
    assert ask(context, headers(context, "username_b"), ticket, payload).status_code == 404
    unknown = create_ticket(context, auth, version=None)
    body = ask(context, auth, unknown, payload).json()
    assert (
        body["stop_reason"] == "version_required"
        and body["model_calls"] == 0
        and body["mcp"] is None
    )
    exp = (
        context["client"]
        .post("/api/experiments/import", headers=auth, json=experiment_payload())
        .json()
    )
    other_version = create_ticket(context, auth, version="2.0")
    assert (
        ask(
            context, auth, other_version, {**payload, "experiment_id": exp["experiment_id"]}
        ).status_code
        == 422
    )
    assert not model[1]


def test_req1203_real_mcp_rejects_tool_and_params_and_revoked_session(context, embedding):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    me = context["client"].get("/api/auth/me", headers=auth).json()
    with context["admin"].connect() as c:
        sid = c.scalar(
            text(
                "SELECT id FROM auth_sessions WHERE user_id=:id AND revoked_at IS NULL "
                "ORDER BY expires_at DESC LIMIT 1"
            ),
            {"id": me["user_id"]},
        )
        corpus = c.scalar(
            text("SELECT corpus_sha256 FROM retrieval_indexes WHERE id=:id"),
            {"id": payload["index_id"]},
        )
    scope = Scope(
        organization_id=me["organization_id"],
        user_id=me["user_id"],
        session_id=sid,
        ticket_id=ticket["ticket_id"],
        index_id=payload["index_id"],
        product_version="1.1",
        experiment_id=None,
        corpus_sha256=corpus,
        ticket_sha256=digest(ticket),
    )

    async def check():
        async with mcp_client.connect(scope, context["app_url"]) as client:
            assert (await client.call("get_ticket", {}, 10))["ticket"] == ticket
            denied = await client.client.call_tool("shell", {"command": "whoami"})
            assert denied.isError
            denied = await client.client.call_tool(
                "search_knowledge", {"query": "RD_TIMEOUT", "product_version": "2.0"}
            )
            assert denied.isError and denied.content[0].text == "TOOL_ARGUMENTS_INVALID"
            context["client"].post("/api/auth/logout", headers=auth)
            denied = await client.client.call_tool("get_ticket", {})
            assert denied.isError and denied.content[0].text == "AUTHENTICATION_REQUIRED"

    anyio.run(check)


def test_req1206_rls_no_update_and_tamper_detection(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    body = ask(context, auth, ticket, payload).json()
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM investigations")) == 0
        with pytest.raises(DBAPIError), engine.begin() as c:
            c.execute(text("UPDATE investigations SET status='completed'"))
        with context["admin"].begin() as c:
            c.execute(
                text("UPDATE investigations SET output_sha256=:digest WHERE id=:id"),
                {"digest": "a" * 64, "id": body["investigation_id"]},
            )
        assert (
            context["client"]
            .get(f"/api/investigations/{body['investigation_id']}", headers=auth)
            .status_code
            == 409
        )
    finally:
        engine.dispose()


def test_req1203_tool_schema_drift_rejected():
    tools = [Tool.model_validate(t) for t in tool_manifest()]
    mcp_client.check_manifest(tools)
    tools[1].inputSchema["properties"]["organization_id"] = {"type": "string"}
    with pytest.raises(mcp_client.MCPFailure):
        mcp_client.check_manifest(tools)


def test_req1204_late_model_result_retains_usage_and_stops(context, embedding, model, monkeypatch):
    from supportops.investigations.guard import Gate

    now = [0.0]

    class ClockGate(Gate):
        def __init__(self, tools, models, budget):
            super().__init__(tools, models, budget, clock=lambda: now[0])

    original = model[0].post

    def late(self, url, body):
        response = original(self, url, body)
        now[0] = 2.0
        return response

    monkeypatch.setattr(runner, "Gate", ClockGate)
    monkeypatch.setattr(model[0], "post", late)
    auth = headers(context)
    ticket, payload = setup(context, auth)
    body = ask(context, auth, ticket, {**payload, "time_budget_ms": 1000}).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "time_budget", body
    assert body["usage"]["known_model_calls"] == 1 and body["report"] is None
    assert body["tool_calls"] == 1
