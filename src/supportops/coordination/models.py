"""任务板内容不可变；取消只修改状态和修订，事件追加保存。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Board(Base):
    __tablename__ = "coordination_boards"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_coordination_board_org"),
        UniqueConstraint(
            "organization_id", "requester_id", "request_id", name="uq_coordination_board_request"
        ),
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        Index("ix_coordination_board_org_ticket", "organization_id", "ticket_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    input_sha256: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSONB)
    plan_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BoardEvent(Base):
    __tablename__ = "coordination_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["board_id", "organization_id"],
            ["coordination_boards.id", "coordination_boards.organization_id"],
            ondelete="CASCADE",
        ),
    )
    board_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
