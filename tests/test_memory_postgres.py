"""真实数据库下的候选、冲突、召回与旧调查协议。"""

from uuid import uuid4

import pytest
from test_hypothesis_postgres import ask, setup
from test_hypothesis_postgres import model as model
from test_retrieval_postgres import embedding as embedding
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def prepare(context, embedding, model):
    auth = headers(context)
    ticket, request = setup(context, auth)
    investigation = ask(context, auth, ticket, request).json()
    assert investigation["status"] == "completed"
    return auth, ticket, request, investigation


def retrospective(context, auth, investigation, request_id=None):
    return context["client"].post(
        f"/api/investigations/{investigation['investigation_id']}/retrospectives",
        headers=auth,
        json={"request_id": str(request_id or uuid4())},
    )


def test_req1602_retrospective_idempotent_and_scoped(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    key = uuid4()
    result = retrospective(context, auth, inv, key)
    assert result.status_code == 201
    body = result.json()
    assert body["candidate"]["status"] == "candidate"
    assert body["model_calls"] == 0
    assert retrospective(context, auth, inv, key).json() == body
    url = f"/api/retrospectives/{body['retrospective_id']}"
    assert context["client"].get(url, headers=auth).json() == body
    assert context["client"].get(url, headers=headers(context, "username_b")).status_code == 404


def test_req1603_revoke_blocks_recall_and_freezes_history(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    result = retrospective(context, auth, inv)
    assert result.status_code == 201
    candidate = result.json()["candidate"]
    client = context["client"]
    recall = f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode=startup"
    assert len(client.get(recall, headers=auth).json()["loaded"]) == 1
    path = f"/api/experiences/{candidate['candidate_id']}/decisions"
    decision = {"revision": 1, "decision": "revoke", "reason": "人工确认不应继续参考"}
    assert client.post(path, headers=auth, json=decision).status_code == 200
    assert client.get(recall, headers=auth).json()["loaded"] == []
    assert client.post(path, headers=auth, json=decision).status_code == 409
    saved = client.get(
        f"/api/retrospectives/{result.json()['retrospective_id']}", headers=auth
    ).json()
    assert saved["source_sha256"] == result.json()["source_sha256"]
    assert saved["candidate"]["status"] == "revoked"


def test_req1603_conflict_both_block_then_invalidate_releases_partner(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    client = context["client"]
    first = retrospective(context, auth, inv).json()["candidate"]
    second = retrospective(context, auth, inv).json()["candidate"]
    path = f"/api/experiences/{first['candidate_id']}/conflicts"
    payload = {
        "other_id": second["candidate_id"],
        "revision": 1,
        "other_revision": 1,
        "reason": "人工标注两条方法适用建议存在分歧",
    }
    result = client.post(path, headers=auth, json=payload)
    assert result.status_code == 200
    assert all(
        c["eligibility"] == "conflicted" and c["revision"] == 2 for c in result.json()["items"]
    )
    recall = f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode=startup"
    assert client.get(recall, headers=auth).json()["loaded"] == []
    invalidated = client.post(
        f"/api/experiences/{first['candidate_id']}/decisions",
        headers=auth,
        json={"revision": 2, "decision": "invalidate", "reason": "不适用"},
    )
    assert invalidated.status_code == 200
    loaded = client.get(recall, headers=auth).json()["loaded"]
    assert [c["candidate_id"] for c in loaded] == [second["candidate_id"]]


def test_req1604_scope_expiry_and_source_drift(context, embedding, model, monkeypatch):
    from datetime import timedelta

    from sqlalchemy import text

    from supportops.chunks.chunking import digest
    from supportops.memory import service

    auth, ticket, request, inv = prepare(context, embedding, model)
    assert retrospective(context, auth, inv).status_code == 201
    client = context["client"]
    path = f"/api/tickets/{ticket['ticket_id']}/memory-recall"
    assert client.get(path + "?mode=online", headers=auth).json()["loaded"] == []
    assert (
        client.get(path + "?mode=startup", headers=headers(context, "username_b")).status_code
        == 404
    )
    future = service.now() + timedelta(days=8)
    with monkeypatch.context() as patch:
        patch.setattr(service, "now", lambda: future)
        assert client.get(path + "?mode=startup", headers=auth).json()["loaded"] == []
    # 管理员故意损坏测试数据，公开接口不能把改写后的记录当作可靠来源。
    changed = {**inv["input_snapshot"], "tools": []}
    with context["admin"].begin() as connection:
        connection.execute(
            text(
                "UPDATE investigations SET input_snapshot=CAST(:body AS jsonb), "
                "input_sha256=:sha WHERE id=:id"
            ),
            {
                "id": inv["investigation_id"],
                "body": __import__("json").dumps(changed),
                "sha": digest(changed),
            },
        )
    response = client.get(path + "?mode=startup", headers=auth)
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "MEMORY_SOURCE_CHANGED"
    )


def test_req1602_key_cannot_change_parameters_and_failed_only_summary(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    key = uuid4()
    assert retrospective(context, auth, inv, key).status_code == 201
    client = context["client"]
    response = client.post(
        f"/api/investigations/{inv['investigation_id']}/retrospectives",
        headers=auth,
        json={"request_id": str(key), "expires_in_days": 2},
    )
    assert response.status_code == 409
    model[0].fault = "timeout"
    failed = ask(context, auth, ticket, request).json()
    assert failed["status"] == "failed"
    result = retrospective(context, auth, failed)
    assert result.status_code == 201 and result.json()["candidate"] is None


def test_req1605_planning_only_default_off_and_saved_history(
    context, embedding, model, monkeypatch
):
    from supportops.memory import service

    auth, ticket, request, inv = prepare(context, embedding, model)
    candidate = retrospective(context, auth, inv).json()["candidate"]
    original = service.recall

    def forbidden(*args):
        raise AssertionError("默认调查不能读取记忆")

    monkeypatch.setattr(service, "recall", forbidden)
    model[1].clear()
    old = ask(context, auth, ticket, request).json()
    assert old["status"] == "completed" and "memory" not in old
    monkeypatch.setattr(service, "recall", original)
    model[1].clear()
    body = ask(context, auth, ticket, {**request, "use_memory": True}).json()
    assert body["status"] == "completed", body["stop_reason"]
    assert body["memory"] == body["input_snapshot"]["memory"]
    assert body["memory"]["loaded"][0]["candidate_id"] == candidate["candidate_id"]
    assert all(c["memory"] == body["memory"] for c in model[1] if c.get("phase") == "plan")
    assert all("memory" not in c for c in model[1] if c.get("phase") != "plan")
    assert not any(e["evidence_id"] == candidate["candidate_id"] for e in body["evidence"])
    context["client"].post(
        f"/api/experiences/{candidate['candidate_id']}/decisions",
        headers=auth,
        json={"revision": 1, "decision": "revoke", "reason": "停止参考"},
    )
    read = context["client"].get(f"/api/investigations/{body['investigation_id']}", headers=auth)
    assert read.json() == body


def test_req1605_revocation_between_plans_stops_without_replacement(
    context, embedding, model, monkeypatch
):
    from sqlalchemy import text

    from supportops.chunks.chunking import digest

    auth, ticket, request, inv = prepare(context, embedding, model)
    candidate = retrospective(context, auth, inv).json()["candidate"]
    original = model[0].post
    count = 0

    def revoke_during_model(self, url, data):
        nonlocal count
        result = original(self, url, data)
        if '"memory"' in data["messages"][-1]["content"]:
            count += 1
            if count == 1:
                # 独立已提交事务模拟同组织用户在第一轮规划期间撤销。
                event = {
                    "revision": 2,
                    "event": "revoke",
                    "status": "revoked",
                    "reason": "并发撤销",
                    "user_id": str(context["user_a"]),
                    "at": "test",
                }
                with context["admin"].begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE experience_candidates SET status='revoked', "
                            "revision=2 WHERE id=:id"
                        ),
                        {"id": candidate["candidate_id"]},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO memory_events VALUES "
                            "(:id,2,:org,CAST(:data AS jsonb),:sha)"
                        ),
                        {
                            "id": candidate["candidate_id"],
                            "org": context["organization_a"],
                            "data": __import__("json").dumps(event),
                            "sha": digest(event),
                        },
                    )
        return result

    monkeypatch.setattr(model[0], "post", revoke_during_model)
    body = ask(context, auth, ticket, {**request, "use_memory": True}).json()
    assert body["status"] == "failed" and body["stop_reason"] == "MEMORY_RECALL_CHANGED"
    assert count == 1 and body["report"] is None


def test_req1605_memory_never_bypasses_current_evidence(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    retrospective(context, auth, inv)
    model[0].fault = "skip_live"
    body = ask(context, auth, ticket, {**request, "use_memory": True}).json()
    assert body["stop_reason"] == "HYPOTHESIS_OBSERVATIONS_REQUIRED"


def test_req1602_database_immutable_content_and_rls(context, embedding, model):
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    auth, ticket, request, inv = prepare(context, embedding, model)
    body = retrospective(context, auth, inv).json()
    candidate = body["candidate"]
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM retrospectives")) == 0
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(context["organization_b"])},
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM retrospectives WHERE id=:id"),
                    {"id": body["retrospective_id"]},
                )
                == 0
            )
        for statement, identity in [
            (
                "UPDATE retrospectives SET summary='{}'::jsonb WHERE id=:id",
                body["retrospective_id"],
            ),
            (
                "UPDATE experience_candidates SET body='{}'::jsonb WHERE id=:id",
                candidate["candidate_id"],
            ),
            ("DELETE FROM experience_candidates WHERE id=:id", candidate["candidate_id"]),
        ]:
            with pytest.raises(DBAPIError):
                with engine.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('app.organization_id', :org, true)"),
                        {"org": str(context["organization_a"])},
                    )
                    connection.execute(text(statement), {"id": identity})
    finally:
        engine.dispose()
    assert (
        context["client"]
        .get(f"/api/retrospectives/{body['retrospective_id']}", headers=auth)
        .json()
        == body
    )


def test_req1604_version_match_limit_and_no_word_match(context, embedding, model):
    auth, ticket, request, inv = prepare(context, embedding, model)
    for _ in range(3):
        assert retrospective(context, auth, inv).status_code == 201
    client = context["client"]
    body = client.get(
        f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode=startup", headers=auth
    ).json()
    assert len(body["loaded"]) == 2 and body["selection_limited"]
    assert (
        sum(
            len(__import__("json").dumps(c, ensure_ascii=False, sort_keys=True))
            for c in body["loaded"]
        )
        <= 6000
    )
    for version in ("2.0", "1.1"):
        new_ticket = client.post(
            "/api/tickets",
            headers=auth,
            json={
                "title": "zzzzquux",
                "description": "zzzzquux",
                "product": "relaydesk",
                "product_version": version,
                "environment": "local_lab",
                "source_type": "synthetic_case",
            },
        ).json()
        assert (
            client.get(
                f"/api/tickets/{new_ticket['ticket_id']}/memory-recall?mode=startup", headers=auth
            ).json()["loaded"]
            == []
        )


def test_req1603_governance_authentication_and_tampered_audit(context, embedding, model):
    from sqlalchemy import text

    auth, ticket, request, inv = prepare(context, embedding, model)
    body = retrospective(context, auth, inv).json()
    candidate = body["candidate"]
    client = context["client"]
    url = f"/api/experiences/{candidate['candidate_id']}/decisions"
    decision = {"revision": 1, "decision": "revoke", "reason": "核对失效"}
    assert client.post(url, json=decision).status_code == 401
    assert (
        client.post(url, headers=headers(context, "username_b"), json=decision).status_code == 404
    )
    assert client.post(url, headers=auth, json={**decision, "reason": " "}).status_code == 422
    with context["admin"].begin() as connection:
        connection.execute(
            text("UPDATE memory_events SET data='{}'::jsonb WHERE candidate_id=:id"),
            {"id": candidate["candidate_id"]},
        )
    result = client.post(url, headers=auth, json=decision)
    assert result.status_code == 409 and result.json()["error"]["code"] == "MEMORY_INTEGRITY_FAILED"


def test_req1605_memory_context_consumes_original_budget(context, embedding, model, monkeypatch):
    from supportops.investigations.live_guard import LiveGate

    auth, ticket, request, inv = prepare(context, embedding, model)
    retrospective(context, auth, inv)
    original = LiveGate.result
    sizes = []

    def measured(self, value):
        original(self, value)
        sizes.append(self.chars)

    monkeypatch.setattr(LiveGate, "result", measured)
    body = ask(context, auth, ticket, {**request, "use_memory": True, "max_model_calls": 1}).json()
    assert body["stop_reason"] == "model_budget" and body["model_calls"] == 0
    assert sizes[0] > 0 and max(sizes) <= 16000
