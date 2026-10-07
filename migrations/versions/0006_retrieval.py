"""追加向量快照与逐条证据，应用只能插入 / 读取。"""

from alembic import op

revision = "0006_retrieval"
down_revision = "0005_experiments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector VERSION '0.8.7'")
    op.execute("""
    CREATE TABLE retrieval_indexes (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, requester_id uuid NOT NULL,
      fingerprint varchar(64) NOT NULL, corpus_sha256 varchar(64) NOT NULL,
      dimensions integer NOT NULL, entry_count integer NOT NULL,
      configuration jsonb NOT NULL, manifest jsonb NOT NULL, build_usage jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_retrieval_indexes_fingerprint UNIQUE (organization_id,fingerprint),
      CONSTRAINT uq_retrieval_indexes_identity UNIQUE (id,organization_id,dimensions),
      CONSTRAINT fk_retrieval_indexes_requester_organization
        FOREIGN KEY (requester_id,organization_id)
        REFERENCES users(id,organization_id),
      CONSTRAINT ck_retrieval_indexes_bounds CHECK
        (dimensions BETWEEN 1 AND 4096 AND entry_count BETWEEN 1 AND 2000),
      CONSTRAINT ck_retrieval_indexes_digests CHECK
        (fingerprint ~ '^[a-f0-9]{64}$' AND corpus_sha256 ~ '^[a-f0-9]{64}$')
    )
    """)
    op.execute("""
    CREATE TABLE retrieval_entries (
      index_id uuid NOT NULL, evidence_id varchar(160) NOT NULL,
      organization_id uuid NOT NULL, dimensions integer NOT NULL,
      product_version varchar(16) NOT NULL, kind varchar(16) NOT NULL,
      document_id uuid, experiment_id uuid, payload jsonb NOT NULL, embedding vector NOT NULL,
      PRIMARY KEY (index_id,evidence_id),
      CONSTRAINT fk_retrieval_entries_index_identity
        FOREIGN KEY (index_id,organization_id,dimensions)
        REFERENCES retrieval_indexes(id,organization_id,dimensions),
      CONSTRAINT ck_retrieval_entries_vector CHECK
        (vector_dims(embedding)=dimensions AND vector_norm(embedding)>0),
      CONSTRAINT ck_retrieval_entries_scope CHECK
        (product_version IN ('1.0','1.1','2.0') AND kind IN ('document','case','log'))
    )
    """)
    for table in ("retrieval_indexes", "retrieval_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_organization_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK "
            "(organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO supportops_app")


def downgrade():
    op.drop_table("retrieval_entries")
    op.drop_table("retrieval_indexes")
