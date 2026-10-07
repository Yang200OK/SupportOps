"""独立只读调查结果，应用只能插入和读取。"""

from alembic import op

revision = "0007_investigations"
down_revision = "0006_retrieval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE investigations (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, ticket_id uuid NOT NULL,
      requester_id uuid NOT NULL, status varchar(32) NOT NULL,
      workflow_version varchar(64) NOT NULL, input_snapshot jsonb NOT NULL,
      input_sha256 varchar(64) NOT NULL, output jsonb NOT NULL, output_sha256 varchar(64) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_investigations_ticket FOREIGN KEY (ticket_id,organization_id)
        REFERENCES tickets(id,organization_id) ON DELETE CASCADE,
      CONSTRAINT fk_investigations_requester FOREIGN KEY (requester_id,organization_id)
        REFERENCES users(id,organization_id),
      CONSTRAINT ck_investigations_digests CHECK
        (input_sha256 ~ '^[a-f0-9]{64}$' AND output_sha256 ~ '^[a-f0-9]{64}$'),
      CONSTRAINT ck_investigations_status CHECK
        (status IN ('completed','failed','stopped','no_evidence','needs_clarification'))
    )
    """)
    op.execute(
        "CREATE INDEX ix_investigations_organization_created_id "
        "ON investigations (organization_id,created_at,id)"
    )
    op.execute("ALTER TABLE investigations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE investigations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY investigations_organization_scope ON investigations "
        "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
        "WITH CHECK (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON investigations TO supportops_app")


def downgrade():
    op.drop_table("investigations")
