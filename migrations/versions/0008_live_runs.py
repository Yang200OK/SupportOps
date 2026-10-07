"""固定本次实验范围，不给应用注册或修改目标的权限。"""

from alembic import op

revision = "0008_live_runs"
down_revision = "0007_investigations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE investigation_lab_runs (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, product_version varchar(20) NOT NULL,
      snapshot jsonb NOT NULL, sha256 varchar(64) NOT NULL, expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_investigation_lab_runs_org FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE CASCADE,
      CONSTRAINT ck_investigation_lab_runs_digest CHECK (sha256 ~ '^[a-f0-9]{64}$'),
      CONSTRAINT ck_investigation_lab_runs_version CHECK (product_version IN ('1.0','1.1','2.0'))
    )
    """)
    op.execute(
        "CREATE INDEX ix_investigation_lab_runs_org_created "
        "ON investigation_lab_runs (organization_id,created_at,id)"
    )
    op.execute("ALTER TABLE investigation_lab_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE investigation_lab_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY investigation_lab_runs_org_scope ON investigation_lab_runs "
        "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
        "WITH CHECK (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid)"
    )
    op.execute("GRANT SELECT ON investigation_lab_runs TO supportops_app")


def downgrade():
    op.drop_table("investigation_lab_runs")
