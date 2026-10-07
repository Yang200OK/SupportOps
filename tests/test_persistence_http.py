"""不依赖数据库的本轮失败契约；缺失依赖不会变成内存存储。"""

import pytest
from fastapi.testclient import TestClient

from supportops.api.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPPORTOPS_DATABASE_URL", "")
    with TestClient(create_app()) as value:
        yield value


def test_req107_unconfigured_readiness_fails_explicitly(client):
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


@pytest.mark.parametrize(
    "method,path", [("get", "/api/auth/me"), ("post", "/api/auth/logout"), ("get", "/api/tickets")]
)
def test_req103_missing_identity_is_rejected_before_database_access(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_req107_login_without_database_is_not_fake_success(client):
    response = client.post("/api/auth/login", json={"username": "support_a", "password": "secret"})
    assert response.status_code == 503
    assert "secret" not in response.text
    assert "access_token" not in response.text


def test_req107_authenticated_shape_with_missing_database_fails(client):
    response = client.get("/api/tickets", headers={"Authorization": "Bearer example-token"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_req109_old_validation_still_works_without_database(client):
    assert client.get("/health/live").status_code == 200
    response = client.post(
        "/api/tickets/validate",
        json={
            "title": "中文工单",
            "description": "RD_TIMEOUT",
            "product": "relaydesk",
            "environment": "local_lab",
            "source_type": "synthetic_case",
        },
    )
    assert response.status_code == 200
    assert response.json()["intake_status"] == "needs_clarification"
    assert response.json()["persisted"] is False
