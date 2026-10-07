"""向量使用 PostgreSQL 原生类型，不在进程中保存备用索引。"""

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
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from supportops.db.models import Base


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kwargs):
        return "vector"


# 让迁移核对识别数据库实际类型；写入和检索均显式 CAST 参数。
ischema_names["vector"] = Vector


class RetrievalIndex(Base):
    __tablename__ = "retrieval_indexes"
    __table_args__ = (
        UniqueConstraint("organization_id", "fingerprint", name="uq_retrieval_indexes_fingerprint"),
        UniqueConstraint(
            "id", "organization_id", "dimensions", name="uq_retrieval_indexes_identity"
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_retrieval_indexes_requester_organization",
        ),
        CheckConstraint(
            "dimensions BETWEEN 1 AND 4096 AND entry_count BETWEEN 1 AND 2000", name="bounds"
        ),
        CheckConstraint(
            "fingerprint ~ '^[a-f0-9]{64}$' AND corpus_sha256 ~ '^[a-f0-9]{64}$'", name="digests"
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    fingerprint: Mapped[str] = mapped_column(String(64))
    corpus_sha256: Mapped[str] = mapped_column(String(64))
    dimensions: Mapped[int] = mapped_column(Integer)
    entry_count: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict] = mapped_column(JSONB)
    manifest: Mapped[dict] = mapped_column(JSONB)
    build_usage: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrievalEntry(Base):
    __tablename__ = "retrieval_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["index_id", "organization_id", "dimensions"],
            [
                "retrieval_indexes.id",
                "retrieval_indexes.organization_id",
                "retrieval_indexes.dimensions",
            ],
            name="fk_retrieval_entries_index_identity",
        ),
        CheckConstraint(
            "vector_dims(embedding)=dimensions AND vector_norm(embedding)>0", name="vector"
        ),
        CheckConstraint(
            "product_version IN ('1.0','1.1','2.0') AND kind IN ('document','case','log')",
            name="scope",
        ),
    )
    index_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dimensions: Mapped[int] = mapped_column(Integer)
    product_version: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    experiment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    embedding: Mapped[object] = mapped_column(Vector())
