"""建议冻结和调查组织一致性在数据库再约束一次。"""

from alembic import op

revision = "0010_action_guards"
down_revision = "0009_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION guard_action_job() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM investigations WHERE id=NEW.investigation_id
          AND organization_id=NEW.organization_id AND ticket_id=NEW.ticket_id) THEN
          RAISE EXCEPTION 'ACTION_INVESTIGATION_SCOPE_INVALID';
        END IF;
      ELSIF OLD.status <> 'proposing' AND
        (NEW.proposal IS DISTINCT FROM OLD.proposal OR
         NEW.proposal_sha256 IS DISTINCT FROM OLD.proposal_sha256) THEN
        RAISE EXCEPTION 'ACTION_PROPOSAL_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute(
        "CREATE TRIGGER action_job_guard BEFORE INSERT OR UPDATE ON action_jobs "
        "FOR EACH ROW EXECUTE FUNCTION guard_action_job()"
    )


def downgrade():
    op.execute("DROP TRIGGER action_job_guard ON action_jobs")
    op.execute("DROP FUNCTION guard_action_job()")
