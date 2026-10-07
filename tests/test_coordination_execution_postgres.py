"""真实 PostgreSQL / stdio MCP；模型替身只用于本文件。"""

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_coordination_postgres import create
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.coordination import executor
from supportops.coordination.execution_models import Execution
from supportops.coordination.execution_service import save

pytestmark = pytest.mark.integration


def start(context):
    auth, _, _, board = create(context)
    request = {"request_id": str(uuid4())}
    path = f"/api/coordination-boards/{board['board_id']}/executions"
    response = context["client"].post(path, headers=auth, json=request)
    assert response.status_code == 201, response.text
    return auth, board, path, request, response.json()


@pytest.fixture
def model(monkeypatch):
    calls = []

    def chat(provider, system, data, schema, max_tokens):
        calls.append(deepcopy(data))
        if "RolePlan" == schema.__name__:
            tools = data["package"]["tools"]
            value = {
                "reason": "读取角色允许的证据",
                "steps": [
                    {
                        "tool": t,
                        "arguments": {"query": "RD_TIMEOUT"} if t == "search_knowledge" else {},
                    }
                    for t in tools
                ],
            }
        elif schema.__name__ == "SupportReview":
            value = {
                "items": [
                    {"claim_id": c["claim_id"], "verdict": "supported", "reason": "测试核对"}
                    for c in data["claims"]
                ]
            }
        else:
            catalog = data["quote_catalog"]
            first = next(iter(catalog))
            value = {
                "claims": [
                    {
                        "claim_id": "C1",
                        "kind": "fact",
                        "text": "证据包含诊断记录",
                        "citations": [{"evidence_id": first, "quote_index": 0}],
                    }
                ],
                "missing_information": ["仍需人工复核"],
            }
            if schema.__name__ == "CoordinationMerge":
                value["conflicts"] = []
        return schema.model_validate(value), {
            "input_tokens": 5,
            "output_tokens": 3,
            "cost_cny": None,
        }

    monkeypatch.setattr(executor, "chat_json", chat)
    return calls


def advance(context, auth, body):
    response = context["client"].post(
        f"/api/coordination-executions/{body['execution_id']}/advance", headers=auth
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_req1901_1902_1904_two_waves_real_role_mcp(context, embedding, model):
    auth, board, path, request, initial = start(context)
    client = context["client"]
    assert client.post(path, headers=auth, json=request).json() == initial
    wave = advance(context, auth, initial)
    assert wave["status"] == "pending", wave
    assert [wave["tasks"][t]["status"] for t in ("documents", "runtime", "synthesis")] == [
        "completed",
        "completed",
        "pending",
    ]
    assert wave["usage"]["model_calls"] == 4
    assert wave["usage"]["tool_calls"] == 2
    assert all("reports" not in c for c in model[:4])
    for call in model[:4]:
        assert "package" in call and "scope" not in call
    final = advance(context, auth, wave)
    assert final["status"] == "completed", final
    assert final["usage"]["model_calls"] == 6
    assert final["result"]["root_cause_status"] == "unresolved"
    assert final["result"]["claims"][0]["citations"][0]["literal_verified"]
    assert advance(context, auth, final) == final
    assert len(model) == 6
    old = client.get(f"/api/coordination-boards/{board['board_id']}", headers=auth).json()
    assert old == board
    for task in final["tasks"].values():
        for evidence in task["evidence"]:
            response = client.get(
                f"/api/coordination-executions/{final['execution_id']}/evidence/{evidence['evidence_id']}",
                headers=auth,
            )
            assert response.status_code == 200
            assert response.json()["evidence"]["text"] == evidence["text"]
    other = headers(context, "username_b")
    assert (
        client.get(
            f"/api/coordination-executions/{initial['execution_id']}", headers=other
        ).status_code
        == 404
    )
    assert client.post(path, headers=other, json={"request_id": str(uuid4())}).status_code == 404


def test_req1902_actual_parallel_private_models(context, embedding, model, monkeypatch):
    original = executor.chat_json
    barrier = Barrier(2)

    def parallel(*args, **kwargs):
        if args[3].__name__ == "RolePlan":
            barrier.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "chat_json", parallel)
    auth, _, _, _, body = start(context)
    wave = advance(context, auth, body)
    assert wave["tasks"]["documents"]["status"] == "completed", wave
    assert wave["tasks"]["runtime"]["status"] == "completed", wave
    plans = [o for o in wave["operations"] if o["key"] == "plan"]
    assert max(o["started_at"] for o in plans) < min(o["finished_at"] for o in plans)


def change_admin(context, body, change):
    with Session(context["admin"]) as session, session.begin():
        row = session.get(Execution, body["execution_id"])
        state = deepcopy(row.state)
        change(state)
        save(session, row, state, "test_process_interruption")


@pytest.mark.parametrize("unknown", [False, True])
def test_req1905_expired_lease_saved_phase_or_unknown_stops(context, embedding, model, unknown):
    auth, _, _, _, body = start(context)
    wave = advance(context, auth, body)

    def interrupt(s):
        s.update(status="running", lease=str(uuid4()), lease_until=time.time() - 1)
        if unknown:
            s["operations"].append(
                {
                    "task": "synthesis",
                    "key": "merge",
                    "kind": "model",
                    "status": "started",
                    "usage": None,
                }
            )
            s["usage"]["model_calls"] += 1
            s["tasks"]["synthesis"].update(status="running")

    change_admin(context, wave, interrupt)
    restored = advance(context, auth, wave)
    assert restored["status"] == ("uncertain" if unknown else "completed"), restored
    assert len(model) == (4 if unknown else 6)
    assert restored["tasks"]["documents"]["report"] == wave["tasks"]["documents"]["report"]
    assert restored["deadline"] == wave["deadline"]


def test_req1903_global_lock_budget_race(context, embedding, model):
    from supportops.actions.service import transaction
    from supportops.auth.service import Principal
    from supportops.coordination.execution_core import reserve
    from supportops.coordination.execution_service import get

    auth, _, _, _, body = start(context)
    engine = context["client"].app.state.database
    with Session(context["admin"]) as s:
        row = s.get(Execution, body["execution_id"])
        principal = Principal(
            context["user_a"],
            "test",
            context["organization_a"],
            "test",
            UUID(row.binding["session_id"]),
        )

    def reserve_one(n):
        try:
            with transaction(engine, principal) as s:
                row = get(s, principal, body["execution_id"], lock=True)
                state = deepcopy(row.state)
                board = executor.store.boards.get_board(s, principal, row.board_id)
                reserve(state, board.plan, "documents", "model", f"r{n}", time.time())
                save(s, row, state, "test_budget_race")
            return "reserved"
        except ValueError:
            return "denied"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve_one, range(4)))
    assert outcomes.count("reserved") == 3
    assert outcomes.count("denied") == 1


def test_req1905_busy_cancel_and_no_following_model(context, embedding, model):
    auth, _, _, _, body = start(context)

    def lock(s):
        s.update(
            status="running",
            lease=str(uuid4()),
            lease_until=time.time() + 20,
            deadline=time.time() + 20,
        )

    change_admin(context, body, lock)
    path = f"/api/coordination-executions/{body['execution_id']}"
    assert context["client"].post(path + "/advance", headers=auth).status_code == 409
    cancelled = context["client"].post(path + "/cancel", headers=auth).json()
    assert cancelled["status"] == "cancelled"
    assert advance(context, auth, body)["status"] == "cancelled"
    assert model == []


def test_req1901_rls_and_immutable_inputs(context, embedding, model):
    auth, _, _, _, body = start(context)
    engine = create_engine(context["app_url"], hide_parameters=True)
    with engine.begin() as c:
        assert c.execute(text("SELECT id FROM coordination_executions")).all() == []
    with pytest.raises(DBAPIError), engine.begin() as c:
        c.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(context["organization_a"])},
        )
        c.execute(
            text("UPDATE coordination_executions SET binding='{}'::jsonb WHERE id=:id"),
            {"id": body["execution_id"]},
        )
    with pytest.raises(DBAPIError), engine.begin() as c:
        c.execute(text("DELETE FROM coordination_execution_events"))
    engine.dispose()


def test_req1903_model_failure_preserves_known_usage_and_partial_success(
    context, embedding, model, monkeypatch
):
    from supportops.rag.model import ChatFailure

    original = executor.chat_json

    def failed(provider, system, data, schema, tokens):
        if data.get("package", {}).get("task_id") == "runtime" and schema.__name__ == "RolePlan":
            raise ChatFailure("MODEL_RESPONSE_INVALID", {"input_tokens": 11, "output_tokens": 7})
        return original(provider, system, data, schema, tokens)

    monkeypatch.setattr(executor, "chat_json", failed)
    auth, _, _, _, body = start(context)
    stopped = advance(context, auth, body)
    assert stopped["status"] == "failed"
    assert stopped["tasks"]["documents"]["status"] == "completed"
    assert stopped["tasks"]["runtime"]["error"] == "MODEL_RESPONSE_INVALID"
    assert stopped["tasks"]["synthesis"]["status"] == "pending"
    assert stopped["model_accounting"]["known_calls"] == 3
    assert stopped["model_accounting"]["input_tokens"] == 21
    assert advance(context, auth, body) == stopped


def test_req1904_unresolved_conflict_bound_and_semantic_review(
    context, embedding, model, monkeypatch
):
    original = executor.chat_json

    def conflicting(provider, system, data, schema, tokens):
        result, usage = original(provider, system, data, schema, tokens)
        if schema.__name__ == "CoordinationMerge":
            ids = list(data["quote_catalog"])
            value = result.model_dump()
            value["conflicts"] = [
                {
                    "description": "测试构造的模型未决冲突",
                    "left": {"evidence_id": ids[0], "quote_index": 0},
                    "right": {"evidence_id": ids[1], "quote_index": 0},
                    "status": "unresolved",
                }
            ]
            result = schema.model_validate(value)
        return result, usage

    monkeypatch.setattr(executor, "chat_json", conflicting)
    auth, _, _, _, body = start(context)
    final = advance(context, auth, advance(context, auth, body))
    assert final["status"] == "completed"
    conflict = final["result"]["conflicts"][0]
    assert conflict["status"] == "unresolved" and not conflict["human_reviewed"]
    assert len(conflict["citations"]) == 2


def test_req1905_cancel_inflight_receipt_still_accounts(context, embedding, model, monkeypatch):
    from threading import Event

    original = executor.chat_json
    begun, released = Event(), Event()

    def waiting(provider, system, data, schema, tokens):
        if data.get("package", {}).get("task_id") == "documents" and schema.__name__ == "RolePlan":
            begun.set()
            assert released.wait(8)
        return original(provider, system, data, schema, tokens)

    monkeypatch.setattr(executor, "chat_json", waiting)
    auth, _, _, _, body = start(context)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(advance, context, auth, body)
        assert begun.wait(8)
        path = f"/api/coordination-executions/{body['execution_id']}/cancel"
        assert context["client"].post(path, headers=auth).json()["status"] == "cancelled"
        released.set()
        cancelled = future.result(timeout=10)
    assert cancelled["status"] == "cancelled"
    assert cancelled["model_accounting"]["known_calls"] >= 1
    assert cancelled["tasks"]["documents"]["status"] == "cancelled"
    assert cancelled["result"] is None


def test_req1902_server_rejects_other_role_and_repeated_reserved_call(context, embedding, model):
    import anyio

    from supportops.auth.service import Principal

    auth, _, _, _, body = start(context)
    lease = uuid4()

    def running(s):
        s.update(
            status="running",
            lease=str(lease),
            lease_until=time.time() + 90,
            deadline=time.time() + 240,
        )
        s["tasks"]["documents"]["status"] = "running"

    change_admin(context, body, running)
    with Session(context["admin"]) as session:
        row = session.get(Execution, body["execution_id"])
        principal = Principal(
            context["user_a"],
            "test",
            context["organization_a"],
            "test",
            UUID(row.binding["session_id"]),
        )
    database = context["client"].app.state.database
    trusted, seconds = executor.begin_operation(
        database,
        principal,
        UUID(body["execution_id"]),
        lease,
        "documents",
        "tool",
        "malicious-test",
        {"tool": "search_knowledge", "arguments": {"query": "RD_TIMEOUT"}},
    )

    async def run():
        async with executor.connect(trusted, context["app_url"], ["search_knowledge"]) as tools:
            denied = await tools.client.call_tool("read_startup_diagnostic", {})
            assert denied.isError
            accepted = await tools.client.call_tool("search_knowledge", {"query": "RD_TIMEOUT"})
            assert not accepted.isError
            repeated = await tools.client.call_tool("search_knowledge", {"query": "RD_TIMEOUT"})
            assert repeated.isError

    anyio.run(run)


def test_req1903_revoked_session_stops_new_calls_but_receipt_keeps_usage(
    context, embedding, model, monkeypatch
):
    from threading import Event

    original = executor.chat_json
    started, released = Event(), Event()

    def waiting(provider, system, data, schema, tokens):
        if data.get("package", {}).get("task_id") == "documents" and schema.__name__ == "RolePlan":
            started.set()
            assert released.wait(8)
        return original(provider, system, data, schema, tokens)

    monkeypatch.setattr(executor, "chat_json", waiting)
    auth, _, _, _, body = start(context)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(advance, context, auth, body)
        assert started.wait(8)
        context["client"].post("/api/auth/logout", headers=auth).raise_for_status()
        released.set()
        stopped = future.result(timeout=10)
    assert stopped["status"] == "failed"
    assert stopped["tasks"]["documents"]["error"] == "AUTHENTICATION_REQUIRED"
    assert stopped["model_accounting"]["known_calls"] >= 1


def test_req1903_small_context_stops_without_cropping(context, embedding, model):
    from test_hypothesis_postgres import setup

    auth = headers(context)
    ticket, payload = setup(context, auth)
    payload.update(request_id=str(uuid4()), context_chars=300)
    board = (
        context["client"]
        .post(f"/api/tickets/{ticket['ticket_id']}/coordination-boards", headers=auth, json=payload)
        .json()
    )
    body = (
        context["client"]
        .post(
            f"/api/coordination-boards/{board['board_id']}/executions",
            headers=auth,
            json={"request_id": str(uuid4())},
        )
        .json()
    )
    stopped = advance(context, auth, body)
    assert stopped["status"] == "failed"
    assert stopped["tasks"]["runtime"]["error"] == "CONTEXT_BUDGET_EXHAUSTED"
    assert stopped["tasks"]["runtime"]["results"][0]["value"]["evidence"][0]["text"]
    assert stopped["tasks"]["runtime"]["usage"]["model_calls"] == 1


def test_req1905_late_receipt_after_unknown_only_corrects_accounting(context, embedding, model):
    from supportops.auth.service import Principal
    from supportops.coordination.execution_core import recover

    auth, _, _, _, body = start(context)
    lease = uuid4()

    def running(s):
        s.update(
            status="running",
            lease=str(lease),
            lease_until=time.time() + 90,
            deadline=time.time() + 240,
        )
        s["tasks"]["documents"]["status"] = "running"

    change_admin(context, body, running)
    with Session(context["admin"]) as session:
        row = session.get(Execution, body["execution_id"])
        principal = Principal(
            context["user_a"],
            "test",
            context["organization_a"],
            "test",
            UUID(row.binding["session_id"]),
        )
    database = context["client"].app.state.database
    executor.begin_operation(
        database, principal, UUID(body["execution_id"]), lease, "documents", "model", "plan"
    )
    change_admin(context, body, recover)

    def prohibited(*args):
        raise AssertionError("未知调用的迟到回执不得推进阶段")

    executor.receipt(
        database,
        principal,
        UUID(body["execution_id"]),
        lease,
        "documents",
        "plan",
        {"reason": "迟到结果", "steps": []},
        {"input_tokens": 5, "output_tokens": 3},
        {"model": "test-model"},
        None,
        prohibited,
    )
    response = context["client"].get(
        f"/api/coordination-executions/{body['execution_id']}", headers=auth
    )
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "uncertain"
    assert value["model_accounting"]["known_calls"] == 1
    assert value["model_accounting"]["unknown_calls"] == 0
    assert value["tasks"]["documents"]["status"] == "failed"
    assert value["result"] is None
