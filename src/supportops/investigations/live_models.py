"""实验登记只由可信准备脚本插入，应用角色仅可读取。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from supportops.db.models import Base


class LabRun(Base):
    __tablename__ = "investigation_lab_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_investigation_lab_runs_org",
            ondelete="CASCADE",
        ),
        CheckConstraint("sha256 ~ '^[a-f0-9]{64}$'", name="digest"),
        CheckConstraint("product_version IN ('1.0','1.1','2.0')", name="version"),
        Index("ix_investigation_lab_runs_org_created", "organization_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    product_version: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
