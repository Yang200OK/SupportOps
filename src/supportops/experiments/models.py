"""不可变观测包的组织范围与摘要身份。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_experiments_requester_organization",
        ),
        UniqueConstraint("organization_id", "run_id", name="uq_experiments_run"),
        CheckConstraint("product_version IN ('1.0','1.1','2.0')", name="version"),
        CheckConstraint("sha256 ~ '^[a-f0-9]{64}$'", name="digest"),
        CheckConstraint(
            "jsonb_array_length(artifact->'observations') BETWEEN 3 AND 200", name="observations"
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requester_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    product_version: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64))
    artifact: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
