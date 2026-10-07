"""只读核对 PostgreSQL 版本、向量扩展和实际精确排序计划。"""

import json

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from supportops.settings import ROOT


def main():
    snapshot = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    config = dotenv_values(ROOT / ".env")
    admin = create_engine(config["SUPPORTOPS_ADMIN_DATABASE_URL"], hide_parameters=True)
    app = create_engine(config["SUPPORTOPS_DATABASE_URL"], hide_parameters=True)
    try:
        with admin.connect() as c:
            index_id = snapshot["index"]["index_id"]
            org = c.scalar(
                text("SELECT organization_id FROM retrieval_indexes WHERE id=:id"), {"id": index_id}
            )
            extension = c.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
            server = c.scalar(text("SHOW server_version"))
            indexes = (
                c.execute(
                    text(
                        "SELECT indexname,indexdef FROM pg_indexes "
                        "WHERE tablename='retrieval_entries'"
                    )
                )
                .mappings()
                .all()
            )
            assert not any(
                "hnsw" in r["indexdef"].lower() or "ivfflat" in r["indexdef"].lower()
                for r in indexes
            )
        with app.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM retrieval_indexes")) == 0
            c.execute(text("SELECT set_config('app.organization_id',:org,true)"), {"org": str(org)})
            params = {"id": index_id, "org": org}
            plan = c.scalar(
                text("""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                SELECT evidence_id,
                  embedding <=> (SELECT embedding FROM retrieval_entries
                    WHERE index_id=:id AND organization_id=:org ORDER BY evidence_id LIMIT 1)
                  AS distance
                FROM retrieval_entries
                WHERE index_id=:id AND organization_id=:org
                  AND product_version='1.1' AND kind='document'
                ORDER BY distance, evidence_id LIMIT 5
                """),
                params,
            )
            count = c.scalar(
                text(
                    "SELECT count(*) FROM retrieval_entries "
                    "WHERE index_id=:id AND organization_id=:org"
                ),
                params,
            )
            assert count == snapshot["index"]["entry_count"]
        report = {
            "server_version": server,
            "vector_extension": extension,
            "existing_postgres_major_preserved": server.startswith("17."),
            "vector_ann_indexes": 0,
            "indexes": [dict(r) for r in indexes],
            "entry_count": count,
            "rls_without_scope_returns_zero": True,
            "explain_analyze": plan,
            "note": "此计划用保存的条目向量核对排序执行；不是模型质量或规模性能实验。",
        }
        (ROOT / "docs/verification/phase-3-round-1/database.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"精确计划核对通过：PostgreSQL {server} / vector {extension}，{count} 条。")
    finally:
        admin.dispose()
        app.dispose()


if __name__ == "__main__":
    main()
