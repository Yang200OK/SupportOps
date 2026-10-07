"""执行检查点独立于原不可变任务板，事件冻结每次状态。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Execution(Base):
    __tablename__ = "coordination_executions"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_coordination_execution_org"),
        UniqueConstraint(
            "organization_id",
            "requester_id",
            "request_id",
            name="uq_coordination_execution_request",
        ),
        ForeignKeyConstraint(
            ["board_id", "organization_id"],
            ["coordination_boards.id", "coordination_boards.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    board_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    binding: Mapped[dict] = mapped_column(JSONB)
    binding_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[dict] = mapped_column(JSONB)
    state_sha256: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionEvent(Base):
    __tablename__ = "coordination_execution_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "organization_id"],
            ["coordination_executions.id", "coordination_executions.organization_id"],
            ondelete="CASCADE",
        ),
    )
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
