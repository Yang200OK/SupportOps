"""组织内方法发布隔离于固定文件目录；内容与审批追加保存。"""

from alembic import op

revision = "0012_skill_publications"
down_revision = "0011_memories"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE skill_drafts (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      request_id uuid NOT NULL, candidate_id uuid NOT NULL,
      payload jsonb NOT NULL, payload_sha256 varchar(64) NOT NULL,
      status varchar(16) NOT NULL, revision integer NOT NULL, created_at timestamptz NOT NULL,
      CONSTRAINT uq_skill_draft_org UNIQUE(id,organization_id),
      CONSTRAINT uq_skill_draft_request UNIQUE(organization_id,requester_id,request_id),
      FOREIGN KEY(requester_id,organization_id) REFERENCES users(id,organization_id),
      FOREIGN KEY(candidate_id,organization_id)
        REFERENCES experience_candidates(id,organization_id) ON DELETE CASCADE,
      CHECK(status IN ('draft','approved','rejected','published','revoked') AND revision>=1),
      CHECK(payload_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    for table, column in (("skill_regressions", "report"), ("skill_decisions", "data")):
        extra = (
            ", requester_id uuid NOT NULL, request_id uuid NOT NULL, "
            "CONSTRAINT uq_skill_decision_request UNIQUE(organization_id,requester_id,request_id), "
            "FOREIGN KEY(requester_id,organization_id) REFERENCES users(id,organization_id)"
            if table == "skill_decisions"
            else ""
        )
        op.execute(f"""
        CREATE TABLE {table} (
          id uuid PRIMARY KEY, draft_id uuid NOT NULL, organization_id uuid NOT NULL,
          {column} jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{{64}}$')
          {extra},
          FOREIGN KEY(draft_id,organization_id)
            REFERENCES skill_drafts(id,organization_id) ON DELETE CASCADE
        )
        """)
    op.execute("""
    CREATE TABLE skill_publication_events (
      draft_id uuid NOT NULL, revision integer NOT NULL, organization_id uuid NOT NULL,
      data jsonb NOT NULL, sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      PRIMARY KEY(draft_id,revision),
      FOREIGN KEY(draft_id,organization_id)
        REFERENCES skill_drafts(id,organization_id) ON DELETE CASCADE
    )
    """)
    for table in (
        "skill_drafts",
        "skill_regressions",
        "skill_decisions",
        "skill_publication_events",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id=NULLIF("
            "current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO supportops_app")
    op.execute("GRANT UPDATE(status,revision) ON skill_drafts TO supportops_app")
    op.execute("""
    CREATE FUNCTION guard_skill_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.revision<>OLD.revision+1 OR NOT (
        (OLD.status='draft' AND NEW.status IN ('approved','rejected')) OR
        (OLD.status='approved' AND NEW.status IN ('published','revoked')) OR
        (OLD.status='published' AND NEW.status='revoked')
      ) THEN RAISE EXCEPTION 'SKILL_REVISION_INVALID'; END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER skill_revision_guard BEFORE UPDATE ON skill_drafts "
        "FOR EACH ROW EXECUTE FUNCTION guard_skill_revision()"
    )


def downgrade():
    for table in (
        "skill_publication_events",
        "skill_decisions",
        "skill_regressions",
        "skill_drafts",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_skill_revision()")
