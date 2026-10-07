"""引用从固定修订反查，不接受客户端摘录或组织范围。"""

import hashlib
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.chunks.chunking import ChunkFailure, build_chunks, digest, snapshot_digest
from supportops.chunks.contracts import (
    ChildChunk,
    ChunkConfig,
    ChunkPage,
    ChunkResult,
    ChunkSetList,
    ChunkSetView,
    ParentChunk,
)
from supportops.chunks.models import ChunkSet
from supportops.documents.contracts import ParsedDocument
from supportops.documents.models import Document, DocumentRevision
from supportops.documents.service import get_revision


def missing():
    return ServiceError(404, "CHUNK_REFERENCE_NOT_FOUND", "切片引用不存在或不属于当前组织。")


def integrity_failed():
    return ServiceError(409, "CITATION_INTEGRITY_FAILED", "固定原文、解析正文或切片位置核对失败。")


def view(record: ChunkSet, document: Document, revision: DocumentRevision, reused=False):
    return ChunkSetView(
        chunk_set_id=record.id,
        document_id=record.document_id,
        revision_id=record.revision_id,
        product_version=document.product_version,
        revision_number=revision.revision_number,
        **{
            name: getattr(record, name)
            for name in (
                "content_sha256",
                "parsed_sha256",
                "chunker_version",
                "config",
                "config_sha256",
                "snapshot_sha256",
                "parent_count",
                "chunk_count",
                "created_at",
            )
        },
        reused=reused,
    )


def create_chunk_set(
    session: Session,
    principal: Principal,
    document_id: UUID,
    revision_id: UUID,
    config: ChunkConfig,
):
    document, revision = get_revision(session, principal, document_id, revision_id)
    if revision.status != "parsed":
        raise ServiceError(409, "REVISION_NOT_PARSED", "失败修订不能生成可用切片。")
    lock_text = f"chunks:{principal.organization_id}:{revision_id}:{digest(config.model_dump())}"
    lock = int.from_bytes(hashlib.sha256(lock_text.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    existing = session.scalar(
        select(ChunkSet).where(
            ChunkSet.organization_id == principal.organization_id,
            ChunkSet.revision_id == revision_id,
            ChunkSet.chunker_version == "structure-v1",
            ChunkSet.config_sha256 == digest(config.model_dump()),
        )
    )
    if existing is not None:
        return view(existing, document, revision, reused=True)
    if hashlib.sha256(revision.raw_bytes).hexdigest() != revision.content_sha256:
        raise integrity_failed()
    parsed = ParsedDocument(text=revision.text, blocks=revision.blocks)
    try:
        result = build_chunks(revision.id, parsed, document.format, config)
    except ChunkFailure as failure:
        status = 409 if failure.code == "SOURCE_LOCATION_INVALID" else 422
        raise ServiceError(status, failure.code, failure.message) from None
    record = ChunkSet(
        id=result.chunk_set_id,
        document_id=document.id,
        revision_id=revision.id,
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        chunker_version=result.chunker_version,
        config=config.model_dump(),
        content_sha256=revision.content_sha256,
        parsed_sha256=result.parsed_sha256,
        config_sha256=result.config_sha256,
        snapshot_sha256=result.snapshot_sha256,
        parents=[p.model_dump(mode="json") for p in result.parents],
        chunks=[c.model_dump(mode="json") for c in result.chunks],
        parent_count=len(result.parents),
        chunk_count=len(result.chunks),
    )
    session.add(record)
    session.flush()
    return view(record, document, revision)


def get_set(session: Session, principal: Principal, set_id: UUID):
    row = session.scalar(
        select(ChunkSet).where(
            ChunkSet.id == set_id, ChunkSet.organization_id == principal.organization_id
        )
    )
    if row is None:
        raise missing()
    document, revision = get_revision(session, principal, row.document_id, row.revision_id)
    return row, document, revision


def list_sets(
    session: Session,
    principal: Principal,
    document_id: UUID,
    revision_id: UUID,
    offset: int,
    limit: int,
):
    document, revision = get_revision(session, principal, document_id, revision_id)
    scope = (ChunkSet.organization_id == principal.organization_id) & (
        ChunkSet.revision_id == revision.id
    )
    total = session.scalar(select(func.count()).select_from(ChunkSet).where(scope))
    records = session.scalars(
        select(ChunkSet)
        .where(scope)
        .order_by(ChunkSet.created_at.desc(), ChunkSet.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ChunkSetList(
        items=[view(r, document, revision) for r in records],
        total=total,
        offset=offset,
        limit=limit,
    )


def list_chunks(session: Session, principal: Principal, set_id: UUID, offset: int, limit: int):
    record, document, revision = get_set(session, principal, set_id)
    return ChunkPage(
        items=[ChildChunk.model_validate(c) for c in record.chunks[offset : offset + limit]],
        total=record.chunk_count,
        offset=offset,
        limit=limit,
        snapshot=view(record, document, revision),
    )


def citation(session: Session, principal: Principal, set_id: UUID, chunk_id: UUID):
    record, document, revision = get_set(session, principal, set_id)
    try:
        result = ChunkResult(
            chunk_set_id=record.id,
            revision_id=record.revision_id,
            chunker_version=record.chunker_version,
            config=record.config,
            config_sha256=record.config_sha256,
            parsed_sha256=record.parsed_sha256,
            parents=record.parents,
            chunks=record.chunks,
            snapshot_sha256=record.snapshot_sha256,
        )
    except ValidationError as exc:
        raise integrity_failed() from exc
    child = next((c for c in result.chunks if c.chunk_id == chunk_id), None)
    if child is None:
        raise missing()
    source_text = revision.text
    if (
        revision.status != "parsed"
        or source_text is None
        or hashlib.sha256(revision.raw_bytes).hexdigest() != record.content_sha256
        or revision.content_sha256 != record.content_sha256
        or hashlib.sha256(source_text.encode()).hexdigest() != record.parsed_sha256
        or digest(record.config) != record.config_sha256
        or snapshot_digest(result) != record.snapshot_sha256
    ):
        raise integrity_failed()
    parent = next((p for p in result.parents if p.parent_id == child.parent_id), None)
    if parent is None or not 0 <= parent.start < parent.end <= len(source_text):
        raise integrity_failed()
    if parent.text != source_text[parent.start : parent.end]:
        raise integrity_failed()
    excerpts = []
    for span in child.spans:
        if not parent.start <= span.start < span.end <= parent.end:
            raise integrity_failed()
        excerpts.append(
            {
                "start": span.start,
                "end": span.end,
                "line_start": span.line_start,
                "line_end": span.line_end,
                "before": source_text[max(parent.start, span.start - 80) : span.start],
                "excerpt": source_text[span.start : span.end],
                "after": source_text[span.end : min(parent.end, span.end + 80)],
            }
        )
    if not excerpts or "".join(p["excerpt"] for p in excerpts) != child.text:
        raise integrity_failed()
    return {
        "snapshot": view(record, document, revision),
        "chunk": child,
        "parent": ParentChunk.model_validate(parent),
        "source": {
            "document_id": document.id,
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "title": revision.title,
            "filename": document.filename,
            "product_version": document.product_version,
            "content_sha256": revision.content_sha256,
            "format": document.format,
            "position_basis": "unicode_chars_in_parsed_text",
        },
        "parts": excerpts,
        "text_verified": True,
        "support_verified": False,
    }
