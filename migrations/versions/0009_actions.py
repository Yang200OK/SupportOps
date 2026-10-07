"""动作检查点与不可变批准 / 事件均受组织 RLS 控制。"""

from alembic import op

revision = "0009_actions"
down_revision = "0008_live_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE action_jobs (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      ticket_id uuid NOT NULL, investigation_id uuid NOT NULL, request_id uuid NOT NULL,
      status varchar(32) NOT NULL, proposal jsonb NOT NULL, proposal_sha256 varchar(64) NOT NULL,
      checkpoint jsonb NOT NULL, checkpoint_sha256 varchar(64) NOT NULL,
      sequence integer NOT NULL DEFAULT 0, expires_at timestamptz NOT NULL,
      lease_until timestamptz, created_at timestamptz NOT NULL,
      UNIQUE(organization_id,requester_id,request_id), UNIQUE(id,organization_id),
      FOREIGN KEY (ticket_id,organization_id) REFERENCES tickets(id,organization_id)
        ON DELETE CASCADE,
      FOREIGN KEY (requester_id,organization_id) REFERENCES users(id,organization_id),
      FOREIGN KEY (investigation_id) REFERENCES investigations(id) ON DELETE CASCADE,
      CHECK (proposal_sha256 ~ '^[a-f0-9]{64}$' AND checkpoint_sha256 ~ '^[a-f0-9]{64}$'),
      CHECK (sequence >= 0),
      CHECK (status IN ('proposing','pending','approved','rejected','action_running','action_done',
        'retest_running','completed','failed','cancel_requested','cancelled','uncertain'))
    )
    """)
    op.execute("""
    CREATE TABLE action_approvals (
      job_id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      session_id uuid NOT NULL, proposal_sha256 varchar(64) NOT NULL,
      decision varchar(16) NOT NULL CHECK (decision IN ('approve','reject')),
      created_at timestamptz NOT NULL,
      FOREIGN KEY(job_id,organization_id) REFERENCES action_jobs(id,organization_id)
        ON DELETE CASCADE,
      FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id)
    )
    """)
    op.execute("""
    CREATE TABLE action_events (
      job_id uuid NOT NULL, organization_id uuid NOT NULL, sequence integer NOT NULL,
      data jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(job_id,sequence),
      FOREIGN KEY(job_id,organization_id) REFERENCES action_jobs(id,organization_id)
        ON DELETE CASCADE
    )
    """)
    for table in ("action_jobs", "action_approvals", "action_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO supportops_app")
    op.execute(
        "GRANT UPDATE(status,checkpoint,checkpoint_sha256,sequence,lease_until,"
        "proposal,proposal_sha256) ON action_jobs TO supportops_app"
    )
    op.execute(
        "CREATE INDEX ix_action_jobs_ticket ON action_jobs(organization_id,ticket_id,created_at,id)"
    )


def downgrade():
    for table in ("action_events", "action_approvals", "action_jobs"):
        op.drop_table(table)
