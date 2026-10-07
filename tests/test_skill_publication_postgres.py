"""真正的 PostgreSQL、MCP 和 API 发布流程；模型替身只在测试侧。"""

from uuid import uuid4

import pytest
from test_hypothesis_postgres import ask
from test_hypothesis_postgres import model as model
from test_memory_postgres import prepare, retrospective
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def approve_publish(context, auth, row):
    client = context["client"]
    path = f"/api/skill-drafts/{row['draft_id']}"
    report = client.post(path + "/regressions", headers=auth).json()
    approval = {
        "request_id": str(uuid4()),
        "revision": 1,
        "decision": "approve",
        "report_id": report["report_id"],
        "payload_sha256": row["payload_sha256"],
        "reason": "测试操作者",
    }
    assert client.post(path + "/decisions", headers=auth, json=approval).status_code == 200
    result = client.post(
        path + "/publish", headers=auth, json={"revision": 2, "reason": "测试显式发布"}
    )
    assert result.status_code == 200
    return result.json(), report, approval


def draft(context, auth, inv):
    c = retrospective(context, auth, inv).json()["candidate"]
    request = {
        "request_id": str(uuid4()),
        "candidate_id": c["candidate_id"],
        "candidate_revision": c["revision"],
    }
    return context["client"].post("/api/skill-drafts", headers=auth, json=request), c, request


def test_req1701_draft_idempotent_scope_and_source(context, embedding, model):
    auth, _, _, inv = prepare(context, embedding, model)
    response, candidate, request = draft(context, auth, inv)
    assert response.status_code == 201
    body = response.json()
    client = context["client"]
    assert client.post("/api/skill-drafts", headers=auth, json=request).json() == body
    changed = {**request, "candidate_revision": request["candidate_revision"] + 1}
    assert client.post("/api/skill-drafts", headers=auth, json=changed).status_code == 409
    assert (
        client.get(
            f"/api/skill-drafts/{body['draft_id']}", headers=headers(context, "username_b")
        ).status_code
        == 404
    )
    assert body["payload"]["method"]["role"] == "planning_guidance_only"
    assert body["payload"]["candidate_sha256"] == candidate["body_sha256"]


def test_req1702_approval_publish_revoke_and_history(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    response, _, _ = draft(context, auth, inv)
    assert response.status_code == 201
    row = response.json()
    path = f"/api/skill-drafts/{row['draft_id']}"
    client = context["client"]
    assert (
        client.post(
            path + "/publish", headers=auth, json={"revision": 1, "reason": "未批准"}
        ).status_code
        == 409
    )
    report = client.post(path + "/regressions", headers=auth).json()
    assert report["report"]["passed"] is True
    payload = {
        "request_id": str(uuid4()),
        "revision": 1,
        "decision": "approve",
        "report_id": report["report_id"],
        "payload_sha256": row["payload_sha256"],
        "reason": "测试操作者批准方法，非根因审定",
    }
    approved = client.post(path + "/decisions", headers=auth, json=payload)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.post(path + "/decisions", headers=auth, json=payload).json() == approved.json()
    assert (
        client.post(
            path + "/publish", headers=auth, json={"revision": 2, "reason": "显式发布"}
        ).json()["status"]
        == "published"
    )
    investigated = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert investigated["status"] == "completed"
    assert len(investigated["input_snapshot"]["skills"]["loaded"]) == 1
    assert any("skills" in call for call in model[1] if call.get("phase") == "plan")
    assert (
        client.post(
            path + "/revoke", headers=auth, json={"revision": 3, "reason": "停止未来使用"}
        ).json()["status"]
        == "revoked"
    )
    after = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert after["input_snapshot"]["skills"]["loaded"] == []
    assert (
        client.get(f"/api/investigations/{investigated['investigation_id']}", headers=auth).json()
        == investigated
    )


@pytest.mark.parametrize("state", ["revoke", "invalidate", "expired", "conflict"])
def test_req1701_ineligible_candidate_cannot_become_draft(
    context, embedding, model, monkeypatch, state
):
    from datetime import timedelta

    from supportops.memory import service

    auth, _, _, inv = prepare(context, embedding, model)
    c = retrospective(context, auth, inv).json()["candidate"]
    client = context["client"]
    if state in ("revoke", "invalidate"):
        assert (
            client.post(
                f"/api/experiences/{c['candidate_id']}/decisions",
                headers=auth,
                json={"revision": 1, "decision": state, "reason": "不适用"},
            ).status_code
            == 200
        )
    elif state == "expired":
        future = service.now() + timedelta(days=8)
        monkeypatch.setattr(service, "now", lambda: future)
    else:
        other = retrospective(context, auth, inv).json()["candidate"]
        assert (
            client.post(
                f"/api/experiences/{c['candidate_id']}/conflicts",
                headers=auth,
                json={
                    "revision": 1,
                    "other_revision": 1,
                    "other_id": other["candidate_id"],
                    "reason": "待处理冲突",
                },
            ).status_code
            == 200
        )
    result = client.post(
        "/api/skill-drafts",
        headers=auth,
        json={
            "request_id": str(uuid4()),
            "candidate_id": c["candidate_id"],
            "candidate_revision": 1,
        },
    )
    assert result.status_code == 409


def test_req1703_failed_source_report_saved_and_rejected(context, embedding, model):
    auth, _, _, inv = prepare(context, embedding, model)
    response, c, _ = draft(context, auth, inv)
    row = response.json()
    client = context["client"]
    assert (
        client.post(
            f"/api/experiences/{c['candidate_id']}/decisions",
            headers=auth,
            json={"revision": 1, "decision": "invalidate", "reason": "来源不可继续使用"},
        ).status_code
        == 200
    )
    path = f"/api/skill-drafts/{row['draft_id']}"
    report = client.post(path + "/regressions", headers=auth)
    assert report.status_code == 200 and report.json()["report"]["passed"] is False
    request = {
        "request_id": str(uuid4()),
        "revision": 1,
        "decision": "approve",
        "report_id": report.json()["report_id"],
        "payload_sha256": row["payload_sha256"],
        "reason": "不能批准",
    }
    assert client.post(path + "/decisions", headers=auth, json=request).status_code == 409
    assert (
        client.post(
            path + "/decisions",
            headers=auth,
            json={**request, "request_id": str(uuid4()), "decision": "reject"},
        ).json()["status"]
        == "rejected"
    )


def test_req1702_wrong_hash_report_and_stale_revision(context, embedding, model):
    auth, _, _, inv = prepare(context, embedding, model)
    first, _, _ = draft(context, auth, inv)
    second, _, _ = draft(context, auth, inv)
    row = first.json()
    path = f"/api/skill-drafts/{row['draft_id']}"
    client = context["client"]
    report = client.post(
        f"/api/skill-drafts/{second.json()['draft_id']}/regressions", headers=auth
    ).json()
    payload = {
        "request_id": str(uuid4()),
        "revision": 1,
        "decision": "approve",
        "report_id": report["report_id"],
        "payload_sha256": row["payload_sha256"],
        "reason": "报告范围",
    }
    assert client.post(path + "/decisions", headers=auth, json=payload).status_code == 404
    report = client.post(path + "/regressions", headers=auth).json()
    payload["report_id"] = report["report_id"]
    assert (
        client.post(
            path + "/decisions", headers=auth, json={**payload, "payload_sha256": "a" * 64}
        ).status_code
        == 409
    )
    assert (
        client.post(path + "/decisions", headers=auth, json={**payload, "revision": 2}).status_code
        == 409
    )


def test_req1704_source_revoke_blocks_load_without_rewriting_history(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    response, c, _ = draft(context, auth, inv)
    row, _, _ = approve_publish(context, auth, response.json())
    client = context["client"]
    old = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert len(old["skills"]["loaded"]) == 1
    assert (
        client.post(
            f"/api/experiences/{c['candidate_id']}/decisions",
            headers=auth,
            json={"revision": 1, "decision": "revoke", "reason": "来源撤销"},
        ).status_code
        == 200
    )
    changed = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert changed["skills"]["loaded"] == []
    assert changed["skills"]["excluded_publications"][0]["error"] == "SKILL_SOURCE_INELIGIBLE"
    assert client.get(f"/api/investigations/{old['investigation_id']}", headers=auth).json() == old
    assert (
        client.get(f"/api/skill-drafts/{row['draft_id']}", headers=auth).json()["eligibility"]
        == "SKILL_SOURCE_INELIGIBLE"
    )


def test_req1704_revocation_between_plans_stops(context, embedding, model, monkeypatch):
    import json

    from supportops.investigations import graph

    auth, ticket, request, inv = prepare(context, embedding, model)
    response, _, _ = draft(context, auth, inv)
    row, _, _ = approve_publish(context, auth, response.json())
    original = model[0].post
    revoked = False

    def post(self, url, body):
        nonlocal revoked
        if json.loads(body["messages"][1]["content"]).get("phase") == "plan" and not revoked:
            revoked = True
            result = context["client"].post(
                f"/api/skill-drafts/{row['draft_id']}/revoke",
                headers=auth,
                json={"revision": 3, "reason": "规划期间撤销"},
            )
            assert result.status_code == 200
        return original(self, url, body)

    monkeypatch.setattr(graph.Provider, "post", post)
    result = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert result["status"] == "failed" and result["stop_reason"] == "SKILL_PUBLICATION_CHANGED"
    assert len(result["input_snapshot"]["skills"]["loaded"]) == 1


def test_req1704_joint_limit_and_current_evidence_required(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    for _ in range(3):
        response, _, _ = draft(context, auth, inv)
        approve_publish(context, auth, response.json())
    ticket = (
        context["client"]
        .post(
            "/api/tickets",
            headers=auth,
            json={
                "title": "配置启动失败",
                "description": "RD_CONFIG_INVALID RD_TIMEOUT 两个症状均需核对",
                "product": "relaydesk",
                "product_version": "1.1",
                "environment": "local_lab",
                "source_type": "synthetic_case",
            },
        )
        .json()
    )
    result = ask(
        context, auth, ticket, {**request, "use_skills": True, "use_published_skills": True}
    ).json()
    assert len(result["skills"]["loaded"]) <= 2 and result["skills"]["selection_limited"]
    assert result["skills"]["loaded"][0]["skill_id"] == "configuration-investigation"
    model[0].fault = "skip_live"
    stopped = ask(context, auth, ticket, {**request, "use_published_skills": True}).json()
    assert (
        stopped["status"] == "failed"
        and stopped["stop_reason"] == "HYPOTHESIS_OBSERVATIONS_REQUIRED"
    )


def test_req1702_database_content_and_reports_immutable(context, embedding, model):
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    auth, _, _, inv = prepare(context, embedding, model)
    response, _, _ = draft(context, auth, inv)
    row, report, _ = approve_publish(context, auth, response.json())
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        for statement in (
            "UPDATE skill_drafts SET payload='{}' WHERE id=:id",
            "UPDATE skill_regressions SET report='{}' WHERE draft_id=:id",
            "UPDATE skill_decisions SET data='{}' WHERE draft_id=:id",
            "UPDATE skill_publication_events SET data='{}' WHERE draft_id=:id",
            "DELETE FROM skill_drafts WHERE id=:id",
            "UPDATE skill_drafts SET status='draft',revision=revision+1 WHERE id=:id",
        ):
            with pytest.raises(DBAPIError), engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(context["organization_a"])},
                )
                conn.execute(text(statement), {"id": row["draft_id"]})
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_b"])},
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM skill_drafts WHERE id=:id"), {"id": row["draft_id"]}
                ).scalar()
                == 0
            )
    finally:
        engine.dispose()


def test_req1702_concurrent_request_keys_and_revision_guard(context, embedding, model):
    from concurrent.futures import ThreadPoolExecutor

    auth, _, _, inv = prepare(context, embedding, model)
    c = retrospective(context, auth, inv).json()["candidate"]
    client = context["client"]
    request = {
        "request_id": str(uuid4()),
        "candidate_id": c["candidate_id"],
        "candidate_revision": c["revision"],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(
            pool.map(
                lambda _: client.post("/api/skill-drafts", headers=auth, json=request), range(2)
            )
        )
    assert [r.status_code for r in created] == [201, 201]
    assert created[0].json() == created[1].json()
    row = created[0].json()
    path = f"/api/skill-drafts/{row['draft_id']}"
    report = client.post(path + "/regressions", headers=auth).json()
    approval = {
        "request_id": str(uuid4()),
        "revision": 1,
        "decision": "approve",
        "report_id": report["report_id"],
        "payload_sha256": row["payload_sha256"],
        "reason": "并发请求的认证测试操作者",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        approved = list(
            pool.map(
                lambda _: client.post(path + "/decisions", headers=auth, json=approval), range(2)
            )
        )
    assert [r.status_code for r in approved] == [200, 200]
    assert approved[0].json() == approved[1].json()
    assert len(approved[0].json()["decisions"]) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        published = list(
            pool.map(
                lambda _: client.post(
                    path + "/publish", headers=auth, json={"revision": 2, "reason": "显式发布"}
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in published) == [200, 409]
    final = client.get(path, headers=auth).json()
    assert final["revision"] == 3 and len(final["events"]) == 3


def test_req1704_scope_projection_and_original_context_budget(context, embedding, model):
    from types import SimpleNamespace
    from uuid import UUID

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from supportops.skills import publication_service as service

    auth, ticket, request, inv = prepare(context, embedding, model)
    response, _, _ = draft(context, auth, inv)
    row, _, _ = approve_publish(context, auth, response.json())
    principal = SimpleNamespace(
        organization_id=context["organization_a"], user_id=context["user_a"]
    )
    other = (
        context["client"]
        .post(
            "/api/tickets",
            headers=auth,
            json={
                "title": ticket["title"],
                "description": ticket["description"],
                "product": "relaydesk",
                "product_version": "2.0",
                "environment": "local_lab",
                "source_type": "synthetic_case",
            },
        )
        .json()
    )
    with Session(context["client"].app.state.database.engine) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(principal.organization_id)},
        )
        assert (
            service.prepare(session, principal, UUID(other["ticket_id"]), "startup")["loaded"] == []
        )
        assert (
            service.prepare(session, principal, UUID(ticket["ticket_id"]), "online")["loaded"] == []
        )
        assert (
            service.prepare(session, principal, UUID(ticket["ticket_id"]), "startup")["loaded"][0][
                "publication_id"
            ]
            == row["draft_id"]
        )
    model[1].clear()
    result = ask(
        context, auth, ticket, {**request, "use_published_skills": True, "max_model_calls": 1}
    ).json()
    # 图为最终结论预留调用，小于预留预算时在首个规划调用前停止。
    assert result["stop_reason"] == "model_budget" and result["model_calls"] == 0
    assert result["input_snapshot"]["skills"]["loaded"]
