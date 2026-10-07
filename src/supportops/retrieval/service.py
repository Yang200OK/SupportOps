"""先限定来源和版本，再在固定向量快照中做全量 cosine 排序。"""

import hashlib
import json
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import Float, cast, func, literal, select, text

from supportops.api.errors import ServiceError
from supportops.chunks import service as chunks
from supportops.chunks.chunking import digest
from supportops.experiments.service import read_experiment
from supportops.models.provider import ModelFailure, ModelSettings, Provider
from supportops.retrieval.models import RetrievalEntry, RetrievalIndex, Vector


def configuration(settings):
    return {
        "endpoint": settings.base_url,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "renderer": "retrieval-text-v1",
        "distance": "cosine",
        "algorithm": "exact",
        "normalization": "scaled-l2-v1",
    }


def unavailable(failure):
    return ServiceError(503, failure.code, "向量模型调用失败，未更换模型或生成备用结果。")


def get_index(session, principal, index_id):
    record = session.scalar(
        select(RetrievalIndex).where(
            RetrievalIndex.id == index_id,
            RetrievalIndex.organization_id == principal.organization_id,
        )
    )
    if record is None:
        raise ServiceError(404, "RETRIEVAL_INDEX_NOT_FOUND", "索引不存在或不属于当前组织。")
    if (
        digest(record.manifest) != record.corpus_sha256
        or digest(
            {
                "organization_id": str(principal.organization_id),
                "corpus": record.corpus_sha256,
                "configuration": record.configuration,
            }
        )
        != record.fingerprint
    ):
        raise ServiceError(409, "INDEX_INTEGRITY_FAILED", "固定索引清单摘要不一致。")
    return record


def view(record, reused=False):
    return {
        "index_id": str(record.id),
        "fingerprint": record.fingerprint,
        "corpus_sha256": record.corpus_sha256,
        "entry_count": record.entry_count,
        "configuration": record.configuration,
        "sources": record.manifest["sources"],
        "build_usage": record.build_usage,
        "created_at": record.created_at,
        "reused": reused,
    }


def collect_entries(session, principal, payload):
    entries = []
    for set_id in sorted(payload.chunk_set_ids, key=str):
        record, document, revision = chunks.get_set(session, principal, set_id)
        for raw in record.chunks:
            # 复用既有原文字节、解析正文、位置和快照摘要核对，不能仅凭数据库字段信任切片。
            reference = chunks.citation(session, principal, set_id, UUID(raw["chunk_id"]))
            child = reference["chunk"]
            evidence = f"chunk:{set_id}:{child.chunk_id}"
            source = {
                **reference["source"],
                "chunk_set_id": str(set_id),
                "chunk_id": str(child.chunk_id),
                "source_key": document.source_key,
                "snapshot_sha256": record.snapshot_sha256,
                "heading_path": child.heading_path,
                "spans": [span.model_dump() for span in child.spans],
                "page_number": child.page_number,
                "json_pointer": child.json_pointer,
            }
            source = json.loads(json.dumps(source, default=str))
            rendered = revision.title + "\n" + " / ".join(child.heading_path) + "\n" + child.text
            entries.append(
                {
                    "evidence_id": evidence,
                    "product_version": document.product_version,
                    "kind": "case" if document.format == "json" else "document",
                    "document_id": str(document.id),
                    "experiment_id": None,
                    "payload": {
                        "text": child.text,
                        "embedding_text": rendered,
                        "source": source,
                        "reference_url": (
                            f"/api/chunk-sets/{set_id}/chunks/{child.chunk_id}/citation"
                        ),
                    },
                }
            )
    for experiment_id in sorted(payload.experiment_ids, key=str):
        detail = read_experiment(session, principal, experiment_id)
        for ordinal, observation in enumerate(detail.artifact.observations):
            event = observation.model_dump(mode="json", exclude_none=True)
            rendered = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            entries.append(
                {
                    "evidence_id": detail.evidence_ids[ordinal],
                    "product_version": detail.product_version,
                    "kind": "log",
                    "document_id": None,
                    "experiment_id": str(experiment_id),
                    "payload": {
                        "text": rendered,
                        "embedding_text": rendered,
                        "source": {
                            "experiment_id": str(experiment_id),
                            "run_id": str(detail.run_id),
                            "ordinal": ordinal,
                            "sha256": detail.sha256,
                            "event": event,
                            "execution_verified_by_api": False,
                        },
                        "reference_url": f"/api/experiments/{experiment_id}",
                    },
                }
            )
    entries.sort(key=lambda row: row["evidence_id"])
    if not 1 <= len(entries) <= 2000 or any(
        len(r["payload"]["embedding_text"]) > 6000 for r in entries
    ):
        raise ServiceError(422, "INDEX_SOURCE_LIMIT", "来源条目数量或单条文本超出本轮上限。")
    # 同一资料不同切片配置不同时入库，避免把重叠实验重复当作多个独立来源。
    documents = [chunks.get_set(session, principal, s)[1].id for s in payload.chunk_set_ids]
    if len(documents) != len(set(documents)):
        raise ServiceError(
            422, "INDEX_DUPLICATE_DOCUMENT", "同一逻辑资料只能选择一个固定切片快照。"
        )
    return entries


def build_index(session, principal, payload):
    started = perf_counter()
    settings = ModelSettings()
    config = configuration(settings)
    entries = collect_entries(session, principal, payload)
    manifest = {
        "sources": {
            "chunk_set_ids": sorted(map(str, payload.chunk_set_ids)),
            "experiment_ids": sorted(map(str, payload.experiment_ids)),
        },
        "entries": [{"evidence_id": row["evidence_id"], "sha256": digest(row)} for row in entries],
    }
    corpus_sha = digest(manifest)
    fingerprint = digest(
        {
            "organization_id": str(principal.organization_id),
            "corpus": corpus_sha,
            "configuration": config,
        }
    )
    lock = int.from_bytes(hashlib.sha256(fingerprint.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    existing = session.scalar(
        select(RetrievalIndex).where(
            RetrievalIndex.organization_id == principal.organization_id,
            RetrievalIndex.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        get_index(session, principal, existing.id)
        return view(existing, reused=True)
    vectors, batches = [], []
    try:
        with Provider(settings) as provider:
            for start in range(0, len(entries), 10):
                result = provider.embed_texts(
                    [r["payload"]["embedding_text"] for r in entries[start : start + 10]]
                )
                vectors.extend(result["vectors"])
                batches.append({k: v for k, v in result.items() if k != "vectors"})
    except ModelFailure as failure:
        raise unavailable(failure) from None
    record = RetrievalIndex(
        id=uuid4(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        fingerprint=fingerprint,
        corpus_sha256=corpus_sha,
        dimensions=settings.embedding_dimensions,
        entry_count=len(entries),
        configuration=config,
        manifest=manifest,
        build_usage={
            "batches": batches,
            "input_tokens": sum(r["input_tokens"] for r in batches),
            "cost_cny": None,
            "source_and_model_latency_ms": round((perf_counter() - started) * 1000, 3),
        },
    )
    session.add(record)
    session.flush()
    for row, vector in zip(entries, vectors, strict=True):
        session.add(
            RetrievalEntry(
                index_id=record.id,
                organization_id=principal.organization_id,
                dimensions=record.dimensions,
                **{
                    k: UUID(v) if k in ("document_id", "experiment_id") and v else v
                    for k, v in row.items()
                },
                embedding=cast(literal(json.dumps(vector)), Vector()),
            )
        )
    session.flush()
    return view(record)


def list_indexes(session, principal, offset, limit):
    scope = RetrievalIndex.organization_id == principal.organization_id
    records = session.scalars(
        select(RetrievalIndex)
        .where(scope)
        .order_by(RetrievalIndex.created_at.desc(), RetrievalIndex.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [view(get_index(session, principal, r.id)) for r in records],
        "total": session.scalar(select(func.count()).select_from(RetrievalIndex).where(scope)),
        "offset": offset,
        "limit": limit,
    }


def entries_page(session, principal, index_id, offset, limit):
    index = get_index(session, principal, index_id)
    rows = session.execute(
        select(
            RetrievalEntry.evidence_id,
            RetrievalEntry.kind,
            RetrievalEntry.product_version,
            RetrievalEntry.payload,
        )
        .where(
            RetrievalEntry.index_id == index_id,
            RetrievalEntry.organization_id == principal.organization_id,
        )
        .order_by(RetrievalEntry.evidence_id)
        .offset(offset)
        .limit(limit)
    ).mappings()
    return {
        "items": [dict(row) for row in rows],
        "total": index.entry_count,
        "offset": offset,
        "limit": limit,
    }


def scope_for(principal, index_id, payload):
    entry = RetrievalEntry
    scope = (
        (entry.index_id == index_id)
        & (entry.organization_id == principal.organization_id)
        & (entry.product_version == payload.product_version)
        & entry.kind.in_(payload.source_kinds)
    )
    if payload.document_ids:
        scope &= (entry.kind == "log") | entry.document_id.in_(payload.document_ids)
    if payload.experiment_ids:
        scope &= (entry.kind != "log") | entry.experiment_id.in_(payload.experiment_ids)
    return scope


def verify_entry_hash(row, index):
    identity = {
        k: str(row[k]) if row[k] is not None else None
        for k in ("evidence_id", "product_version", "kind", "document_id", "experiment_id")
    }
    identity["payload"] = row["payload"]
    expected = next(
        (e["sha256"] for e in index.manifest["entries"] if e["evidence_id"] == row["evidence_id"]),
        None,
    )
    if digest(identity) != expected:
        raise ServiceError(409, "INDEX_INTEGRITY_FAILED", "命中证据与索引清单不一致。")


def verify_hit(session, principal, row, index):
    source = row["payload"]["source"]
    verify_entry_hash(row, index)
    if row["kind"] == "log":
        detail = read_experiment(session, principal, UUID(source["experiment_id"]))
        if (
            detail.sha256 != source["sha256"]
            or detail.evidence_ids[source["ordinal"]] != row["evidence_id"]
        ):
            raise ServiceError(409, "INDEX_INTEGRITY_FAILED", "原始观测锚点不一致。")
    else:
        chunks.citation(session, principal, UUID(source["chunk_set_id"]), UUID(source["chunk_id"]))


def search(session, principal, index_id, payload):
    started = perf_counter()
    index = get_index(session, principal, index_id)
    settings = ModelSettings()
    if configuration(settings) != index.configuration:
        raise ServiceError(
            409, "INDEX_MODEL_MISMATCH", "模型、维度或接口已变化，需要显式重新建索引。"
        )
    scope = scope_for(principal, index_id, payload)
    eligible = session.scalar(select(func.count()).select_from(RetrievalEntry).where(scope))
    result = {
        "index_id": str(index_id),
        "corpus_sha256": index.corpus_sha256,
        "configuration": index.configuration,
        "scope": payload.model_dump(mode="json", exclude={"query"}),
        "eligible_count": eligible,
        "items": [],
        "support_verified": False,
        "usage": {"input_tokens": 0, "cost_cny": None, "model_called": False},
        "sql_latency_ms": 0.0,
    }
    if not eligible:
        result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        return result
    try:
        with Provider(settings) as provider:
            embedding = provider.embed_texts([payload.query])
    except ModelFailure as failure:
        raise unavailable(failure) from None
    result["usage"] = {k: v for k, v in embedding.items() if k != "vectors"}
    result["usage"]["model_called"] = True
    distance = RetrievalEntry.embedding.op("<=>", return_type=Float)(
        cast(literal(json.dumps(embedding["vectors"][0])), Vector())
    )
    sql_start = perf_counter()
    rows = (
        session.execute(
            select(
                RetrievalEntry.evidence_id,
                RetrievalEntry.kind,
                RetrievalEntry.product_version,
                RetrievalEntry.document_id,
                RetrievalEntry.experiment_id,
                RetrievalEntry.payload,
                distance.label("distance"),
            )
            .where(scope)
            .order_by(distance, RetrievalEntry.evidence_id)
            .limit(payload.top_k)
        )
        .mappings()
        .all()
    )
    result["sql_latency_ms"] = round((perf_counter() - sql_start) * 1000, 3)
    for rank, row in enumerate(rows, 1):
        verify_hit(session, principal, row, index)
        result["items"].append(
            {
                "rank": rank,
                "evidence_id": row["evidence_id"],
                "kind": row["kind"],
                "product_version": row["product_version"],
                "cosine_similarity": 1 - row["distance"],
                "text": row["payload"]["text"],
                "source": row["payload"]["source"],
                "reference_url": row["payload"]["reference_url"],
                "text_verified": True,
                "support_verified": False,
            }
        )
    result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
    return result
