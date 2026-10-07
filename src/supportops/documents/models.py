"""有组织身份的逻辑来源与不可变原文修订。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "product",
            "product_version",
            "source_key",
            name="uq_documents_source",
        ),
        UniqueConstraint("id", "organization_id", name="uq_documents_id_organization"),
        CheckConstraint(
            "product='relaydesk' AND product_version IN ('1.0','1.1','2.0')", name="scope"
        ),
        CheckConstraint(
            "source_type IN ('demo_product','synthetic_case','user_report')", name="source_type"
        ),
        CheckConstraint("format IN ('md','pdf','json')", name="format"),
        CheckConstraint("license IN ('CC0-1.0','proprietary')", name="license"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    product: Mapped[str] = mapped_column(String(32))
    product_version: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(32))
    license: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(150))
    format: Mapped[str] = mapped_column(String(16))


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_revisions_document_organization",
        ),
        ForeignKeyConstraint(
            ["importer_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_document_revisions_importer_organization",
        ),
        UniqueConstraint("document_id", "content_sha256", name="uq_document_revisions_content"),
        UniqueConstraint("document_id", "revision_number", name="uq_document_revisions_number"),
        UniqueConstraint(
            "id", "document_id", "organization_id", name="uq_document_revisions_identity"
        ),
        CheckConstraint(
            "revision_number>0 AND byte_size BETWEEN 0 AND 2097152 "
            "AND octet_length(raw_bytes)=byte_size",
            name="size",
        ),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="digest"),
        CheckConstraint(
            "(status='parsed' AND text IS NOT NULL AND error_code IS NULL "
            "AND error_message IS NULL AND jsonb_array_length(blocks)>0) OR "
            "(status='failed' AND text IS NULL AND error_code IS NOT NULL "
            "AND error_message IS NOT NULL AND blocks='[]'::jsonb)",
            name="state",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    importer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    revision_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    parser_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)
    blocks: Mapped[list] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
