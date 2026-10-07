"""真实数据库 / 独立 MCP 验证；模型替身只验证应用控制契约。"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import anyio
import pytest
from mcp.types import Tool
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index
from test_ticket_postgres import context as context
from test_ticket_postgres import create_ticket, headers

from supportops.chunks.chunking import digest
from supportops.investigations import graph, live_client
from supportops.investigations.hypothesis_contracts import LabRegistration, LiveScope, live_manifest
from supportops.investigations.live_service import register_lab_run
from supportops.models.provider import ModelFailure

pytestmark = pytest.mark.integration


def setup(context, auth):
    ticket = create_ticket(context, auth)
    knowledge, _ = index(context, auth)
    now = datetime.now(timezone.utc)
    request_id = uuid4()
    registration = LabRegistration(
        run_id=uuid4(),
        instance_id=uuid4(),
        receiver_instance_id=None,
        product_version="1.1",
        request_ids=[request_id],
        started_at=now - timedelta(seconds=2),
        expires_at=now + timedelta(minutes=8),
        mode="startup",
        boot={
            "exit_code": 2,
            "observation": {
                "request_id": request_id,
                "observed_at": now,
                "phase": "failure",
                "service": "boot",
                "product_version": "1.1",
                "event": "config_checked",
                "status": 422,
                "error_code": "RD_CONFIG_INVALID",
                "elapsed_ms": 1.5,
                "invalid_keys": ["downstream_timeout_ms"],
            },
        },
    )
    with Session(context["admin"]) as session, session.begin():
        registered = register_lab_run(session, context["organization_a"], registration)
        identity = str(registered.id)
    return ticket, {"index_id": knowledge["index_id"], "lab_run_id": identity}


@pytest.fixture
def model(monkeypatch):
    calls = []

    class Model:
        fault = None

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, body):
            data = json.loads(body["messages"][1]["content"])
            calls.append(data)
            if Model.fault == "timeout":
                raise ModelFailure("MODEL_TIMEOUT")
            if data.get("phase") == "plan":
                steps = [
                    {
                        "step_id": "S1",
                        "hypothesis_ids": ["H1"],
                        "reason": "读取版本规则",
                        "tool": "search_knowledge",
                        "arguments": {"query": "RD_TIMEOUT"},
                    },
                    {
                        "step_id": "S2",
                        "hypothesis_ids": ["H1"],
                        "reason": "检查本次启动",
                        "tool": "read_startup_diagnostic",
                        "arguments": {},
                    },
                ]
                done = data["completed_step_ids"]
                current = [
                    e
                    for e in data["evidence"]
                    if e["source"].get("source_type") == "current_startup_diagnostic"
                ]
                refs = (
                    [
                        {
                            "evidence_id": current[0]["evidence_id"],
                            "quote_index": 0,
                        }
                    ]
                    if current
                    else []
                )
                h = {
                    "hypothesis_id": "H1",
                    "cause": "版本配置键不匹配",
                    "basis": "工单现象待核对",
                    "support_signal": "启动校验拒绝版本错误键",
                    "refute_signal": "启动成功且有效键符合版本",
                    "missing_information": ["未做修复后验证"],
                    "status": "supported" if refs else "proposed",
                    "reason": "本次启动校验报告配置错误" if refs else "等待本次证据",
                    "support_citations": refs,
                    "refute_citations": [],
                }
                next_step = (
                    "S1"
                    if "S1" not in done or Model.fault == "repeat"
                    else "S2"
                    if "S2" not in done and Model.fault != "skip_live"
                    else None
                )
                value = {
                    "hypotheses": [h],
                    "steps": steps
                    if Model.fault == "repeat"
                    else [s for s in steps if s["step_id"] not in done],
                    "action": "execute" if next_step else "finish",
                    "next_step_id": next_step,
                    "reason": "测试侧建议",
                }
                if Model.fault == "scope":
                    value["steps"][0]["arguments"]["run_id"] = str(uuid4())
                if Model.fault == "mode":
                    value["steps"][1]["tool"] = "read_current_observations"
                if Model.fault == "echo" and data["hypotheses"]:
                    value["hypotheses"] = data["hypotheses"]
                if Model.fault == "quote" and refs:
                    value["hypotheses"][0]["support_citations"][0]["quote_index"] = 12
            elif data.get("phase") == "generate":
                e = data["contexts"][0]
                value = {
                    "claims": [
                        {
                            "claim_id": "C1",
                            "kind": "fact",
                            "text": "资料提供版本规则",
                            "citations": [
                                {
                                    "evidence_id": e["anchor_evidence_ids"][0],
                                    "quote_index": 0,
                                }
                            ],
                        }
                    ],
                    "missing_information": ["没有修复后验证"],
                }
            else:
                value = {
                    "items": [
                        {
                            "claim_id": c["claim_id"],
                            "verdict": "insufficient"
                            if Model.fault == "review" and c["kind"] == "hypothesis"
                            else "supported",
                            "reason": "测试侧语义判断",
                        }
                        for c in data["claims"]
                    ]
                }
            return {
                "model": self.settings.main_model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            }, 1

    monkeypatch.setattr(graph, "Provider", Model)
    return Model, calls


def ask(context, auth, ticket, payload):
    return context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations", headers=auth, json=payload
    )


def test_req1303_real_mcp_startup_hypotheses_and_req1307_readback(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = ask(context, auth, ticket, payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed", body
    assert body["workflow_version"] == "hypothesis-investigation.v1"
    assert body["hypotheses"][0]["status"] == "supported"
    assert body["hypotheses"][0]["semantic_reviewed"] and not body["human_reviewed"]
    assert body["mcp"]["transport"] == "stdio" and body["tool_calls"] == 3
    assert body["usage"]["known_model_calls"] == 5
    assert len(body["plan_history"]) == 3 and len(body["steps"]) == 2
    assert body["plan_history"][-1]["hypotheses"][0]["semantic_reviewed"] is False
    path = f"/api/investigations/{body['investigation_id']}"
    assert context["client"].get(path, headers=auth).json() == body
    assert context["client"].get(path, headers=headers(context, "username_b")).status_code == 404
    current = next(
        e
        for e in body["evidence"]
        if e["source"].get("source_type") == "current_startup_diagnostic"
    )
    ref = context["client"].get(current["reference_url"], headers=auth)
    assert ref.status_code == 200 and ref.json()["text_verified"]
    assert (
        context["client"]
        .get(current["reference_url"], headers=headers(context, "username_b"))
        .status_code
        == 404
    )
    assert (
        context["client"]
        .get(f"/api/tickets/{ticket['ticket_id']}/investigations", headers=auth)
        .json()["total"]
        == 0
    )
    assert (
        context["client"]
        .get(f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations", headers=auth)
        .json()["total"]
        == 1
    )


@pytest.mark.parametrize(
    "fault,expected,unknown",
    [
        ("timeout", "MODEL_TIMEOUT", 1),
        ("repeat", "repeated_step", 0),
        ("scope", "MODEL_RESPONSE_INVALID", 0),
        ("quote", "HYPOTHESIS_CITATION_INVALID", 0),
    ],
)
def test_req1305_invalid_transition_and_req1306_failures_saved(
    context, embedding, model, fault, expected, unknown
):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    model[0].fault = fault
    body = ask(context, auth, ticket, payload).json()
    assert body["stop_reason"] == expected, body
    assert body["report"] is None and body["usage"]["unknown_model_calls"] == unknown
    assert body["persisted"]


def test_req1305_review_downgrades_unproven_cause(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    model[0].fault = "review"
    body = ask(context, auth, ticket, payload).json()
    assert body["status"] == "completed", body
    assert body["hypotheses"][0]["status"] == "unresolved"
    assert body["hypotheses"][0]["proposed_status"] == "supported"


def test_req1301_scope_and_missing_input_precede_model(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    assert ask(context, headers(context, "username_b"), ticket, payload).status_code == 404
    assert (
        ask(context, auth, create_ticket(context, auth, version="2.0"), payload).status_code == 422
    )
    no_run = ask(context, auth, ticket, {"index_id": payload["index_id"]}).json()
    assert no_run["stop_reason"] == "lab_run_required" and no_run["model_calls"] == 0
    assert not model[1]


def test_req1306_budget_reserves_report_review(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    body = ask(context, auth, ticket, {**payload, "max_model_calls": 2}).json()
    assert body["stop_reason"] == "model_budget" and body["model_calls"] == 0, body
    assert body["tool_calls"] == 1 and body["report"] is None


def test_req1307_rls_registration_is_read_only_and_tamper_detected(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM investigation_lab_runs")) == 0
        for sql in (
            "UPDATE investigation_lab_runs SET sha256='bad'",
            "INSERT INTO investigation_lab_runs SELECT * FROM investigation_lab_runs",
        ):
            with pytest.raises(DBAPIError), engine.begin() as c:
                c.execute(text(sql))
        with context["admin"].begin() as c:
            c.execute(
                text("UPDATE investigation_lab_runs SET sha256=:sha WHERE id=:id"),
                {"sha": "a" * 64, "id": payload["lab_run_id"]},
            )
        assert ask(context, auth, ticket, payload).status_code == 409
        assert not model[1]
    finally:
        engine.dispose()


def test_req1303_real_live_mcp_denies_control_params_and_revoked_session(context, embedding):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    me = context["client"].get("/api/auth/me", headers=auth).json()
    with context["admin"].connect() as connection:
        sid = connection.scalar(
            text(
                "SELECT id FROM auth_sessions WHERE user_id=:id AND revoked_at IS NULL "
                "ORDER BY expires_at DESC LIMIT 1"
            ),
            {"id": me["user_id"]},
        )
        corpus = connection.scalar(
            text("SELECT corpus_sha256 FROM retrieval_indexes WHERE id=:id"),
            {"id": payload["index_id"]},
        )
        registration_sha = connection.scalar(
            text("SELECT sha256 FROM investigation_lab_runs WHERE id=:id"),
            {"id": payload["lab_run_id"]},
        )
    scope = LiveScope(
        organization_id=me["organization_id"],
        user_id=me["user_id"],
        session_id=sid,
        ticket_id=ticket["ticket_id"],
        index_id=payload["index_id"],
        product_version="1.1",
        experiment_id=None,
        corpus_sha256=corpus,
        ticket_sha256=digest(ticket),
        lab_run_id=payload["lab_run_id"],
        registration_sha256=registration_sha,
        investigation_id=uuid4(),
    )

    async def check():
        async with live_client.connect(scope, context["app_url"]) as client:
            assert "organization_id" not in (await client.call("get_ticket", {}, 10))["ticket"]
            denied = await client.client.call_tool("release_pool", {})
            assert denied.isError and denied.content[0].text == "TOOL_NOT_ALLOWED"
            denied = await client.client.call_tool(
                "read_current_observations", {"run_id": str(uuid4())}
            )
            assert denied.isError and denied.content[0].text == "TOOL_ARGUMENTS_INVALID"
            context["client"].post("/api/auth/logout", headers=auth)
            denied = await client.client.call_tool("read_startup_diagnostic", {})
            assert denied.isError and denied.content[0].text == "AUTHENTICATION_REQUIRED"

    anyio.run(check)


def test_req1303_live_schema_drift_rejected():
    tools = [Tool.model_validate(t) for t in live_manifest()]
    live_client.check_manifest(tools)
    tools[2].inputSchema["properties"]["url"] = {"type": "string"}
    with pytest.raises(live_client.MCPFailure):
        live_client.check_manifest(tools)


def test_req1306_late_model_retains_known_usage(context, embedding, model, monkeypatch):
    from supportops.investigations.live_guard import LiveGate

    now = [0.0]

    class ClockGate(LiveGate):
        def __init__(self, tools, models, budget):
            super().__init__(tools, models, budget, clock=lambda: now[0])

    original = model[0].post

    def late(self, url, body):
        response = original(self, url, body)
        now[0] = 2.0
        return response

    monkeypatch.setattr(graph, "LiveGate", ClockGate)
    monkeypatch.setattr(model[0], "post", late)
    auth = headers(context)
    ticket, payload = setup(context, auth)
    body = ask(context, auth, ticket, {**payload, "time_budget_ms": 1000}).json()
    assert body["status"] == "stopped" and body["stop_reason"] == "time_budget", body
    assert body["usage"]["known_model_calls"] == 1 and body["report"] is None


def test_req1303_startup_plan_cannot_select_online_tools(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    model[0].fault = "mode"
    body = ask(context, auth, ticket, payload).json()
    assert body["stop_reason"] == "HYPOTHESIS_TOOL_MODE_INVALID", body
    assert body["tool_calls"] == 1 and body["report"] is None


def test_req1302_model_receives_only_its_own_hypothesis_schema(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    model[0].fault = "echo"
    body = ask(context, auth, ticket, payload).json()
    assert body["status"] == "completed", body
    for data in model[1]:
        if data.get("phase") == "plan":
            for item in data["hypotheses"]:
                assert "semantic_reviewed" not in item and "human_reviewed" not in item


def test_req1304_finish_requires_explicit_current_observation_tool(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    model[0].fault = "skip_live"
    body = ask(context, auth, ticket, payload).json()
    assert body["stop_reason"] == "HYPOTHESIS_OBSERVATIONS_REQUIRED", body["stop_reason"]
    assert body["status"] == "failed" and body["report"] is None
    assert body["tool_calls"] == 2 and body["model_calls"] == 2
