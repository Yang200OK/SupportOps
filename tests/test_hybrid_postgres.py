"""生产排序使用真实数据库；模型替身仅验证协议与失败边界。"""

import json
import math
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_documents_postgres import payload, post
from test_retrieval_postgres import embedding as embedding
from test_retrieval_postgres import index, search
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.settings import ROOT

pytestmark = pytest.mark.integration


def test_req704_bm25_has_no_model_and_empty_is_not_vector(context, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    before = len(embedding[1])
    embedding[0].fail = True
    result = search(context, auth, record, mode="bm25", query="RD_TIMEOUT").json()
    assert result["mode"] == "bm25" and result["items"]
    assert not result["usage"]["model_called"] and len(embedding[1]) == before
    assert search(context, auth, record, mode="bm25", query="absent").json()["items"] == []


def test_req703_rrf_and_model_failure_do_not_degrade(context, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    result = search(context, auth, record, mode="rrf", query="RD_TIMEOUT").json()
    hit = result["items"][0]
    assert hit["vector_rank"] and hit["bm25_rank"]
    assert hit["rrf_score"] == pytest.approx(
        1 / (60 + hit["vector_rank"]) + 1 / (60 + hit["bm25_rank"])
    )
    embedding[0].fail = True
    failure = search(context, auth, record, mode="rrf", query="RD_TIMEOUT")
    assert failure.status_code == 503 and failure.json()["error"]["code"] == "MODEL_TIMEOUT"


@pytest.mark.parametrize("mode", ["bm25", "rrf"])
def test_req704_hybrid_scope_org_and_empty(context, embedding, mode):
    auth = headers(context)
    record, _ = index(context, auth)
    assert search(context, headers(context, "username_b"), record, mode=mode).status_code == 404
    before = len(embedding[1])
    result = search(context, auth, record, mode=mode, product_version="2.0").json()
    assert result["items"] == [] and result["eligible_count"] == 0
    assert len(embedding[1]) == before


def test_req705_tampered_lexical_payload_fails_before_scoring(context, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    with context["admin"].begin() as c:
        c.execute(
            text(
                "UPDATE retrieval_entries SET "
                "payload=jsonb_set(payload,'{embedding_text}','\"tampered\"') WHERE index_id=:id"
            ),
            {"id": record["index_id"]},
        )
    result = search(context, auth, record, mode="bm25")
    assert result.status_code == 409 and result.json()["error"]["code"] == "INDEX_INTEGRITY_FAILED"


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "auto"},
        {"candidate_limit": True},
        {"candidate_limit": 101},
        {"candidate_limit": 1, "top_k": 2},
    ],
)
def test_req703_invalid_modes_and_limits_rejected(context, changes):
    auth = headers(context)
    record, _ = index(context, auth)
    assert search(context, auth, record, **changes).status_code == 422


@pytest.mark.parametrize("mode", ["bm25", "rrf"])
def test_req702_scoped_statistics_and_filters_before_candidate_limit(context, mode):
    auth = headers(context)
    revisions, snapshots = [], []
    for version, key in (("1.0", "old"), ("1.1", "selected"), ("1.1", "another")):
        revision = post(
            context,
            auth,
            payload(b"RD_TIMEOUT", product_version=version, source_key=key, title=key),
        ).json()
        response = context["client"].post(
            f"/api/documents/{revision['document_id']}/revisions/{revision['revision_id']}/chunk-sets",
            headers=auth,
            json={},
        )
        assert response.status_code == 200 and response.json()["chunk_count"] == 1
        revisions.append(revision)
        snapshots.append(response.json()["chunk_set_id"])
    response = context["client"].post(
        "/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": snapshots}
    )
    assert response.status_code == 200
    result = search(
        context,
        auth,
        response.json(),
        mode=mode,
        query="RD_TIMEOUT",
        top_k=1,
        candidate_limit=1,
        document_ids=[revisions[1]["document_id"]],
    ).json()
    assert result["eligible_count"] == 1 and len(result["items"]) == 1
    hit = result["items"][0]
    assert hit["source"]["document_id"] == revisions[1]["document_id"]
    assert hit["product_version"] == "1.1"
    assert hit["bm25_score"] == pytest.approx(math.log(4 / 3))


def test_req704_explicit_keyword_mode_survives_model_change(context, monkeypatch, embedding):
    auth = headers(context)
    record, _ = index(context, auth)
    monkeypatch.setenv("SUPPORTOPS_MODEL_EMBEDDING_MODEL", "changed")
    before = len(embedding[1])
    assert search(context, auth, record, mode="bm25", query="RD_TIMEOUT").status_code == 200
    assert search(context, auth, record, mode="rrf").status_code == 409
    assert len(embedding[1]) == before


@pytest.mark.parametrize("mode", ["bm25", "rrf"])
def test_req705_original_tampering_stops_hybrid(context, mode):
    auth = headers(context)
    record, snapshot = index(context, auth)
    with context["admin"].begin() as c:
        c.execute(
            text("UPDATE document_revisions SET text=text || 'changed' WHERE id=:id"),
            {"id": snapshot["revision_id"]},
        )
    result = search(context, auth, record, mode=mode, query="RD_TIMEOUT")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "CITATION_INTEGRITY_FAILED"


@pytest.mark.parametrize("mode", ["bm25", "rrf"])
def test_req705_specific_experiment_scope_and_original_anchor(context, embedding, mode):
    auth = headers(context)
    manifest = json.loads((ROOT / "data/lab/manifest.json").read_text(encoding="utf-8"))
    sources = [r for r in manifest["records"] if r["product_version"] == "1.1"][:2]
    experiments = []
    for source in sources:
        artifact = json.loads((ROOT / "data/lab" / source["path"]).read_text(encoding="utf-8"))
        response = context["client"].post(
            "/api/experiments/import",
            headers=auth,
            json={"artifact": artifact, "sha256": source["sha256"]},
        )
        assert response.status_code == 200
        summary = response.json()
        detail = context["client"].get(f"/api/experiments/{summary['experiment_id']}", headers=auth)
        assert detail.status_code == 200
        experiments.append(detail.json())
    response = context["client"].post(
        "/api/retrieval/indexes",
        headers=auth,
        json={"experiment_ids": [e["experiment_id"] for e in experiments]},
    )
    assert response.status_code == 200
    record = response.json()
    result = search(
        context,
        auth,
        record,
        mode=mode,
        query="phase",
        source_kinds=["log"],
        experiment_ids=[experiments[0]["experiment_id"]],
    ).json()
    assert result["eligible_count"] == len(experiments[0]["artifact"]["observations"])
    assert len(result["items"]) == result["eligible_count"]
    for hit in result["items"]:
        source = hit["source"]
        assert source["experiment_id"] == experiments[0]["experiment_id"]
        assert source["sha256"] == experiments[0]["sha256"]
        assert source["event"] == experiments[0]["artifact"]["observations"][source["ordinal"]]
        assert hit["text_verified"] and not hit["support_verified"]
    before = len(embedding[1])
    result = search(context, auth, record, mode=mode, experiment_ids=[str(uuid4())]).json()
    assert not result["items"] and result["eligible_count"] == 0
    assert len(embedding[1]) == before
