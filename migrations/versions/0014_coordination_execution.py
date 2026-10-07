"""独立执行及不可变状态回执审计。"""

from alembic import op

revision = "0014_coordination_execution"
down_revision = "0013_coordination"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE coordination_executions (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      request_id uuid NOT NULL, board_id uuid NOT NULL,
      binding jsonb NOT NULL, binding_sha256 varchar(64) NOT NULL,
      state jsonb NOT NULL, state_sha256 varchar(64) NOT NULL,
      revision integer NOT NULL CHECK(revision>=1), created_at timestamptz NOT NULL,
      CONSTRAINT uq_coordination_execution_org UNIQUE(id,organization_id),
      CONSTRAINT uq_coordination_execution_request UNIQUE(organization_id,requester_id,request_id),
      FOREIGN KEY(board_id,organization_id)
        REFERENCES coordination_boards(id,organization_id) ON DELETE CASCADE,
      FOREIGN KEY(requester_id,organization_id) REFERENCES users(id,organization_id),
      CHECK(binding_sha256 ~ '^[a-f0-9]{64}$' AND state_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("""
    CREATE TABLE coordination_execution_events (
      execution_id uuid NOT NULL, revision integer NOT NULL,
      organization_id uuid NOT NULL, data jsonb NOT NULL,
      sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(execution_id,revision),
      FOREIGN KEY(execution_id,organization_id)
        REFERENCES coordination_executions(id,organization_id) ON DELETE CASCADE
    )
    """)
    for table in ("coordination_executions", "coordination_execution_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_scope ON {table} "
            "USING (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO supportops_app")
    op.execute(
        "GRANT UPDATE(state,state_sha256,revision) ON coordination_executions TO supportops_app"
    )
    op.execute("""
    CREATE FUNCTION guard_execution_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.revision<>OLD.revision+1 THEN RAISE EXCEPTION 'EXECUTION_REVISION_INVALID'; END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER execution_revision_guard BEFORE UPDATE ON coordination_executions "
        "FOR EACH ROW EXECUTE FUNCTION guard_execution_revision()"
    )


def downgrade():
    op.drop_table("coordination_execution_events")
    op.drop_table("coordination_executions")
    op.execute("DROP FUNCTION guard_execution_revision()")
