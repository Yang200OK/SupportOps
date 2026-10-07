"""追加有组织隔离的不可变资料来源与修订，不改历史迁移。"""

from alembic import op

revision = "0003_documents"
down_revision = "0002_run_records"
branch_labels = None
depends_on = None


def upgrade():
    # 仅冻结本轮两张表，不让后续 ORM 修改影响已发布的迁移。
    op.execute("""
    CREATE TABLE documents (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
      product varchar(32) NOT NULL, product_version varchar(16) NOT NULL,
      source_key varchar(100) NOT NULL, source_type varchar(32) NOT NULL,
      license varchar(32) NOT NULL, filename varchar(150) NOT NULL, format varchar(16) NOT NULL,
      CONSTRAINT uq_documents_source UNIQUE (organization_id,product,product_version,source_key),
      CONSTRAINT uq_documents_id_organization UNIQUE (id,organization_id),
      CONSTRAINT ck_documents_scope CHECK (
        product='relaydesk' AND product_version IN ('1.0','1.1','2.0')),
      CONSTRAINT ck_documents_source_type CHECK (
        source_type IN ('demo_product','synthetic_case','user_report')),
      CONSTRAINT ck_documents_format CHECK (format IN ('md','pdf','json')),
      CONSTRAINT ck_documents_license CHECK (license IN ('CC0-1.0','proprietary'))
    )
    """)
    op.execute("""
    CREATE TABLE document_revisions (
      id uuid PRIMARY KEY, document_id uuid NOT NULL,
      organization_id uuid NOT NULL REFERENCES organizations(id), importer_id uuid NOT NULL,
      revision_number integer NOT NULL, title varchar(200) NOT NULL,
      content_sha256 varchar(64) NOT NULL, byte_size integer NOT NULL, raw_bytes bytea NOT NULL,
      imported_at timestamptz NOT NULL DEFAULT now(), parser_version varchar(64) NOT NULL,
      status varchar(16) NOT NULL, text text, blocks jsonb NOT NULL,
      error_code varchar(64), error_message text,
      CONSTRAINT fk_document_revisions_document_organization 
        FOREIGN KEY (document_id,organization_id) REFERENCES documents(id,organization_id),
      CONSTRAINT fk_document_revisions_importer_organization 
        FOREIGN KEY (importer_id,organization_id) REFERENCES users(id,organization_id),
      CONSTRAINT uq_document_revisions_content UNIQUE (document_id,content_sha256),
      CONSTRAINT uq_document_revisions_number UNIQUE (document_id,revision_number),
      CONSTRAINT ck_document_revisions_size CHECK (
        revision_number>0 AND byte_size BETWEEN 0 AND 2097152 
        AND octet_length(raw_bytes)=byte_size),
      CONSTRAINT ck_document_revisions_digest CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
      CONSTRAINT ck_document_revisions_state CHECK (
        (status='parsed' AND text IS NOT NULL AND error_code IS NULL AND error_message IS NULL 
        AND jsonb_array_length(blocks)>0)
        OR (status='failed' AND text IS NULL AND error_code IS NOT NULL
        AND error_message IS NOT NULL 
        AND blocks='[]'::jsonb))
    )
    """)
    for table in ("documents", "document_revisions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_organization_scope ON {table} "
            "USING (organization_id=NULLIF(current_setting('app.organization_id',true),'')::uuid) "
            "WITH CHECK (organization_id="
            "NULLIF(current_setting('app.organization_id',true),'')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO supportops_app")


def downgrade():
    op.drop_table("document_revisions")
    op.drop_table("documents")
