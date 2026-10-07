"""复盘、候选及追加治理记录采用组织 RLS 和最小写权限。"""

from alembic import op

revision = "0011_memories"
down_revision = "0010_action_guards"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE retrospectives (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      ticket_id uuid NOT NULL, investigation_id uuid NOT NULL, request_id uuid NOT NULL,
      request jsonb NOT NULL, source jsonb NOT NULL, source_sha256 varchar(64) NOT NULL,
      summary jsonb NOT NULL, summary_sha256 varchar(64) NOT NULL, created_at timestamptz NOT NULL,
      CONSTRAINT uq_retrospective_org UNIQUE(id,organization_id),
      CONSTRAINT uq_memory_request UNIQUE(organization_id,requester_id,request_id),
      FOREIGN KEY(ticket_id,organization_id) REFERENCES tickets(id,organization_id)
        ON DELETE CASCADE,
      FOREIGN KEY(requester_id,organization_id) REFERENCES users(id,organization_id),
      FOREIGN KEY(investigation_id) REFERENCES investigations(id) ON DELETE CASCADE,
      CHECK(source_sha256 ~ '^[a-f0-9]{64}$' AND summary_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("""
    CREATE TABLE experience_candidates (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, retrospective_id uuid NOT NULL,
      product_version varchar(16) NOT NULL, environment varchar(32) NOT NULL,
      mode varchar(16) NOT NULL, body jsonb NOT NULL, body_sha256 varchar(64) NOT NULL,
      status varchar(16) NOT NULL, revision integer NOT NULL,
      expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL,
      CONSTRAINT uq_experience_org UNIQUE(id,organization_id),
      CONSTRAINT uq_experience_retrospective UNIQUE(retrospective_id),
      FOREIGN KEY(retrospective_id,organization_id) REFERENCES retrospectives(id,organization_id)
        ON DELETE CASCADE,
      CHECK(product_version IN ('1.0','1.1','2.0') AND environment='local_lab'),
      CHECK(mode IN ('startup','online') AND status IN ('candidate','invalidated','revoked')),
      CHECK(revision>=1 AND expires_at>created_at AND expires_at<=created_at+interval '30 days'),
      CHECK(body_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("""
    CREATE TABLE memory_events (
      candidate_id uuid NOT NULL, revision integer NOT NULL, organization_id uuid NOT NULL,
      data jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(candidate_id,revision),
      FOREIGN KEY(candidate_id,organization_id)
        REFERENCES experience_candidates(id,organization_id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE memory_conflicts (
      left_id uuid NOT NULL, right_id uuid NOT NULL, organization_id uuid NOT NULL,
      data jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(left_id,right_id), CHECK(left_id<right_id),
      FOREIGN KEY(left_id,organization_id)
        REFERENCES experience_candidates(id,organization_id) ON DELETE CASCADE,
      FOREIGN KEY(right_id,organization_id)
        REFERENCES experience_candidates(id,organization_id) ON DELETE CASCADE
    )
    """)
    for table in ("retrospectives", "experience_candidates", "memory_events", "memory_conflicts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO supportops_app")
    op.execute("GRANT UPDATE(status,revision) ON experience_candidates TO supportops_app")
    op.execute(
        "CREATE INDEX ix_memory_ticket ON retrospectives(organization_id,ticket_id,created_at,id)"
    )
    op.execute(
        "CREATE INDEX ix_memory_recall ON experience_candidates"
        "(organization_id,product_version,mode,created_at)"
    )
    op.execute("""
    CREATE FUNCTION guard_memory_scope() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM investigations WHERE id=NEW.investigation_id
        AND organization_id=NEW.organization_id AND ticket_id=NEW.ticket_id) THEN
        RAISE EXCEPTION 'MEMORY_INVESTIGATION_SCOPE_INVALID';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER memory_scope_guard BEFORE INSERT ON retrospectives "
        "FOR EACH ROW EXECUTE FUNCTION guard_memory_scope()"
    )
    op.execute("""
    CREATE FUNCTION guard_memory_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.revision<>OLD.revision+1 OR OLD.status<>'candidate' OR
        NEW.status NOT IN ('candidate','invalidated','revoked') THEN
        RAISE EXCEPTION 'MEMORY_REVISION_INVALID';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER memory_revision_guard BEFORE UPDATE ON experience_candidates "
        "FOR EACH ROW EXECUTE FUNCTION guard_memory_revision()"
    )


def downgrade():
    for table in ("memory_conflicts", "memory_events", "experience_candidates", "retrospectives"):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_memory_scope()")
    op.execute("DROP FUNCTION guard_memory_revision()")
