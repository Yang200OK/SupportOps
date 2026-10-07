"""追加不可修改、具有组织隔离的运行记录。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_run_records"
down_revision = "0001_identity_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_unique_constraint("uq_tickets_id_organization", "tickets", ["id", "organization_id"])
    op.create_table(
        "runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("ticket_id", uuid, nullable=False),
        sa.Column("requester_id", uuid, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("workflow_version", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("output", postgresql.JSONB, nullable=False),
        sa.Column("events", postgresql.JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["tickets.id", "tickets.organization_id"],
            name="fk_runs_ticket_organization",
        ),
        sa.ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_runs_requester_organization",
        ),
        sa.CheckConstraint("kind = 'intake_check'", name="ck_runs_kind"),
        sa.CheckConstraint("status IN ('succeeded','blocked')", name="ck_runs_status"),
        sa.CheckConstraint("duration_ms >= 0 AND finished_at >= started_at", name="ck_runs_timing"),
        sa.CheckConstraint("input_sha256 ~ '^[a-f0-9]{64}$'", name="ck_runs_digest"),
    )
    op.create_index(
        "ix_runs_organization_started_id", "runs", ["organization_id", "started_at", "id"]
    )
    op.execute("ALTER TABLE runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY runs_organization_scope ON runs "
        "USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) "
        "WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON runs TO supportops_app")


def downgrade() -> None:
    op.drop_table("runs")
    op.drop_constraint("uq_tickets_id_organization", "tickets", type_="unique")
