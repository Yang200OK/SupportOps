"""检查点可推进；建议、批准与事件作为独立审计记录。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class ActionJob(Base):
    __tablename__ = "action_jobs"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="action_jobs_id_organization_id_key"),
        UniqueConstraint(
            "organization_id",
            "requester_id",
            "request_id",
            name="action_jobs_organization_id_requester_id_request_id_key",
        ),
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        Index("ix_action_jobs_ticket", "organization_id", "ticket_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    investigation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32))
    proposal: Mapped[dict] = mapped_column(JSONB)
    proposal_sha256: Mapped[str] = mapped_column(String(64))
    checkpoint: Mapped[dict] = mapped_column(JSONB)
    checkpoint_sha256: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionApproval(Base):
    __tablename__ = "action_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["action_jobs.id", "action_jobs.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["user_id", "organization_id"], ["users.id", "users.organization_id"]),
    )
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    proposal_sha256: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionEvent(Base):
    __tablename__ = "action_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["action_jobs.id", "action_jobs.organization_id"],
            ondelete="CASCADE",
        ),
    )
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
