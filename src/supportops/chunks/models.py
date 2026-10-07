"""切片快照不可变，引用必须绑定同一组织的固定原文修订。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class ChunkSet(Base):
    __tablename__ = "chunk_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "document_id", "organization_id"],
            [
                "document_revisions.id",
                "document_revisions.document_id",
                "document_revisions.organization_id",
            ],
            name="fk_chunk_sets_revision_identity",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_chunk_sets_requester_organization",
        ),
        UniqueConstraint(
            "revision_id", "chunker_version", "config_sha256", name="uq_chunk_sets_configuration"
        ),
        CheckConstraint("chunker_version='structure-v1'", name="chunker_version"),
        CheckConstraint(
            "parent_count BETWEEN 1 AND 3000 AND chunk_count BETWEEN 1 AND 3000 "
            "AND jsonb_array_length(parents)=parent_count "
            "AND jsonb_array_length(chunks)=chunk_count",
            name="counts",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$' AND parsed_sha256 ~ '^[a-f0-9]{64}$' "
            "AND config_sha256 ~ '^[a-f0-9]{64}$' AND snapshot_sha256 ~ '^[a-f0-9]{64}$'",
            name="digests",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    chunker_version: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    parsed_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    parent_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    parents: Mapped[list] = mapped_column(JSONB)
    chunks: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
