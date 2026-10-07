"""完成的只读调查一次插入，后续恢复与动作状态按第 3 轮设计。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_investigations_ticket",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_investigations_requester",
        ),
        CheckConstraint(
            "input_sha256 ~ '^[a-f0-9]{64}$' AND output_sha256 ~ '^[a-f0-9]{64}$'", name="digests"
        ),
        CheckConstraint(
            "status IN ('completed','failed','stopped','no_evidence','needs_clarification')",
            name="status",
        ),
        Index("ix_investigations_organization_created_id", "organization_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32))
    workflow_version: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    input_sha256: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSONB)
    output_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
