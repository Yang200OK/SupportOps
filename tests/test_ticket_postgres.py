"""真实 PostgreSQL 身份、事务与组织隔离测试，禁止转入 SQLite。"""

import hashlib
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from supportops.api.app import create_app
from supportops.db.runtime import MIGRATION

pytestmark = pytest.mark.integration


@pytest.fixture
def context(monkeypatch):
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    app_url = values.get("SUPPORTOPS_TEST_DATABASE_URL")
    admin_url = values.get("SUPPORTOPS_TEST_ADMIN_DATABASE_URL")
    if not app_url or not admin_url:
        pytest.fail("真实集成配置缺失，不能使用备用数据库。")
    for url in (app_url, admin_url):
        if make_url(url).database != "supportops_test":
            pytest.fail("集成测试禁止修改非 supportops_test 数据库。")
    monkeypatch.setenv("SUPPORTOPS_DATABASE_URL", app_url)
    admin = create_engine(admin_url, hide_parameters=True)
    suffix = uuid4().hex[:10]
    organization_a, organization_b = uuid4(), uuid4()
    user_a, user_b, colleague = uuid4(), uuid4(), uuid4()
    password = secrets.token_urlsafe(18)
    password_hash = PasswordHash.recommended().hash(password)
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations(id, name) VALUES (:id,:name)"),
            [
                {"id": organization_a, "name": f"Test A {suffix}"},
                {"id": organization_b, "name": f"Test B {suffix}"},
            ],
        )
        connection.execute(
            text(
                "INSERT INTO users(id,organization_id,username,password_hash,active) "
                "VALUES (:id,:org,:name,:hash,true)"
            ),
            [
                {"id": user_a, "org": organization_a, "name": f"a_{suffix}", "hash": password_hash},
                {"id": user_b, "org": organization_b, "name": f"b_{suffix}", "hash": password_hash},
                {
                    "id": colleague,
                    "org": organization_a,
                    "name": f"c_{suffix}",
                    "hash": password_hash,
                },
            ],
        )
    try:
        with TestClient(create_app()) as client:
            yield {
                "client": client,
                "admin": admin,
                "app_url": app_url,
                "username_a": f"a_{suffix}",
                "username_b": f"b_{suffix}",
                "colleague": f"c_{suffix}",
                "password": password,
                "organization_a": organization_a,
                "organization_b": organization_b,
                "user_a": user_a,
                "user_b": user_b,
            }
    finally:
        # 只按本夹具生成的组织 ID 清理明确的测试库；不 drop / truncate。
        with admin.begin() as connection:
            for org in (organization_a, organization_b):
                connection.execute(
                    text(
                        "DELETE FROM auth_sessions WHERE user_id IN "
                        "(SELECT id FROM users WHERE organization_id=:org)"
                    ),
                    {"org": org},
                )
                connection.execute(
                    text("DELETE FROM retrieval_entries WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM retrieval_indexes WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM chunk_sets WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM experiments WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM document_revisions WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM documents WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM runs WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM tickets WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(
                    text("DELETE FROM users WHERE organization_id=:org"), {"org": org}
                )
                connection.execute(text("DELETE FROM organizations WHERE id=:org"), {"org": org})
        admin.dispose()


def login(context, username_key="username_a"):
    response = context["client"].post(
        "/api/auth/login",
        json={
            "username": context[username_key],
            "password": context["password"],
        },
    )
    assert response.status_code == 200
    return response.json()


def headers(context, username_key="username_a"):
    return {"Authorization": "Bearer " + login(context, username_key)["access_token"]}


def draft(version="1.1"):
    return {
        "title": "  升级后超时  ",
        "description": "\nRD_TIMEOUT\n  delivery_timeout_ms=2000\n",
        "product": "relaydesk",
        "product_version": version,
        "environment": "local_lab",
        "source_type": "synthetic_case",
    }


def create_ticket(context, auth, version="1.1"):
    response = context["client"].post("/api/tickets", json=draft(version), headers=auth)
    assert response.status_code == 201
    return response.json()


def test_req101_ready_verifies_actual_schema_and_restricted_role(context):
    response = context["client"].get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ready"
    assert response.json()["migration"] == MIGRATION


def test_req102_password_and_bearer_are_only_stored_as_hashes(context):
    result = login(context)
    assert result["token_type"] == "bearer"
    assert result["expires_in"] == 28800
    token = result["access_token"]
    with context["admin"].connect() as connection:
        row = connection.execute(
            text("SELECT token_hash FROM auth_sessions WHERE user_id=:id"),
            {"id": context["user_a"]},
        ).scalar_one()
        password_hash = connection.execute(
            text("SELECT password_hash FROM users WHERE id=:id"), {"id": context["user_a"]}
        ).scalar_one()
    assert row == hashlib.sha256(token.encode()).hexdigest()
    assert row != token
    assert password_hash.startswith("$argon2id$")
    assert password_hash != context["password"]
    me = context["client"].get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert me.status_code == 200
    assert me.json()["organization_id"] == str(context["organization_a"])
    assert "password_hash" not in me.text and "token_hash" not in me.text


def test_req102_unknown_account_and_wrong_password_share_error(context):
    client = context["client"]
    wrong = client.post(
        "/api/auth/login", json={"username": context["username_a"], "password": "wrong"}
    )
    missing = client.post(
        "/api/auth/login", json={"username": "nonexistent_user", "password": "wrong"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_req102_expired_session_is_rejected(context):
    auth = headers(context)
    with context["admin"].begin() as connection:
        connection.execute(
            text("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE user_id=:id"),
            {"id": context["user_a"]},
        )
    assert context["client"].get("/api/auth/me", headers=auth).status_code == 401


def test_req102_logout_revokes_server_session(context):
    auth = headers(context)
    assert context["client"].post("/api/auth/logout", headers=auth).status_code == 204
    assert context["client"].get("/api/auth/me", headers=auth).status_code == 401


def test_req102_disabled_user_cannot_login_or_reuse_session(context):
    auth = headers(context)
    with context["admin"].begin() as connection:
        connection.execute(
            text("UPDATE users SET active=false WHERE id=:id"), {"id": context["user_a"]}
        )
    assert context["client"].get("/api/auth/me", headers=auth).status_code == 401
    response = context["client"].post(
        "/api/auth/login",
        json={
            "username": context["username_a"],
            "password": context["password"],
        },
    )
    assert response.status_code == 401


def test_req103_forged_organization_header_does_not_change_identity(context):
    auth = {**headers(context), "X-Organization-ID": str(context["organization_b"])}
    result = create_ticket(context, auth)
    assert result["organization_id"] == str(context["organization_a"])
    assert result["requester_id"] == str(context["user_a"])


def test_req104_creates_real_normalized_ticket(context):
    result = create_ticket(context, headers(context))
    assert result["persisted"] is True
    assert result["title"] == "升级后超时"
    assert result["description"] == "RD_TIMEOUT\n  delivery_timeout_ms=2000"
    assert result["intake_status"] == "ready_for_intake"
    assert result["missing_fields"] == []
    with context["admin"].connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM tickets WHERE id=:id"), {"id": result["ticket_id"]}
        ).scalar_one()
    assert count == 1


def test_req104_unknown_version_is_persisted_with_clarification(context):
    result = create_ticket(context, headers(context), None)
    assert result["intake_status"] == "needs_clarification"
    assert result["missing_fields"] == ["product_version"]
    assert result["product_version"] is None


@pytest.mark.parametrize(
    "field", ["organization_id", "requester_id", "status", "root_cause", "approved_action"]
)
def test_req104_invalid_write_has_no_database_side_effect(context, field):
    response = context["client"].post(
        "/api/tickets", headers=headers(context), json={**draft(), field: "untrusted"}
    )
    assert response.status_code == 422
    with context["admin"].connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM tickets WHERE organization_id=:org"),
            {"org": context["organization_a"]},
        ).scalar_one()
    assert count == 0


def test_req105_list_and_get_are_isolated_and_missing_is_indistinguishable(context):
    client = context["client"]
    a, b = headers(context), headers(context, "username_b")
    ticket_a, ticket_b = create_ticket(context, a), create_ticket(context, b)
    result = client.get("/api/tickets", headers=a).json()
    assert result["total"] == 1
    assert [item["ticket_id"] for item in result["items"]] == [ticket_a["ticket_id"]]
    assert client.get(f"/api/tickets/{ticket_a['ticket_id']}", headers=a).status_code == 200
    other = client.get(f"/api/tickets/{ticket_b['ticket_id']}", headers=a)
    missing = client.get(f"/api/tickets/{uuid4()}", headers=a)
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_req105_same_organization_colleague_can_read_ticket(context):
    result = create_ticket(context, headers(context))
    colleague_auth = headers(context, "colleague")
    response = context["client"].get(f"/api/tickets/{result['ticket_id']}", headers=colleague_auth)
    assert response.status_code == 200
    assert response.json()["requester_id"] == str(context["user_a"])


def test_req105_pagination_is_ordered_and_bounded(context):
    auth = headers(context)
    created = [create_ticket(context, auth)["ticket_id"] for _ in range(3)]
    response = context["client"].get("/api/tickets?offset=1&limit=1", headers=auth)
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert [item["ticket_id"] for item in response.json()["items"]] == [created[1]]
    for query in ("offset=-1", "limit=0", "limit=101"):
        assert context["client"].get("/api/tickets?" + query, headers=auth).status_code == 422


def test_req106_rls_survives_direct_sql_and_connection_reuse(context):
    a, b = headers(context), headers(context, "username_b")
    ticket_a = create_ticket(context, a)
    create_ticket(context, b)
    engine = create_engine(context["app_url"], pool_size=1, max_overflow=0, hide_parameters=True)
    try:
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM tickets")).scalar_one() == 0
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(context["organization_a"])},
            )
            assert [
                str(row) for row in connection.execute(text("SELECT id FROM tickets")).scalars()
            ] == [ticket_a["ticket_id"]]
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM tickets")).scalar_one() == 0
            assert not connection.execute(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user")
            ).scalar_one()
    finally:
        engine.dispose()


def test_req106_rls_blocks_cross_scope_insert_and_foreign_key_blocks_wrong_requester(context):
    engine = create_engine(context["app_url"], hide_parameters=True)
    statement = text(
        "INSERT INTO tickets(id,organization_id,requester_id,title,description,product,"
        "product_version,environment,source_type,intake_status) VALUES "
        "(:id,:org,:user,'x','RD_TIMEOUT','relaydesk','1.1','local_lab',"
        "'synthetic_case','ready_for_intake')"
    )
    try:
        for org, user in (
            (context["organization_b"], context["user_b"]),
            (context["organization_a"], context["user_b"]),
        ):
            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('app.organization_id',:org,true)"),
                        {"org": str(context["organization_a"])},
                    )
                    connection.execute(statement, {"id": uuid4(), "org": org, "user": user})
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE forbidden_table(id integer)"))
    finally:
        engine.dispose()


def test_req107_stale_migration_fails_readiness(context):
    with context["admin"].begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='stale'"))
    try:
        response = context["client"].get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    finally:
        with context["admin"].begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:version"), {"version": MIGRATION}
            )


def test_req107_admin_database_identity_is_rejected(context, monkeypatch):
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    monkeypatch.setenv("SUPPORTOPS_DATABASE_URL", values["SUPPORTOPS_TEST_ADMIN_DATABASE_URL"])
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").status_code == 503


def test_req107_empty_migration_record_is_explicit_failure(context):
    with context["admin"].begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
    try:
        response = context["client"].get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    finally:
        with context["admin"].begin() as connection:
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:version)"),
                {"version": MIGRATION},
            )


def test_req107_unreachable_database_does_not_leak_connection(context, monkeypatch):
    url = make_url(context["app_url"]).set(port=1).render_as_string(hide_password=False)
    monkeypatch.setenv("SUPPORTOPS_DATABASE_URL", url)
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert "postgresql" not in response.text
        assert "traceback" not in response.text.lower()


def test_req103_invalid_bearer_is_not_identity(context):
    response = context["client"].get("/api/auth/me", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_req104_invalid_ticket_uuid_is_validation_error(context):
    response = context["client"].get("/api/tickets/not-a-uuid", headers=headers(context))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
