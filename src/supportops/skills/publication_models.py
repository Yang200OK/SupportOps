"""草稿正文与报告 / 审批 / 事件不可变，只允许明确状态转换。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class SkillDraft(Base):
    __tablename__ = "skill_drafts"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_skill_draft_org"),
        UniqueConstraint(
            "organization_id", "requester_id", "request_id", name="uq_skill_draft_request"
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        ForeignKeyConstraint(
            ["candidate_id", "organization_id"],
            ["experience_candidates.id", "experience_candidates.organization_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SkillRegression(Base):
    __tablename__ = "skill_regressions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["draft_id", "organization_id"],
            ["skill_drafts.id", "skill_drafts.organization_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    report: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))


class SkillDecision(Base):
    __tablename__ = "skill_decisions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "requester_id", "request_id", name="uq_skill_decision_request"
        ),
        ForeignKeyConstraint(
            ["requester_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        ForeignKeyConstraint(
            ["draft_id", "organization_id"],
            ["skill_drafts.id", "skill_drafts.organization_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))


class SkillEvent(Base):
    __tablename__ = "skill_publication_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["draft_id", "organization_id"],
            ["skill_drafts.id", "skill_drafts.organization_id"],
            ondelete="CASCADE",
        ),
    )
    draft_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    data: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
