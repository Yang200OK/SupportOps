"""追加不可变公共实验观测表；不保存控制与答案。"""

from alembic import op

revision = "0005_experiments"
down_revision = "0004_chunk_sets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE experiments (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      run_id uuid NOT NULL, product_version varchar(20) NOT NULL,
      sha256 varchar(64) NOT NULL, artifact jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_experiments_requester_organization FOREIGN KEY (requester_id,organization_id)
        REFERENCES users(id,organization_id),
      CONSTRAINT uq_experiments_run UNIQUE (organization_id,run_id),
      CONSTRAINT ck_experiments_version CHECK (product_version IN ('1.0','1.1','2.0')),
      CONSTRAINT ck_experiments_digest CHECK (sha256 ~ '^[a-f0-9]{64}$'),
      CONSTRAINT ck_experiments_observations CHECK
        (jsonb_array_length(artifact->'observations') BETWEEN 3 AND 200)
    )
    """)
    op.execute("ALTER TABLE experiments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY experiments_organization_scope ON experiments "
        "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
        "WITH CHECK (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON experiments TO supportops_app")


def downgrade():
    op.drop_table("experiments")
