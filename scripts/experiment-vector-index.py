"""仅在事务临时表比较真实向量；标签留在本机，结束回滚。"""

import json
import math
import os
import traceback
from datetime import datetime, timezone
from time import perf_counter

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from supportops.chunks.chunking import digest
from supportops.models.provider import ModelFailure, ModelSettings, Provider
from supportops.retrieval.dataset import Qrels, RetrievalDataset
from supportops.retrieval.evaluation import metrics
from supportops.settings import ROOT

OUTPUT = ROOT / "docs/verification/phase-3-round-3/vector-index.json"
TABLE = "supportops_ann_probe"
INDEX = "supportops_ann_probe_hnsw"


def production_indexes(connection):
    return (
        connection.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='retrieval_entries' ORDER BY indexname"
            )
        )
        .mappings()
        .all()
    )


def scoped_query(request, organization, vector):
    # 和公开接口一致：指定资料只约束资料，指定实验只约束日志。
    clauses = [
        "organization_id=CAST(:org AS uuid)",
        "product_version=:version",
        "kind=ANY(CAST(:kinds AS text[]))",
    ]
    parameters = {
        "org": str(organization),
        "version": request.product_version,
        "kinds": request.source_kinds,
        "query": json.dumps(vector),
    }
    if request.document_ids:
        clauses.append("(kind='log' OR document_id=ANY(CAST(:docs AS uuid[])))")
        parameters["docs"] = [str(v) for v in request.document_ids]
    if request.experiment_ids:
        clauses.append("(kind!='log' OR experiment_id=ANY(CAST(:experiments AS uuid[])))")
        parameters["experiments"] = [str(v) for v in request.experiment_ids]
    query = f"SELECT evidence_id, embedding <=> CAST(:query AS vector) AS distance FROM {TABLE} "
    query += "WHERE " + " AND ".join(clauses)
    return query, parameters


def has_index(node):
    return node.get("Index Name") == INDEX or any(has_index(n) for n in node.get("Plans", []))


def p95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def main():
    if OUTPUT.exists():
        raise RuntimeError("拒绝覆盖索引实验。")
    base = ROOT / "data/retrieval/baseline-v1"
    dataset = RetrievalDataset.model_validate_json((base / "dataset.json").read_text("utf-8"))
    qrels = Qrels.model_validate_json((base / "qrels.json").read_text("utf-8"))
    snapshot = json.loads((base / "snapshot.json").read_text("utf-8"))
    assert digest(dataset.model_dump(mode="json")) == qrels.dataset_sha256
    assert dataset.corpus_sha256 == snapshot["index"]["corpus_sha256"]
    tasks = [t for t in dataset.tasks if t.split == "dev"]
    labels = {label.task_id: label.relevance for label in qrels.labels}
    assert len(tasks) == 36
    raw = os.environ.get("SUPPORTOPS_ADMIN_DATABASE_URL") or dotenv_values(ROOT / ".env").get(
        "SUPPORTOPS_ADMIN_DATABASE_URL"
    )
    if not raw or make_url(raw).database != "supportops":
        raise RuntimeError("只允许本项目显式管理员连接。")
    engine = create_engine(raw, hide_parameters=True)
    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "dataset_sha256": qrels.dataset_sha256,
        "qrels_sha256": digest(qrels.model_dump(mode="json")),
        "human_reviewed": False,
        "split": "dev",
        "holdout_executed": False,
        "configuration": {
            "m": 16,
            "ef_construction": 64,
            "ef_search": 40,
            "max_scan_tuples": 20000,
        },
        "embedding_batches": [],
        "groups": [],
        "cost_cny": None,
    }
    before = None
    try:
        with engine.connect() as connection:
            report["database_version"] = connection.scalar(text("SELECT version()"))
            report["vector_version"] = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            before = [dict(r) for r in production_indexes(connection)]
            index = (
                connection.execute(
                    text("SELECT * FROM retrieval_indexes WHERE id=CAST(:id AS uuid)"),
                    {"id": str(dataset.index_id)},
                )
                .mappings()
                .one()
            )
            assert index["corpus_sha256"] == dataset.corpus_sha256
            assert index["entry_count"] == 502 and index["dimensions"] == 1024
            assert index["configuration"] == snapshot["index"]["configuration"]
            assert digest(index["manifest"]) == index["corpus_sha256"]
            # 字段类型明确约束，临时表与索引均只属于当前连接。
            connection.execute(
                text(
                    f"CREATE TEMP TABLE {TABLE} (evidence_id text, organization_id uuid, "
                    "product_version text, kind text, document_id uuid, experiment_id uuid, "
                    "embedding vector(1024)) ON COMMIT DROP"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {TABLE} SELECT evidence_id,organization_id,product_version,"
                    "kind,document_id,experiment_id,embedding FROM retrieval_entries "
                    "WHERE index_id=CAST(:id AS uuid) AND organization_id=CAST(:org AS uuid)"
                ),
                {"id": str(dataset.index_id), "org": str(index["organization_id"])},
            )
            assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 502
            payloads = (
                connection.execute(
                    text(
                        "SELECT evidence_id,payload FROM retrieval_entries "
                        "WHERE index_id=CAST(:id AS uuid)"
                    ),
                    {"id": str(dataset.index_id)},
                )
                .mappings()
                .all()
            )
            frozen = {e["evidence_id"]: e for e in snapshot["entries"]}
            assert len(payloads) == len(frozen) == 502
            assert all(r["payload"] == frozen[r["evidence_id"]]["payload"] for r in payloads)
            report["entries"] = 502
            vectors = []
            settings = ModelSettings()
            assert settings.embedding_dimensions == 1024
            assert settings.embedding_model == index["configuration"]["model"]
            assert settings.base_url == index["configuration"]["endpoint"]
            with Provider(settings) as provider:
                for offset in range(0, len(tasks), 10):
                    batch = provider.embed_texts(
                        [t.request.query for t in tasks[offset : offset + 10]]
                    )
                    vectors.extend(batch.pop("vectors"))
                    report["embedding_batches"].append(batch)
            # 精确组先执行，确保不存在近似索引；每个查询先预热一次，再测一次。
            for name in ("exact", "hnsw-off", "hnsw-strict"):
                if name == "hnsw-off":
                    started = perf_counter()
                    connection.execute(
                        text(
                            f"CREATE INDEX {INDEX} ON {TABLE} USING hnsw "
                            "(embedding vector_cosine_ops) WITH (m=16,ef_construction=64)"
                        )
                    )
                    report["build_ms"] = round((perf_counter() - started) * 1000, 3)
                    report["index_bytes"] = connection.scalar(
                        text(f"SELECT pg_relation_size('{INDEX}')")
                    )
                connection.execute(
                    text(f"SET LOCAL enable_seqscan={'on' if name == 'exact' else 'off'}")
                )
                connection.execute(
                    text(f"SET LOCAL enable_indexscan={'off' if name == 'exact' else 'on'}")
                )
                connection.execute(text("SET LOCAL enable_bitmapscan=off"))
                if name != "exact":
                    connection.execute(text("SET LOCAL hnsw.ef_search=40"))
                    connection.execute(text("SET LOCAL hnsw.max_scan_tuples=20000"))
                    connection.execute(
                        text(
                            "SET LOCAL hnsw.iterative_scan="
                            + ("'off'" if name == "hnsw-off" else "'strict_order'")
                        )
                    )
                rows = []
                report["groups"].append({"name": name, "status": "running", "attempts": rows})
                for task, vector in zip(tasks, vectors, strict=True):
                    inner, parameters = scoped_query(task.request, index["organization_id"], vector)
                    sql = (
                        (inner + " ORDER BY distance,evidence_id LIMIT 5")
                        if name == "exact"
                        else (
                            "SELECT * FROM (" + inner + " ORDER BY distance LIMIT 5) candidates "
                            "ORDER BY distance,evidence_id"
                        )
                    )
                    connection.execute(text(sql), parameters).all()
                    started = perf_counter()
                    ranked = [r.evidence_id for r in connection.execute(text(sql), parameters)]
                    wall = round((perf_counter() - started) * 1000, 3)
                    plan = connection.scalar(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), parameters
                    )[0]
                    assert has_index(plan["Plan"]) == (name != "exact")
                    exact_ids = (
                        ranked
                        if name == "exact"
                        else next(
                            a["ranked_ids"]
                            for a in report["groups"][0]["attempts"]
                            if a["task_id"] == task.task_id
                        )
                    )
                    rows.append(
                        {
                            "task_id": task.task_id,
                            "ranked_ids": ranked,
                            "metrics": metrics(ranked, labels[task.task_id], 5),
                            "overlap_at_5": len(set(ranked).intersection(exact_ids))
                            / len(exact_ids),
                            "underfilled_vs_exact": len(ranked) < len(exact_ids),
                            "sql_wall_ms": wall,
                            "plan": plan,
                            "index_used": has_index(plan["Plan"]),
                        }
                    )
                report["groups"][-1].update(
                    status="completed",
                    summary={
                        "total": len(rows),
                        "overlap_at_5": sum(r["overlap_at_5"] for r in rows) / len(rows),
                        "underfilled_queries": sum(r["underfilled_vs_exact"] for r in rows),
                        "sql_p95_ms": p95([r["sql_wall_ms"] for r in rows]),
                        "execution_p95_ms": p95([r["plan"]["Execution Time"] for r in rows]),
                        "macro_all": {
                            k: sum(r["metrics"][k] for r in rows) / len(rows)
                            for k in ("recall", "mrr", "ndcg")
                        },
                    },
                )
            connection.rollback()
            assert connection.scalar(text(f"SELECT to_regclass('{TABLE}')")) is None
            report["temporary_table_removed"] = True
            assert [dict(r) for r in production_indexes(connection)] == before
            report["production_indexes_unchanged"] = True
            report["status"] = "completed"
    except Exception as failure:
        report["status"] = "failed"
        report["error_type"] = type(failure).__name__
        frame = traceback.extract_tb(failure.__traceback__)[-1]
        report["error_location"] = {
            "file": frame.filename.rsplit("\\", 1)[-1],
            "line": frame.lineno,
        }
        report["error_code"] = failure.code if isinstance(failure, ModelFailure) else None
        if isinstance(failure, ModelFailure):
            report["unknown_usage_calls"] = int(failure.code != "MODEL_NOT_CONFIGURED")
    finally:
        report["known_input_tokens"] = sum(b["input_tokens"] for b in report["embedding_batches"])
        report["known_model_calls"] = len(report["embedding_batches"])
        if before is not None:
            with engine.connect() as connection:
                report["production_indexes_unchanged"] = [
                    dict(r) for r in production_indexes(connection)
                ] == before
                report["temporary_table_removed"] = (
                    connection.scalar(text(f"SELECT to_regclass('{TABLE}')")) is None
                )
        engine.dispose()
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("embedding_batches", "groups")},
            ensure_ascii=False,
        )
    )
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
