"""读取三份实跑报告，对齐相同任务与快照后记录收益和退化。"""

import argparse
import json
from pathlib import Path

from supportops.retrieval.evaluation import summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("拒绝覆盖比较报告。")
    reports = {
        mode: json.loads((args.reports_dir / f"dev-{mode}.json").read_text(encoding="utf-8"))
        for mode in ("vector", "bm25", "rrf")
    }
    baseline = reports["vector"]
    ids = [a["task_id"] for a in baseline["attempts"]]
    for mode, report in reports.items():
        for key in ("dataset_sha256", "qrels_sha256", "index", "split", "candidate_limit"):
            if report[key] != baseline[key]:
                raise RuntimeError(f"比较前提不一致：{key}")
        if report["split"] != "dev" or report["mode"] != mode:
            raise RuntimeError("只比较本轮显式开发集三路报告。")
        if [a["task_id"] for a in report["attempts"]] != ids:
            raise RuntimeError("任务分母不一致。")
        for before, after in zip(baseline["attempts"], report["attempts"], strict=True):

            def scope(attempt):
                return {k: v for k, v in attempt["request"].items() if k != "mode"}

            if scope(before) != scope(after):
                raise RuntimeError("查询或来源范围不一致。")
    cases = []
    for n, task_id in enumerate(ids):
        rows = {mode: report["attempts"][n] for mode, report in reports.items()}
        cases.append(
            {
                "task_id": task_id,
                "query": rows["vector"]["request"]["query"],
                "status": {mode: r["status"] for mode, r in rows.items()},
                "metrics_at_5": {mode: r.get("metrics") for mode, r in rows.items()},
                "ranked_ids": {
                    mode: [h["evidence_id"] for h in r.get("result", {}).get("items", [])]
                    for mode, r in rows.items()
                },
                "rrf_minus_vector": {
                    metric: rows["rrf"]["metrics"][metric] - rows["vector"]["metrics"][metric]
                    for metric in ("recall", "mrr", "ndcg")
                }
                if all(r["status"] == "completed" for r in rows.values())
                else None,
            }
        )
    report = {
        "schema_version": "supportops.retrieval-comparison.v1",
        "split": "dev",
        "human_reviewed": False,
        "dataset_sha256": baseline["dataset_sha256"],
        "qrels_sha256": baseline["qrels_sha256"],
        "corpus_sha256": baseline["index"]["corpus_sha256"],
        "candidate_limit": baseline["candidate_limit"],
        "summaries": {mode: r["summary"] for mode, r in reports.items()},
        "by_source": {
            mode: {
                kind: summarize(
                    [a for a in r["attempts"] if a["request"]["source_kinds"] == [kind]]
                )
                for kind in ("document", "log")
            }
            for mode, r in reports.items()
        },
        "rrf_vs_vector_at_5": {
            metric: {
                label: sum(
                    c["rrf_minus_vector"] is not None and predicate(c["rrf_minus_vector"][metric])
                    for c in cases
                )
                for label, predicate in (
                    ("improved", lambda x: x > 1e-12),
                    ("unchanged", lambda x: abs(x) <= 1e-12),
                    ("regressed", lambda x: x < -1e-12),
                )
            }
            for metric in ("recall", "mrr", "ndcg")
        },
        "bm25_empty_tasks": [
            c["task_id"]
            for c in cases
            if c["status"]["bm25"] == "completed" and not c["ranked_ids"]["bm25"]
        ],
        "cases": cases,
        "limitations": [
            "单次顺序实跑，时延不是 SLA",
            "初稿标签未人工复核",
            "12 个 holdout 未运行",
            "费用未与账单核对",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rrf_vs_vector_at_5": report["rrf_vs_vector_at_5"],
                "bm25_empty_count": len(report["bm25_empty_tasks"]),
                "by_source": report["by_source"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
