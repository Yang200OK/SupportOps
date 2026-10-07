"""真实数据库调度基础；不运行付费模型。"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_hypothesis_postgres import setup
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def create(context):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    payload["request_id"] = str(uuid4())
    path = f"/api/tickets/{ticket['ticket_id']}/coordination-boards"
    response = context["client"].post(path, headers=auth, json=payload)
    assert response.status_code == 201, response.text
    return auth, path, payload, response.json()


def test_req1801_1805_persistent_board_idempotency_and_scope(context, embedding):
    auth, path, payload, body = create(context)
    client = context["client"]
    assert body["execution_started"] is False
    assert body["usage"] == {"model_calls": 0, "tool_calls": 0}
    assert [t["status"] for t in body["tasks"]] == ["ready", "ready", "blocked"]
    assert client.post(path, headers=auth, json=payload).json() == body
    assert (
        client.post(path, headers=auth, json={**payload, "max_model_calls": 7}).status_code == 409
    )
    detail = f"/api/coordination-boards/{body['board_id']}"
    assert client.get(detail, headers=auth).json() == body
    assert client.get(detail, headers=headers(context, "username_b")).status_code == 404
    assert client.get(path, headers=auth).json()["total"] == 1
    assert client.get(detail + "/tasks/runtime/package", headers=auth).status_code == 200


def test_req1805_cancel_revision_and_immutable_history(context, embedding):
    auth, _, _, body = create(context)
    client = context["client"]
    path = f"/api/coordination-boards/{body['board_id']}"
    result = client.post(path + "/cancel", headers=auth, json={"revision": 1, "reason": "取消计划"})
    assert result.status_code == 200, result.text
    cancelled = result.json()
    assert cancelled["status"] == "cancelled" and cancelled["revision"] == 2
    assert cancelled["plan_sha256"] == body["plan_sha256"]
    assert all(t["status"] == "cancelled" for t in cancelled["tasks"])
    assert len(cancelled["events"]) == 2
    assert (
        client.post(
            path + "/cancel", headers=auth, json={"revision": 1, "reason": "陈旧请求"}
        ).status_code
        == 409
    )


def test_req1803_1804_rehashed_illegal_package_rejected(context, embedding):
    import json

    from supportops.chunks.chunking import digest

    auth, _, _, body = create(context)
    from sqlalchemy.orm import Session

    from supportops.coordination.models import Board, BoardEvent

    with Session(context["admin"]) as session, session.begin():
        original = session.get(Board, body["board_id"])
        data = {column.name: getattr(original, column.name) for column in Board.__table__.columns}
        data["id"], data["request_id"] = uuid4(), uuid4()
        data["plan"] = json.loads(json.dumps(data["plan"]))
        data["plan"]["tasks"][0]["package"]["tools"] = ["read_runtime_state"]
        data["plan_sha256"] = digest(data["plan"])
        session.add(Board(**data))
        session.flush()
        audit = session.get(BoardEvent, (original.id, 1))
        session.add(
            BoardEvent(
                board_id=data["id"],
                revision=1,
                organization_id=original.organization_id,
                data=audit.data,
                sha256=audit.sha256,
            )
        )
    response = context["client"].get(f"/api/coordination-boards/{data['id']}", headers=auth)
    assert response.status_code == 409


@pytest.mark.parametrize("kind", ["version", "foreign_index", "foreign_run", "expired", "unknown"])
def test_req1801_fixed_scope_rejected(context, embedding, kind):
    from datetime import datetime, timedelta, timezone

    from test_ticket_postgres import create_ticket

    from supportops.chunks.chunking import digest
    from supportops.investigations.live_models import LabRun

    auth = headers(context)
    ticket, payload = setup(context, auth)
    payload["request_id"] = str(uuid4())
    if kind == "version":
        ticket = create_ticket(context, auth, "2.0")
    elif kind == "unknown":
        ticket = create_ticket(context, auth, None)
    elif kind in {"foreign_index", "foreign_run"}:
        other_auth = headers(context, "username_b")
        other_ticket, other_payload = setup(context, other_auth)
        # 夹具登记在 A，明确改为 B；检索索引已经属于 B。
        with Session(context["admin"]) as session, session.begin():
            registration = session.get(LabRun, other_payload["lab_run_id"])
            registration.organization_id = context["organization_b"]
        key = "index_id" if kind == "foreign_index" else "lab_run_id"
        payload[key] = other_payload[key]
    else:
        with Session(context["admin"]) as session, session.begin():
            row = session.get(LabRun, payload["lab_run_id"])
            snapshot = dict(row.snapshot)
            end = datetime.now(timezone.utc) - timedelta(seconds=1)
            snapshot["started_at"] = (end - timedelta(minutes=1)).isoformat()
            snapshot["expires_at"] = end.isoformat()
            # 启动诊断时间也留在新过期窗口内，专门验证过期边界。
            snapshot["boot"]["observation"]["observed_at"] = (
                end - timedelta(seconds=2)
            ).isoformat()
            row.snapshot = snapshot
            row.sha256 = digest(snapshot)
            row.expires_at = end
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/coordination-boards", headers=auth, json=payload
    )
    assert response.status_code == (
        404 if kind.startswith("foreign") else 409 if kind == "expired" else 422
    )


def test_req1803_execution_package_checks_role_ready_and_current_scope(context, embedding):
    from supportops.api.errors import ServiceError
    from supportops.auth.service import Principal
    from supportops.coordination.service import execution_package

    auth, _, _, body = create(context)
    principal = Principal(context["user_a"], "test", context["organization_a"], "test", uuid4())
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            session.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_a"])},
            )
            with pytest.raises(ServiceError) as wrong:
                execution_package(session, principal, body["board_id"], "runtime", "document_agent")
            assert wrong.value.code == "COORDINATION_ROLE_DENIED"
            with pytest.raises(ServiceError) as blocked:
                execution_package(session, principal, body["board_id"], "synthesis", "coordinator")
            assert blocked.value.code == "COORDINATION_TASK_NOT_READY"
            package, binding = execution_package(
                session, principal, body["board_id"], "documents", "document_agent"
            )
            assert package["package"]["tools"] == ["search_knowledge"] and binding.mode == "startup"
        context["client"].post(
            f"/api/coordination-boards/{body['board_id']}/cancel",
            headers=auth,
            json={"revision": 1, "reason": "取消计划"},
        )
        with Session(engine) as session, session.begin():
            session.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_a"])},
            )
            with pytest.raises(ServiceError) as cancelled:
                execution_package(
                    session, principal, body["board_id"], "documents", "document_agent"
                )
            assert cancelled.value.code == "COORDINATION_TASK_NOT_READY"
    finally:
        engine.dispose()


def test_req1805_database_rls_and_content_permissions(context, embedding):
    _, _, _, body = create(context)
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM coordination_boards")).scalar_one()
                == 0
            )
            connection.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_b"])},
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM coordination_boards WHERE id=:id"),
                    {"id": body["board_id"]},
                ).scalar_one()
                == 0
            )
        for statement in [
            "UPDATE coordination_boards SET plan='{}' WHERE id=:id",
            "DELETE FROM coordination_boards WHERE id=:id",
            "UPDATE coordination_events SET data='{}' WHERE board_id=:id",
            "DELETE FROM coordination_events WHERE board_id=:id",
        ]:
            with engine.connect() as connection:
                connection.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(context["organization_a"])},
                )
                with pytest.raises(DBAPIError):
                    connection.execute(text(statement), {"id": body["board_id"]})
                connection.rollback()
    finally:
        engine.dispose()


def test_req1805_concurrent_request_has_one_board(context, embedding):
    from concurrent.futures import ThreadPoolExecutor

    auth = headers(context)
    ticket, payload = setup(context, auth)
    payload["request_id"] = str(uuid4())
    path = f"/api/tickets/{ticket['ticket_id']}/coordination-boards"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: context["client"].post(path, headers=auth, json=payload), range(2))
        )
    assert all(r.status_code == 201 for r in results)
    assert results[0].json() == results[1].json()
    assert context["client"].get(path, headers=auth).json()["total"] == 1
