"""原始复盘不可变；候选仅开放状态与修订号的受控更新。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Retrospective(Base):
    __tablename__ = "retrospectives"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_retrospective_org"),
        UniqueConstraint("organization_id", "requester_id", "request_id", name="uq_memory_request"),
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        Index("ix_memory_ticket", "organization_id", "ticket_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    investigation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[dict] = mapped_column(JSONB)
    source_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSONB)
    summary_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Experience(Base):
    __tablename__ = "experience_candidates"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_experience_org"),
        UniqueConstraint("retrospective_id", name="uq_experience_retrospective"),
        ForeignKeyConstraint(
            ["retrospective_id", "organization_id"],
            ["retrospectives.id", "retrospectives.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_memory_recall", "organization_id", "product_version", "mode", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    retrospective_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    product_version: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSONB)
    body_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "organization_id"],
            ["experience_candidates.id", "experience_candidates.organization_id"],
            ondelete="CASCADE",
        ),
    )
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))


class MemoryConflict(Base):
    __tablename__ = "memory_conflicts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["left_id", "organization_id"],
            ["experience_candidates.id", "experience_candidates.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["right_id", "organization_id"],
            ["experience_candidates.id", "experience_candidates.organization_id"],
            ondelete="CASCADE",
        ),
    )
    left_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    right_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
