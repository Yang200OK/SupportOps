"""追加不可变切片快照和修订组合身份。"""

from alembic import op

revision = "0004_chunk_sets"
down_revision = "0003_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_document_revisions_identity",
        "document_revisions",
        ["id", "document_id", "organization_id"],
    )
    op.execute("""
    CREATE TABLE chunk_sets (
      id uuid PRIMARY KEY, document_id uuid NOT NULL, revision_id uuid NOT NULL,
      organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      chunker_version varchar(64) NOT NULL, config jsonb NOT NULL,
      content_sha256 varchar(64) NOT NULL, parsed_sha256 varchar(64) NOT NULL,
      config_sha256 varchar(64) NOT NULL, snapshot_sha256 varchar(64) NOT NULL,
      parent_count integer NOT NULL, chunk_count integer NOT NULL,
      parents jsonb NOT NULL, chunks jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT fk_chunk_sets_revision_identity
        FOREIGN KEY (revision_id,document_id,organization_id)
        REFERENCES document_revisions(id,document_id,organization_id),
      CONSTRAINT fk_chunk_sets_requester_organization FOREIGN KEY (requester_id,organization_id)
        REFERENCES users(id,organization_id),
      CONSTRAINT uq_chunk_sets_configuration UNIQUE (revision_id,chunker_version,config_sha256),
      CONSTRAINT ck_chunk_sets_chunker_version CHECK (chunker_version='structure-v1'),
      CONSTRAINT ck_chunk_sets_counts CHECK (parent_count BETWEEN 1 AND 3000
        AND chunk_count BETWEEN 1 AND 3000 AND jsonb_array_length(parents)=parent_count
        AND jsonb_array_length(chunks)=chunk_count),
      CONSTRAINT ck_chunk_sets_digests CHECK (content_sha256 ~ '^[a-f0-9]{64}$'
        AND parsed_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$'
        AND snapshot_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("ALTER TABLE chunk_sets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chunk_sets FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY chunk_sets_organization_scope ON chunk_sets "
        "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
        "WITH CHECK (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT ON chunk_sets TO supportops_app")


def downgrade():
    op.drop_table("chunk_sets")
    op.drop_constraint("uq_document_revisions_identity", "document_revisions", type_="unique")
