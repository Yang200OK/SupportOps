"""会话组织、不可变原文和修订结果在同一请求事务中处理。"""

import hashlib
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, aliased

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.documents.contracts import (
    DocumentList,
    DocumentSummary,
    DocumentView,
    ImportRequest,
)
from supportops.documents.models import Document, DocumentRevision
from supportops.documents.parser import ParseFailure, parse_document


def missing():
    return ServiceError(404, "DOCUMENT_NOT_FOUND", "资料不存在或不属于当前组织。")


def summary(document: Document, row: DocumentRevision) -> DocumentSummary:
    return DocumentSummary(
        document_id=document.id,
        revision_id=row.id,
        **{
            name: getattr(document, name)
            for name in (
                "source_key",
                "product",
                "product_version",
                "source_type",
                "license",
                "filename",
                "format",
            )
        },
        **{
            name: getattr(row, name)
            for name in (
                "revision_number",
                "title",
                "content_sha256",
                "byte_size",
                "imported_at",
                "importer_id",
                "status",
                "error_code",
                "error_message",
                "parser_version",
            )
        },
    )


def view(document: Document, row: DocumentRevision, reused=False) -> DocumentView:
    return DocumentView(
        **summary(document, row).model_dump(), text=row.text, blocks=row.blocks, reused=reused
    )


def import_document(session: Session, principal: Principal, payload: ImportRequest) -> DocumentView:
    scope = {
        "organization_id": principal.organization_id,
        "product": payload.product,
        "product_version": payload.product_version,
        "source_key": payload.source_key,
    }
    identity = "|".join(str(value) for value in scope.values())
    lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
    # 同来源所有导入在数据库事务内串行；提交 / 回滚后自动释放锁。
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    document = session.scalar(select(Document).filter_by(**scope))
    if document is None:
        document = Document(
            **scope,
            **{
                name: getattr(payload, name)
                for name in ("source_type", "license", "filename", "format")
            },
        )
        session.add(document)
        session.flush()
    elif any(
        getattr(document, name) != getattr(payload, name)
        for name in ("source_type", "license", "filename", "format")
    ):
        raise ServiceError(
            409,
            "SOURCE_IDENTITY_CONFLICT",
            "已有来源的类型、许可、文件名或格式不同，请使用新来源标识。",
        )
    raw = payload.raw_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    old = session.scalar(
        select(DocumentRevision).where(
            DocumentRevision.document_id == document.id,
            DocumentRevision.organization_id == principal.organization_id,
            DocumentRevision.content_sha256 == digest,
        )
    )
    if old is not None:
        return view(document, old, reused=True)
    count = (
        session.scalar(
            select(func.max(DocumentRevision.revision_number)).where(
                DocumentRevision.document_id == document.id,
                DocumentRevision.organization_id == principal.organization_id,
            )
        )
        or 0
    )
    record = DocumentRevision(
        document_id=document.id,
        organization_id=principal.organization_id,
        importer_id=principal.user_id,
        revision_number=count + 1,
        title=payload.title,
        raw_bytes=raw,
        byte_size=len(raw),
        content_sha256=digest,
        parser_version="source-parser-v1",
        status="parsed",
        blocks=[],
    )
    try:
        parsed = parse_document(raw, payload.format, payload.product_version)
        record.text = parsed.text
        record.blocks = [block.model_dump() for block in parsed.blocks]
        if payload.format == "json":
            # JSON 来源声明必须与外层身份一致，不能将用户报告伪装成演示产品资料。
            import json

            if json.loads(raw.decode("utf-8-sig"))["source_type"] != payload.source_type:
                raise ParseFailure("SOURCE_TYPE_MISMATCH", "案例正文来源类型与导入声明不一致。")
    except ParseFailure as failure:
        record.status = "failed"
        record.text = None
        record.blocks = []
        record.error_code = failure.code
        record.error_message = failure.message
    session.add(record)
    session.flush()
    return view(document, record)


def get_document(session: Session, principal: Principal, document_id: UUID) -> Document:
    document = session.scalar(
        select(Document).where(
            Document.id == document_id, Document.organization_id == principal.organization_id
        )
    )
    if document is None:
        raise missing()
    return document


def get_revision(session: Session, principal: Principal, document_id: UUID, revision_id: UUID):
    document = get_document(session, principal, document_id)
    revision = session.scalar(
        select(DocumentRevision).where(
            DocumentRevision.id == revision_id,
            DocumentRevision.document_id == document.id,
            DocumentRevision.organization_id == principal.organization_id,
        )
    )
    if revision is None:
        raise missing()
    return document, revision


def list_documents(
    session: Session, principal: Principal, version: str | None, offset: int, limit: int
) -> DocumentList:
    filters = [Document.organization_id == principal.organization_id]
    if version is not None:
        filters.append(Document.product_version == version)
    total = session.scalar(select(func.count()).select_from(Document).where(*filters))
    latest = aliased(DocumentRevision)
    number = (
        select(func.max(latest.revision_number))
        .where(
            latest.document_id == Document.id, latest.organization_id == principal.organization_id
        )
        .correlate(Document)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Document, DocumentRevision)
        .join(DocumentRevision, DocumentRevision.document_id == Document.id)
        .where(*filters, DocumentRevision.revision_number == number)
        .order_by(DocumentRevision.imported_at.desc(), Document.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return DocumentList(
        items=[summary(doc, rev) for doc, rev in rows], total=total, offset=offset, limit=limit
    )


def list_revisions(
    session: Session, principal: Principal, document_id: UUID, offset: int, limit: int
) -> DocumentList:
    document = get_document(session, principal, document_id)
    scope = (DocumentRevision.document_id == document.id) & (
        DocumentRevision.organization_id == principal.organization_id
    )
    total = session.scalar(select(func.count()).select_from(DocumentRevision).where(scope))
    rows = session.scalars(
        select(DocumentRevision)
        .where(scope)
        .order_by(DocumentRevision.revision_number.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return DocumentList(
        items=[summary(document, row) for row in rows], total=total, offset=offset, limit=limit
    )
