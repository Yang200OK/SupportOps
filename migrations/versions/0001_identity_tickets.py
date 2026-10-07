"""建立组织、身份会话与具有组织隔离的工单。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_identity_tickets"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "organizations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.UniqueConstraint("name", name="uq_organizations_name"),
    )
    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("id", "organization_id", name="uq_users_id_organization"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "tickets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("organization_id", uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("requester_id", uuid, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("product", sa.String(32), nullable=False),
        sa.Column("product_version", sa.String(16)),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("intake_status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["requester_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_tickets_requester_organization",
        ),
        sa.CheckConstraint("product = 'relaydesk'", name="ck_tickets_product"),
        sa.CheckConstraint(
            "product_version IS NULL OR product_version IN ('1.0','1.1','2.0')",
            name="ck_tickets_product_version",
        ),
        sa.CheckConstraint("environment = 'local_lab'", name="ck_tickets_environment"),
        sa.CheckConstraint(
            "source_type IN ('synthetic_case','user_report')", name="ck_tickets_source_type"
        ),
        sa.CheckConstraint(
            "length(btrim(title)) BETWEEN 1 AND 200", name="ck_tickets_title_length"
        ),
        sa.CheckConstraint(
            "length(btrim(description)) BETWEEN 1 AND 10000", name="ck_tickets_description_length"
        ),
        sa.CheckConstraint(
            "(product_version IS NULL AND intake_status = 'needs_clarification') "
            "OR (product_version IS NOT NULL AND intake_status = 'ready_for_intake')",
            name="ck_tickets_intake_status",
        ),
    )
    op.create_index(
        "ix_tickets_organization_created_id", "tickets", ["organization_id", "created_at", "id"]
    )
    # 当前角色无上下文时读取零行；FORCE 避免普通 owner 意外绕过。
    op.execute("ALTER TABLE tickets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tickets FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tickets_organization_scope ON tickets "
        "USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) "
        "WITH CHECK (organization_id = "
        "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT ON organizations, users, alembic_version TO supportops_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON auth_sessions TO supportops_app")
    op.execute("GRANT SELECT, INSERT ON tickets TO supportops_app")


def downgrade() -> None:
    # 显式降级是破坏性动作；运行脚本不提供自动降级或重建数据库。
    op.drop_table("tickets")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("organizations")
