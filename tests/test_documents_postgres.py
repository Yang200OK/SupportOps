"""真实 PostgreSQL 验证导入、原文字节、组织隔离与修订。"""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

pytestmark = pytest.mark.integration


def payload(source=b"# Config\n\nRD_TIMEOUT timeout_ms=2000\n", **changes):
    return {
        "source_key": "config-guide",
        "title": "配置说明",
        "product": "relaydesk",
        "product_version": "1.1",
        "source_type": "demo_product",
        "license": "CC0-1.0",
        "filename": "config.md",
        "format": "md",
        "content_base64": base64.b64encode(source).decode(),
        **changes,
    }


def post(context, auth, data=None):
    return context["client"].post("/api/documents/import", headers=auth, json=data or payload())


def test_req304_idempotent_revision_and_original_history(context):
    auth = headers(context)
    first = post(context, auth)
    assert first.status_code == 200
    one = first.json()
    repeat = post(context, auth).json()
    assert repeat["reused"] is True and repeat["revision_id"] == one["revision_id"]
    changed = post(context, auth, payload(b"# Updated\n\nRD_TIMEOUT timeout_ms=3000\n")).json()
    assert changed["document_id"] == one["document_id"]
    assert changed["revision_number"] == 2 and changed["revision_id"] != one["revision_id"]
    assert post(context, auth).json()["revision_id"] == one["revision_id"]
    client = context["client"]
    listing = client.get("/api/documents", headers=auth).json()
    assert listing["total"] == 1 and listing["items"][0]["revision_id"] == changed["revision_id"]
    original = client.get(
        f"/api/documents/{one['document_id']}/revisions/{one['revision_id']}/original", headers=auth
    )
    assert original.content == base64.b64decode(payload()["content_base64"])
    assert hashlib.sha256(original.content).hexdigest() == one["content_sha256"]
    history = client.get(f"/api/documents/{one['document_id']}/revisions", headers=auth).json()
    assert [x["revision_number"] for x in history["items"]] == [2, 1]


def test_req305_same_org_shared_cross_org_and_raw_are_404(context):
    auth = headers(context)
    item = post(context, auth).json()
    path = f"/api/documents/{item['document_id']}/revisions/{item['revision_id']}"
    client = context["client"]
    assert client.get(path, headers=headers(context, "colleague")).status_code == 200
    other = headers(context, "username_b")
    assert client.get("/api/documents", headers=other).json()["total"] == 0
    missing = f"/api/documents/{uuid4()}/revisions/{uuid4()}"
    assert client.get(path, headers=other).json() == client.get(missing, headers=other).json()
    assert client.get(path + "/original", headers=other).status_code == 404
    assert (
        client.get(f"/api/documents/{item['document_id']}/revisions", headers=other).status_code
        == 404
    )


def test_req306_failed_parse_is_saved_without_usable_text(context):
    auth = headers(context)
    item = post(context, auth, payload(b"%PDF-broken", format="pdf", filename="broken.pdf")).json()
    assert item["status"] == "failed" and item["error_code"] == "INVALID_PDF"
    assert item["text"] is None and item["blocks"] == []
    assert post(context, auth, payload(b"%PDF-broken", format="pdf", filename="broken.pdf")).json()[
        "reused"
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "forged"},
        {"filename": "../a.md"},
        {"product_version": "9.9"},
        {"format": "exe"},
        {"content_base64": "invalid*"},
    ],
)
def test_req302_invalid_request_never_writes(context, changes):
    auth = headers(context)
    assert post(context, auth, payload(**changes)).status_code == 422
    assert context["client"].get("/api/documents", headers=auth).json()["total"] == 0


def test_req304_source_identity_cannot_be_rewritten(context):
    auth = headers(context)
    post(context, auth)
    assert post(context, auth, payload(license="proprietary")).status_code == 409
    assert (
        context["client"].get("/api/documents", headers=auth).json()["items"][0]["revision_number"]
        == 1
    )


def test_req304_concurrent_reimports_are_one_revision(context):
    auth = headers(context)
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: post(context, auth), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["revision_id"] for r in responses}) == 1
    assert sum(not r.json()["reused"] for r in responses) == 1


def test_req305_rls_immutable_and_transaction_scope(context):
    post(context, headers(context))
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM document_revisions")) == 0
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(context["organization_a"])},
            )
            assert connection.scalar(text("SELECT count(*) FROM document_revisions")) == 1
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM document_revisions")) == 0
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE document_revisions SET status='failed'"))
    finally:
        engine.dispose()


def test_req305_response_failure_rolls_back(context, monkeypatch):
    from supportops.documents import service

    auth = headers(context)

    def broken(*args, **kwargs):
        raise RuntimeError("构造响应失败")

    monkeypatch.setattr(service, "view", broken)
    with pytest.raises(RuntimeError, match="构造响应失败"):
        post(context, auth)
    assert context["client"].get("/api/documents", headers=auth).json()["total"] == 0


def test_req302_versions_pagination_and_auth(context):
    auth = headers(context)
    for version in ("1.0", "1.1", "2.0"):
        assert post(context, auth, payload(product_version=version)).status_code == 200
    client = context["client"]
    assert client.get("/api/documents?product_version=1.0", headers=auth).json()["total"] == 1
    first = client.get("/api/documents?limit=2", headers=auth).json()
    second = client.get("/api/documents?limit=2&offset=2", headers=auth).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents?product_version=9.9", headers=auth).status_code == 422


def test_req302_request_body_limit_and_raw_download(context):
    auth = headers(context)
    client = context["client"]
    response = client.post("/api/documents/import", headers=auth, content=b"x" * (3145728 + 1))
    assert response.status_code == 413
    assert client.get("/api/documents", headers=auth).json()["total"] == 0
    item = post(context, auth).json()
    response = client.get(
        f"/api/documents/{item['document_id']}/revisions/{item['revision_id']}/raw", headers=auth
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["content_base64"] == payload()["content_base64"]


def test_req305_database_cross_org_importer_fk_is_rejected(context):
    item = post(context, headers(context)).json()
    with pytest.raises(DBAPIError):
        with context["admin"].begin() as connection:
            connection.execute(
                text("UPDATE document_revisions SET organization_id=:org WHERE id=:id"),
                {"org": context["organization_b"], "id": item["revision_id"]},
            )
