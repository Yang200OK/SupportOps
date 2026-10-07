"""新检索接口先证明行为缺失，后续用真实 PostgreSQL 验证。"""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_chunks_postgres import create
from test_documents_postgres import payload, post
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.models.provider import ModelFailure
from supportops.retrieval import service

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def embedding(monkeypatch):
    # 替身仅在测试侧验证数据库排序；真实模型质量另由 HTTP 脚本评测。
    monkeypatch.setenv("SUPPORTOPS_MODEL_EMBEDDING_DIMENSIONS", "3")
    calls = []

    class Model:
        fail = False

        def __init__(self, settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def embed_texts(self, texts):
            calls.append(texts)
            if Model.fail:
                raise ModelFailure("MODEL_TIMEOUT")
            return {
                "vectors": [[1, 0, 0] if "Connection" in t else [0, 1, 0] for t in texts],
                "input_tokens": len(texts) * 5,
                "requested_model": self.settings.embedding_model,
                "returned_model": self.settings.embedding_model,
                "dimensions": 3,
                "latency_ms": 1,
                "cost_cny": None,
            }

    monkeypatch.setattr(service, "Provider", Model)
    return Model, calls


def index(context, auth):
    _, _, snapshot = create(context, auth)
    response = context["client"].post(
        "/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": [snapshot["chunk_set_id"]]}
    )
    assert response.status_code == 200, response.text
    return response.json(), snapshot


def search(context, auth, record, **changes):
    return context["client"].post(
        f"/api/retrieval/indexes/{record['index_id']}/search",
        headers=auth,
        json={"query": "Connection", "product_version": "1.1", "top_k": 20, **changes},
    )


def test_req602_index_http_exists(context):
    auth = headers(context)
    _, _, snapshot = create(context, auth)
    response = context["client"].post(
        "/api/retrieval/indexes",
        headers=auth,
        json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
    )
    assert response.status_code == 200


def test_req603_same_sources_reuse_without_model_charge(context, embedding):
    auth = headers(context)
    record, snapshot = index(context, auth)
    count = len(embedding[1])
    same = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
        )
        .json()
    )
    assert same["reused"] and same["index_id"] == record["index_id"]
    assert len(embedding[1]) == count
    assert record["build_usage"]["cost_cny"] is None


def test_req604_exact_ranking_matches_full_python_cosine(context, embedding):
    auth = headers(context)
    sets = []
    for title in ("Connection", "Cache"):
        r = post(
            context,
            auth,
            payload(
                f"# {title}\n\n{title} evidence".encode(), title=title, source_key=title.lower()
            ),
        ).json()
        s = (
            context["client"]
            .post(
                f"/api/documents/{r['document_id']}/revisions/{r['revision_id']}/chunk-sets",
                headers=auth,
                json={},
            )
            .json()
        )
        sets.append(s["chunk_set_id"])
    record = (
        context["client"]
        .post("/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": sets})
        .json()
    )
    actual = search(context, auth, record).json()
    with context["admin"].connect() as c:
        rows = c.execute(
            text("SELECT evidence_id,embedding::text FROM retrieval_entries WHERE index_id=:id"),
            {"id": record["index_id"]},
        ).all()
    expected = []
    for evidence, raw in rows:
        vector = json.loads(raw)
        expected.append((evidence, vector[0] / math.sqrt(sum(x * x for x in vector))))
    expected.sort(key=lambda item: (-item[1], item[0]))
    assert [r["evidence_id"] for r in actual["items"]] == [r[0] for r in expected]
    assert [r["cosine_similarity"] for r in actual["items"]] == pytest.approx(
        [r[1] for r in expected]
    )
    assert all(r["text_verified"] and not r["support_verified"] for r in actual["items"])


@pytest.mark.parametrize(
    "change",
    [{"product_version": "2.0"}, {"source_kinds": ["log"]}, {"document_ids": [str(uuid4())]}],
)
def test_req604_empty_filtered_scope_has_no_model_call(context, embedding, change):
    auth = headers(context)
    record, _ = index(context, auth)
    embedding[0].fail = True
    count = len(embedding[1])
    result = search(context, auth, record, **change)
    assert result.status_code == 200
    assert result.json()["items"] == [] and result.json()["eligible_count"] == 0
    assert not result.json()["usage"]["model_called"] and len(embedding[1]) == count


def test_req606_all_read_routes_are_org_scoped(context):
    auth = headers(context)
    record, _ = index(context, auth)
    other, colleague = headers(context, "username_b"), headers(context, "colleague")
    path = f"/api/retrieval/indexes/{record['index_id']}"
    for suffix in ("", "/entries"):
        assert context["client"].get(path + suffix, headers=other).status_code == 404
        assert context["client"].get(path + suffix, headers=colleague).status_code == 200
    assert search(context, other, record).status_code == 404
    assert context["client"].get("/api/retrieval/indexes", headers=other).json()["total"] == 0
    assert context["client"].get(path).status_code == 401


@pytest.mark.parametrize(
    "setting,value",
    [
        ("EMBEDDING_MODEL", "different-model"),
        ("EMBEDDING_DIMENSIONS", "2"),
        ("BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ],
)
def test_req605_changed_model_requires_new_index(context, embedding, monkeypatch, setting, value):
    auth = headers(context)
    record, _ = index(context, auth)
    count = len(embedding[1])
    monkeypatch.setenv("SUPPORTOPS_MODEL_" + setting, value)
    result = search(context, auth, record)
    assert result.status_code == 409 and result.json()["error"]["code"] == "INDEX_MODEL_MISMATCH"
    assert len(embedding[1]) == count


def test_req603_model_failure_and_response_failure_roll_back(context, embedding, monkeypatch):
    auth = headers(context)
    _, _, snapshot = create(context, auth)
    body = {"chunk_set_ids": [snapshot["chunk_set_id"]]}
    embedding[0].fail = True
    result = context["client"].post("/api/retrieval/indexes", headers=auth, json=body)
    assert result.status_code == 503 and result.json()["error"]["code"] == "MODEL_TIMEOUT"
    embedding[0].fail = False

    def broken(*args, **kwargs):
        raise RuntimeError("索引响应失败")

    monkeypatch.setattr(service, "view", broken)
    with pytest.raises(RuntimeError, match="索引响应失败"):
        context["client"].post("/api/retrieval/indexes", headers=auth, json=body)
    with context["admin"].connect() as c:
        for table in ("retrieval_indexes", "retrieval_entries"):
            assert (
                c.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE organization_id=:org"),
                    {"org": context["organization_a"]},
                )
                == 0
            )


def test_req603_concurrent_build_publishes_one_snapshot(context, embedding):
    auth = headers(context)
    _, _, snapshot = create(context, auth)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(
                lambda _: context["client"].post(
                    "/api/retrieval/indexes",
                    headers=auth,
                    json={"chunk_set_ids": [snapshot["chunk_set_id"]]},
                ),
                range(3),
            )
        )
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["index_id"] for r in results}) == 1
    assert sum(not r.json()["reused"] for r in results) == 1


def test_req606_rls_and_immutable_tables(context):
    auth = headers(context)
    index(context, auth)
    engine = create_engine(context["app_url"], hide_parameters=True)
    try:
        for table in ("retrieval_indexes", "retrieval_entries"):
            with engine.begin() as c:
                assert c.scalar(text(f"SELECT count(*) FROM {table}")) == 0
                c.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(context["organization_a"])},
                )
                assert c.scalar(text(f"SELECT count(*) FROM {table}")) > 0
            with pytest.raises(DBAPIError):
                with engine.begin() as c:
                    c.execute(text(f"DELETE FROM {table}"))
    finally:
        engine.dispose()


def test_req606_tampered_original_stops_search(context):
    auth = headers(context)
    record, snapshot = index(context, auth)
    with context["admin"].begin() as c:
        c.execute(
            text("UPDATE document_revisions SET text=text || 'tamper' WHERE id=:id"),
            {"id": snapshot["revision_id"]},
        )
    actual = search(context, auth, record)
    assert actual.status_code == 409
    assert actual.json()["error"]["code"] == "CITATION_INTEGRITY_FAILED"


def test_req605_unknown_version_and_label_injection_are_rejected(context):
    auth = headers(context)
    record, _ = index(context, auth)
    for changes in (
        {"product_version": None},
        {"product_version": "9.0"},
        {"expected": {}},
        {"organization_id": str(context["organization_b"])},
        {"top_k": True},
    ):
        assert search(context, auth, record, **changes).status_code == 422


def test_req604_version_and_document_filters_apply_before_top_k(context):
    auth = headers(context)
    records = []
    for version, title in (("1.0", "Connection"), ("1.1", "Cache"), ("1.1", "Connection")):
        r = post(
            context,
            auth,
            payload(
                f"# {title}\n\n{title}".encode(),
                product_version=version,
                title=title,
                source_key=title.lower(),
            ),
        ).json()
        s = (
            context["client"]
            .post(
                f"/api/documents/{r['document_id']}/revisions/{r['revision_id']}/chunk-sets",
                headers=auth,
                json={},
            )
            .json()
        )
        records.append((r, s))
    built = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={"chunk_set_ids": [s["chunk_set_id"] for r, s in records]},
        )
        .json()
    )
    result = search(
        context, auth, built, top_k=1, document_ids=[records[1][0]["document_id"]]
    ).json()
    assert result["items"][0]["product_version"] == "1.1"
    assert result["items"][0]["source"]["document_id"] == records[1][0]["document_id"]
    assert result["items"][0]["cosine_similarity"] == 0


def test_req602_cross_org_sources_rejected_before_embedding(context, embedding):
    auth = headers(context)
    _, _, s = create(context, auth)
    before = len(embedding[1])
    response = context["client"].post(
        "/api/retrieval/indexes",
        headers=headers(context, "username_b"),
        json={"chunk_set_ids": [s["chunk_set_id"]]},
    )
    assert response.status_code == 404 and len(embedding[1]) == before


def test_req606_public_log_anchor_and_case_type(context):
    from supportops.settings import ROOT

    auth = headers(context)
    case = post(
        context,
        auth,
        payload(
            (ROOT / "data/relaydesk/1.1/reported-symptom.json").read_bytes(),
            filename="case.json",
            format="json",
            source_key="case",
            source_type="synthetic_case",
        ),
    ).json()
    snapshot_response = context["client"].post(
        f"/api/documents/{case['document_id']}/revisions/{case['revision_id']}/chunk-sets",
        headers=auth,
        json={},
    )
    assert snapshot_response.status_code == 200, snapshot_response.text
    snapshot = snapshot_response.json()
    manifest = json.loads((ROOT / "data/lab/manifest.json").read_text(encoding="utf-8"))
    original = next(r for r in manifest["records"] if r["product_version"] == "1.1")
    artifact = json.loads((ROOT / "data/lab" / original["path"]).read_text(encoding="utf-8"))
    experiment = (
        context["client"]
        .post(
            "/api/experiments/import",
            headers=auth,
            json={"artifact": artifact, "sha256": original["sha256"]},
        )
        .json()
    )
    record = (
        context["client"]
        .post(
            "/api/retrieval/indexes",
            headers=auth,
            json={
                "chunk_set_ids": [snapshot["chunk_set_id"]],
                "experiment_ids": [experiment["experiment_id"]],
            },
        )
        .json()
    )
    cases = search(context, auth, record, source_kinds=["case"]).json()["items"]
    assert cases and all(r["kind"] == "case" for r in cases)
    logs = search(
        context, auth, record, source_kinds=["log"], experiment_ids=[experiment["experiment_id"]]
    ).json()["items"]
    assert len(logs) == len(artifact["observations"])
    assert all(
        r["evidence_id"].startswith("lab:") and not r["source"]["execution_verified_by_api"]
        for r in logs
    )
    for hit in logs:
        assert hit["source"]["event"] == artifact["observations"][hit["source"]["ordinal"]]


def test_req603_middle_batch_failure_leaves_no_index(context, embedding, monkeypatch):
    auth = headers(context)
    _, path = __import__("test_chunks_postgres").setup(
        context, auth, ("# Big\n\n" + "Connection " * 200).encode()
    )
    snapshot = (
        context["client"]
        .post(path, headers=auth, json={"max_chars": 128, "overlap_chars": 20})
        .json()
    )
    assert snapshot["chunk_count"] > 10
    original = embedding[0].embed_texts
    calls = []

    def broken(self, texts):
        calls.append(texts)
        if len(calls) == 2:
            raise ModelFailure("MODEL_TIMEOUT")
        return original(self, texts)

    monkeypatch.setattr(embedding[0], "embed_texts", broken)
    response = context["client"].post(
        "/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": [snapshot["chunk_set_id"]]}
    )
    assert response.status_code == 503 and len(calls) == 2
    assert context["client"].get("/api/retrieval/indexes", headers=auth).json()["total"] == 0
