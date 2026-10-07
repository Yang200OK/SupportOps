"""真实 PostgreSQL、MCP 与 Skill 快照；模型替身只用于控制契约。"""

import pytest
from test_hypothesis_postgres import model as model
from test_hypothesis_postgres import setup
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def test_req1505_authenticated_catalog_and_scoped_body(context):
    client = context["client"]
    assert client.get("/api/skills").status_code == 401
    auth = headers(context)
    result = client.get("/api/skills", headers=auth)
    assert result.status_code == 200
    assert len(result.json()["items"]) == 4
    assert "body" not in result.json()["items"][0]
    path = "/api/skills/pool-investigation/versions/1.0.0"
    assert client.get(path + "?product_version=1.1&mode=online", headers=auth).status_code == 200
    assert client.get(path + "?product_version=1.1&mode=startup", headers=auth).status_code == 422
    assert client.get(path + "?product_version=9.0&mode=online", headers=auth).status_code == 422
    client.post("/api/auth/logout", headers=auth)
    assert client.get("/api/skills", headers=auth).status_code == 401


def test_req1504_enabled_investigation_snapshots_and_budget(context, embedding, model, monkeypatch):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    # 测试构造工单原有 RD_TIMEOUT，startup 只匹配配置方法，读取诊断后再规划。
    from sqlalchemy import text

    with context["admin"].begin() as connection:
        connection.execute(
            text("UPDATE tickets SET description=:body WHERE id=:id"),
            {"body": "RelayDesk 1.1 启动失败 RD_CONFIG_INVALID", "id": ticket["ticket_id"]},
        )
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
        headers=auth,
        json={**payload, "use_skills": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed", body["stop_reason"]
    bundle = body["input_snapshot"]["skills"]
    assert [s["skill_id"] for s in bundle["loaded"]] == ["configuration-investigation"]
    assert body["skills"] == bundle
    assert all(c["skills"] == bundle for c in model[1] if c.get("phase") == "plan")
    assert all("skills" not in c for c in model[1] if c.get("phase") != "plan")
    assert all(not e["evidence_id"].startswith("skill:") for e in body["evidence"])
    assert body["tool_calls"] <= 6 and body["model_calls"] <= 8
    assert any(e["event"] == "skills_loaded" for e in body["events"])
    identity = body["investigation_id"]
    from supportops.skills import catalog

    def changed_release(*args):
        raise AssertionError("历史读取不应该重新加载当前发布文件")

    monkeypatch.setattr(catalog.Catalog, "__init__", changed_release)
    assert context["client"].get(f"/api/investigations/{identity}", headers=auth).json() == body
    other = headers(context, "username_b")
    assert (
        context["client"].get(f"/api/investigations/{identity}", headers=other).status_code == 404
    )


def test_req1504_old_investigation_does_not_enter_loader(context, embedding, model, monkeypatch):
    from supportops.skills.catalog import Catalog

    def forbidden(*args):
        raise AssertionError("关闭时不加载目录")

    monkeypatch.setattr(Catalog, "prepare", forbidden)
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations", headers=auth, json=payload
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert "skills" not in response.json()


def test_req1504_skill_never_bypasses_current_evidence(context, embedding, model):
    model[0].fault = "skip_live"
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
        headers=auth,
        json={**payload, "use_skills": True},
    )
    assert response.status_code == 201
    assert response.json()["stop_reason"] == "HYPOTHESIS_OBSERVATIONS_REQUIRED"


def test_req1504_skill_context_consumes_original_budget(context, embedding, model, monkeypatch):
    from supportops.investigations.live_guard import LiveGate

    original = LiveGate.result
    sizes = []

    def measured(self, value):
        original(self, value)
        sizes.append(self.chars)

    monkeypatch.setattr(LiveGate, "result", measured)
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
        headers=auth,
        json={**payload, "use_skills": True, "max_model_calls": 1},
    )
    assert response.status_code == 201
    assert response.json()["stop_reason"] == "model_budget"
    assert sizes[0] > 0 and max(sizes) <= 16000


def test_req1504_enabled_skill_cannot_change_tool_scope(context, embedding, model):
    model[0].fault = "scope"
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
        headers=auth,
        json={**payload, "use_skills": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["stop_reason"] == "MODEL_RESPONSE_INVALID"
    assert body["tool_calls"] == 1


def test_req1504_missing_registration_clarifies_without_skill_or_model(context, embedding, model):
    auth = headers(context)
    ticket, payload = setup(context, auth)
    response = context["client"].post(
        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
        headers=auth,
        json={"index_id": payload["index_id"], "use_skills": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert body["model_calls"] == 0 and model[1] == []
    assert "skills" not in body and "skills" not in body["input_snapshot"]
