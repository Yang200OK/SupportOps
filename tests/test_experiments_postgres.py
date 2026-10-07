"""公共观测只能作为所属组织的不可变实验记录读取。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_lab_contracts import bundle
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def test_req506_experiment_import_route_exists(context):
    response = context["client"].post("/api/experiments/import", headers=headers(context), json={})
    assert response.status_code == 422


def payload():
    value = bundle()
    return {"artifact": value.model_dump(mode="json", exclude_none=True), "sha256": value.digest()}


def test_req506_import_idempotency_conflict_and_integrity(context):
    client, auth, data = context["client"], headers(context), payload()
    one = client.post("/api/experiments/import", headers=auth, json=data)
    assert one.status_code == 200
    same = client.post("/api/experiments/import", headers=auth, json=data).json()
    assert same["reused"] and same["experiment_id"] == one.json()["experiment_id"]
    path = "/api/experiments/" + same["experiment_id"]
    detail = client.get(path, headers=auth).json()
    assert detail["text_verified"] and detail["execution_verified_by_api"] is False
    assert len(detail["evidence_ids"]) == 3
    value = bundle().model_copy(update={"run_id": UUID(data["artifact"]["run_id"])})
    changed = {
        "artifact": value.model_dump(mode="json", exclude_none=True),
        "sha256": value.digest(),
    }
    assert client.post("/api/experiments/import", headers=auth, json=changed).status_code == 409
    with context["admin"].begin() as c:
        c.execute(
            text(
                "UPDATE experiments SET artifact=jsonb_set(artifact,"
                "'{observations,0,elapsed_ms}', '77') WHERE id=:id"
            ),
            {"id": same["experiment_id"]},
        )
    assert client.get(path, headers=auth).status_code == 409


def test_req506_same_org_cross_org_and_labels(context):
    client, auth, data = context["client"], headers(context), payload()
    record = client.post("/api/experiments/import", headers=auth, json=data).json()
    path = "/api/experiments/" + record["experiment_id"]
    assert client.get(path, headers=headers(context, "colleague")).status_code == 200
    other = headers(context, "username_b")
    assert client.get(path, headers=other).status_code == 404
    assert (
        client.get("/api/experiments/" + str(uuid4()), headers=other).json()
        == client.get(path, headers=other).json()
    )
    assert client.get("/api/experiments", headers=other).json()["total"] == 0
    assert client.get(path).status_code == 401
    data["artifact"]["expected"] = {"root_cause": "forged"}
    assert client.post("/api/experiments/import", headers=auth, json=data).status_code == 422


def test_req506_concurrent_import_one_record(context):
    client, auth, data = context["client"], headers(context), payload()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/api/experiments/import", headers=auth, json=data), range(4)
            )
        )
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["experiment_id"] for r in responses}) == 1
    assert sum(not r.json()["reused"] for r in responses) == 1


def test_req506_rls_immutable_and_requester_scope(context):
    auth = headers(context)
    record = context["client"].post("/api/experiments/import", headers=auth, json=payload()).json()
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM experiments")) == 0
            c.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_a"])},
            )
            assert c.scalar(text("SELECT count(*) FROM experiments")) == 1
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text("DELETE FROM experiments"))
        with pytest.raises(DBAPIError):
            with context["admin"].begin() as c:
                c.execute(
                    text("UPDATE experiments SET organization_id=:org WHERE id=:id"),
                    {"org": context["organization_b"], "id": record["experiment_id"]},
                )
    finally:
        engine.dispose()


def test_req506_failed_response_rolls_back(context, monkeypatch):
    from supportops.experiments import service

    data, auth = payload(), headers(context)

    def failed(*args, **kwargs):
        raise RuntimeError("观测响应失败")

    monkeypatch.setattr(service, "view", failed)
    with pytest.raises(RuntimeError, match="观测响应失败"):
        context["client"].post("/api/experiments/import", headers=auth, json=data)
    assert context["client"].get("/api/experiments", headers=auth).json()["total"] == 0
