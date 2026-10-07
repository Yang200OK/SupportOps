"""真实数据库的切片快照、引用和原组织边界。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_documents_postgres import payload, post
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration
CONFIG = {"max_chars": 128, "overlap_chars": 20}


def setup(context, auth, source=b"# Configuration\n\nRD_TIMEOUT timeout_ms=2000\n"):
    revision = post(context, auth, payload(source)).json()
    path = (
        f"/api/documents/{revision['document_id']}/revisions/{revision['revision_id']}/chunk-sets"
    )
    return revision, path


def create(context, auth):
    revision, path = setup(context, auth)
    response = context["client"].post(path, headers=auth, json=CONFIG)
    assert response.status_code == 200
    return revision, path, response.json()


def test_req401_idempotency_parameters_and_list(context):
    auth = headers(context)
    _, path, one = create(context, auth)
    same = context["client"].post(path, headers=auth, json=CONFIG).json()
    assert same["reused"] and same["chunk_set_id"] == one["chunk_set_id"]
    changed = (
        context["client"]
        .post(path, headers=auth, json={"max_chars": 256, "overlap_chars": 20})
        .json()
    )
    assert changed["chunk_set_id"] != one["chunk_set_id"]
    assert context["client"].get(path, headers=auth).json()["total"] == 2


def test_req405_citation_and_old_revision_remain_exact(context):
    auth = headers(context)
    revision, _, one = create(context, auth)
    client = context["client"]
    chunks = client.get(f"/api/chunk-sets/{one['chunk_set_id']}/chunks", headers=auth).json()
    assert chunks["total"] > 0
    chunk = chunks["items"][-1]
    path = f"/api/chunk-sets/{one['chunk_set_id']}/chunks/{chunk['chunk_id']}/citation"
    citation = client.get(path, headers=auth).json()
    assert citation["text_verified"] and citation["support_verified"] is False
    assert citation["source"]["revision_id"] == revision["revision_id"]
    assert "".join(p["excerpt"] for p in citation["parts"]) == chunk["text"]
    post(context, auth, payload(b"# Updated\n\nRD_TIMEOUT timeout_ms=3000\n"))
    assert client.get(path, headers=auth).json() == citation


def test_req406_cross_org_all_routes_and_unknown_citations_are_404(context):
    auth = headers(context)
    _, path, one = create(context, auth)
    client = context["client"]
    set_path = f"/api/chunk-sets/{one['chunk_set_id']}/chunks"
    chunk = client.get(set_path, headers=auth).json()["items"][0]
    citation_path = set_path + f"/{chunk['chunk_id']}/citation"
    colleague = headers(context, "colleague")
    other = headers(context, "username_b")
    assert client.get(citation_path, headers=colleague).status_code == 200
    assert client.get(path, headers=other).status_code == 404
    assert client.post(path, headers=other, json=CONFIG).status_code == 404
    assert client.get(set_path, headers=other).status_code == 404
    unknown = f"/api/chunk-sets/{uuid4()}/chunks/{uuid4()}/citation"
    assert (
        client.get(citation_path, headers=other).json() == client.get(unknown, headers=other).json()
    )
    assert client.get(set_path).status_code == 401


def test_req401_failed_revision_never_creates_chunk_set(context):
    auth = headers(context)
    _, path = setup(context, auth, b"\xff")
    response = context["client"].post(path, headers=auth, json=CONFIG)
    assert response.status_code == 409
    assert context["client"].get(path, headers=auth).json()["total"] == 0


def test_req406_concurrent_same_config_is_one_snapshot(context):
    auth = headers(context)
    _, path = setup(context, auth)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda _: context["client"].post(path, headers=auth, json=CONFIG), range(4)
            )
        )
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["chunk_set_id"] for r in responses}) == 1
    assert sum(not r.json()["reused"] for r in responses) == 1


def test_req406_rls_immutable_and_fk(context):
    auth = headers(context)
    _, _, one = create(context, auth)
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM chunk_sets")) == 0
            c.execute(
                text("SELECT set_config('app.organization_id',:org,true)"),
                {"org": str(context["organization_a"])},
            )
            assert c.scalar(text("SELECT count(*) FROM chunk_sets")) == 1
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text("UPDATE chunk_sets SET chunker_version='forged'"))
        with pytest.raises(DBAPIError):
            with context["admin"].begin() as c:
                c.execute(
                    text("UPDATE chunk_sets SET organization_id=:org WHERE id=:id"),
                    {"org": context["organization_b"], "id": one["chunk_set_id"]},
                )
    finally:
        engine.dispose()


def test_req405_parsed_text_tampering_is_rejected(context):
    auth = headers(context)
    revision, _, one = create(context, auth)
    client = context["client"]
    chunk = client.get(f"/api/chunk-sets/{one['chunk_set_id']}/chunks", headers=auth).json()[
        "items"
    ][0]
    with context["admin"].begin() as c:
        c.execute(
            text("UPDATE document_revisions SET text=text || 'tampered' WHERE id=:id"),
            {"id": revision["revision_id"]},
        )
    response = client.get(
        f"/api/chunk-sets/{one['chunk_set_id']}/chunks/{chunk['chunk_id']}/citation", headers=auth
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "CITATION_INTEGRITY_FAILED"
    )


def test_req406_response_failure_rolls_back_snapshot(context, monkeypatch):
    from supportops.chunks import service

    auth = headers(context)
    _, path = setup(context, auth)

    def broken(*args, **kwargs):
        raise RuntimeError("切片响应失败")

    monkeypatch.setattr(service, "view", broken)
    with pytest.raises(RuntimeError, match="切片响应失败"):
        context["client"].post(path, headers=auth, json=CONFIG)
    assert context["client"].get(path, headers=auth).json()["total"] == 0


@pytest.mark.parametrize("target", ["raw", "snapshot", "invalid_snapshot"])
def test_req405_original_and_snapshot_tampering_are_rejected(context, target):
    auth = headers(context)
    revision, _, one = create(context, auth)
    client = context["client"]
    chunk = client.get(f"/api/chunk-sets/{one['chunk_set_id']}/chunks", headers=auth).json()[
        "items"
    ][0]
    with context["admin"].begin() as c:
        if target == "raw":
            c.execute(
                text(
                    "UPDATE document_revisions SET raw_bytes=set_byte(raw_bytes,0,33) WHERE id=:id"
                ),
                {"id": revision["revision_id"]},
            )
        else:
            expression = "jsonb_set(chunks,'{0,text}',to_jsonb('tampered'::text))"
            if target == "invalid_snapshot":
                expression = "jsonb_set(chunks,'{0,ordinal}',to_jsonb('invalid'::text))"
            c.execute(
                text(f"UPDATE chunk_sets SET chunks={expression} WHERE id=:id"),
                {"id": one["chunk_set_id"]},
            )
    response = client.get(
        f"/api/chunk-sets/{one['chunk_set_id']}/chunks/{chunk['chunk_id']}/citation", headers=auth
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CITATION_INTEGRITY_FAILED"


def test_req405_chunk_cannot_be_mixed_with_another_snapshot(context):
    auth = headers(context)
    _, path, one = create(context, auth)
    client = context["client"]
    other = client.post(path, headers=auth, json={"max_chars": 256, "overlap_chars": 20}).json()
    chunk = client.get(f"/api/chunk-sets/{one['chunk_set_id']}/chunks", headers=auth).json()[
        "items"
    ][0]
    assert (
        client.get(
            f"/api/chunk-sets/{other['chunk_set_id']}/chunks/{chunk['chunk_id']}/citation",
            headers=auth,
        ).status_code
        == 404
    )
