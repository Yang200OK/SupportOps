"""独立任务板与追加审计，不修改旧调查记录。"""

from alembic import op

revision = "0013_coordination"
down_revision = "0012_skill_publications"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE coordination_boards (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      request_id uuid NOT NULL, ticket_id uuid NOT NULL,
      input_snapshot jsonb NOT NULL, input_sha256 varchar(64) NOT NULL,
      plan jsonb NOT NULL, plan_sha256 varchar(64) NOT NULL,
      status varchar(16) NOT NULL, revision integer NOT NULL, created_at timestamptz NOT NULL,
      CONSTRAINT uq_coordination_board_org UNIQUE(id,organization_id),
      CONSTRAINT uq_coordination_board_request UNIQUE(organization_id,requester_id,request_id),
      FOREIGN KEY(ticket_id,organization_id)
        REFERENCES tickets(id,organization_id) ON DELETE CASCADE,
      FOREIGN KEY(requester_id,organization_id) REFERENCES users(id,organization_id),
      CHECK(status IN ('planned','cancelled') AND revision >= 1),
      CHECK(input_sha256 ~ '^[a-f0-9]{64}$' AND plan_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("""
    CREATE INDEX ix_coordination_board_org_ticket
      ON coordination_boards(organization_id,ticket_id,created_at)
    """)
    op.execute("""
    CREATE TABLE coordination_events (
      board_id uuid NOT NULL, revision integer NOT NULL, organization_id uuid NOT NULL,
      data jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(board_id,revision),
      FOREIGN KEY(board_id,organization_id)
        REFERENCES coordination_boards(id,organization_id) ON DELETE CASCADE
    )
    """)
    for table in ("coordination_boards", "coordination_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO supportops_app")
    op.execute("GRANT UPDATE(status,revision) ON coordination_boards TO supportops_app")
    op.execute("""
    CREATE FUNCTION guard_coordination_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status<>'planned' OR NEW.status<>'cancelled' OR NEW.revision<>OLD.revision+1
      THEN RAISE EXCEPTION 'COORDINATION_REVISION_INVALID'; END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER coordination_revision_guard BEFORE UPDATE ON coordination_boards "
        "FOR EACH ROW EXECUTE FUNCTION guard_coordination_revision()"
    )


def downgrade():
    op.drop_table("coordination_events")
    op.drop_table("coordination_boards")
    op.execute("DROP FUNCTION guard_coordination_revision()")
