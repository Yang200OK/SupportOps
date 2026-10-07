"""在冻结快照上执行显式分区，保留每项原排名和失败分母。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx

from supportops.chunks.chunking import digest
from supportops.retrieval.contexts import covered_ids
from supportops.retrieval.dataset import Qrels, RetrievalDataset
from supportops.retrieval.evaluation import metrics, summarize
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser(description="真实检索评测；将调用 embedding 模型")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("vector", "bm25", "rrf"), default="vector")
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--expand-parent", action="store_true")
    parser.add_argument("--resume-from", type=Path)
    args = parser.parse_args()
    progress_path = args.output.with_suffix(".progress.jsonl")
    resume_label = (
        None if args.resume_from is None else str(args.resume_from.resolve().relative_to(ROOT))
    )
    if args.output.exists() or progress_path.exists():
        raise RuntimeError("拒绝覆盖已有评测结果。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    dataset = RetrievalDataset.model_validate_json(
        (args.dataset_dir / "dataset.json").read_text(encoding="utf-8")
    )
    qrels = Qrels.model_validate_json((args.dataset_dir / "qrels.json").read_text(encoding="utf-8"))
    dataset_sha = digest(dataset.model_dump(mode="json"))
    prior = None
    if args.resume_from is not None:
        prior = json.loads(args.resume_from.read_text(encoding="utf-8"))
        if (
            prior["dataset_sha256"] != dataset_sha
            or prior["split"] != args.split
            or prior["mode"] != args.mode
            or prior["candidate_limit"] != args.candidate_limit
            or prior.get("rerank", False) != args.rerank
            or prior.get("expand_parent", False) != args.expand_parent
            or prior["qrels_sha256"] != digest(qrels.model_dump(mode="json"))
        ):
            raise RuntimeError("显式续测必须沿用全部冻结前提和配置。")
    labels = {label.task_id: label.relevance for label in qrels.labels}
    if (
        qrels.dataset_sha256 != dataset_sha
        or len(labels) != len(qrels.labels)
        or set(labels) != {t.task_id for t in dataset.tasks}
    ):
        raise RuntimeError("标签摘要或完整任务分母不一致。")
    account = next(
        a
        for a in json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
            "accounts"
        ]
        if a["username"] == "support_a"
    )
    attempts = []
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=70, trust_env=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        try:
            index_response = client.get(f"/api/retrieval/indexes/{dataset.index_id}")
            index_response.raise_for_status()
            index = index_response.json()
            if prior is not None and prior["index"] != index:
                raise RuntimeError("显式续测的实际索引已经变化。")
            if index["corpus_sha256"] != dataset.corpus_sha256:
                raise RuntimeError("知识快照与检索集不同。")
            ids = set()
            entries = []
            for offset in range(0, index["entry_count"], 100):
                r = client.get(
                    f"/api/retrieval/indexes/{dataset.index_id}/entries?offset={offset}&limit=100"
                )
                r.raise_for_status()
                page = r.json()["items"]
                entries.extend(page)
                ids.update(e["evidence_id"] for e in page)
            if any(not set(relevance).issubset(ids) for relevance in labels.values()):
                raise RuntimeError("标签引用不属于冻结知识快照。")
            stopped = False
            prior_rows = {} if prior is None else {a["task_id"]: a for a in prior["attempts"]}
            for task in dataset.tasks:
                if task.split != args.split:
                    continue
                if task.task_id in prior_rows and prior_rows[task.task_id]["status"] == "completed":
                    attempts.append(prior_rows[task.task_id])
                    continue
                row = {
                    "task_id": task.task_id,
                    "status": "not_run",
                    "latency_ms": None,
                    "input_tokens": None,
                    "cost_cny": None,
                    "error_code": None,
                    "request": task.request.model_dump(mode="json"),
                }
                attempts.append(row)
                row["request"].update(mode=args.mode, candidate_limit=args.candidate_limit)
                if args.rerank or args.expand_parent:
                    row["request"].update(rerank=args.rerank, expand_parent=args.expand_parent)
                if stopped:
                    continue
                started = perf_counter()
                try:
                    response = client.post(
                        f"/api/retrieval/indexes/{dataset.index_id}/search", json=row["request"]
                    )
                    row["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                    if response.status_code != 200:
                        error_body = response.json()
                        row.update(
                            status="failed",
                            error_code=error_body.get("error", {}).get("code", "HTTP_ERROR"),
                            failed_stage=error_body.get("stage"),
                            partial_usage=error_body.get("usage"),
                            input_tokens=error_body.get("usage", {}).get("input_tokens"),
                        )
                        stopped = True
                        continue
                    result = response.json()
                    coverage = covered_ids(result, entries)
                    ranked = [hit["evidence_id"] for hit in result["items"]]
                    row.update(
                        status="completed",
                        result=result,
                        context_recall=len(coverage.intersection(labels[task.task_id]))
                        / len(labels[task.task_id]),
                        covered_evidence_ids=sorted(coverage),
                        context_chars=result.get(
                            "context_chars", sum(len(h["text"]) for h in result["items"])
                        ),
                        input_tokens=result["usage"]["input_tokens"],
                        metrics=metrics(ranked, labels[task.task_id], 5),
                        metrics_at={
                            str(k): metrics(ranked, labels[task.task_id], k) for k in (1, 3, 5)
                        },
                        versions_correct=all(
                            hit["product_version"] == task.request.product_version
                            for hit in result["items"]
                        ),
                    )
                except httpx.RequestError:
                    row.update(
                        status="failed",
                        error_code="HTTP_CONNECTION_FAILED",
                        latency_ms=round((perf_counter() - started) * 1000, 3),
                    )
                    stopped = True
                finally:
                    # 每次请求立即保存，最终汇总错误不能丢掉真实响应和收费信息。
                    with progress_path.open("a", encoding="utf-8") as progress:
                        progress.write(json.dumps(row, ensure_ascii=False) + "\n")
            if not attempts:
                raise RuntimeError("所选分区没有任务。")
            summary = summarize(attempts)
            summary["unknown_usage_calls"] = sum(
                (r.get("partial_usage") or {}).get("unknown_usage_calls", 1)
                for r in attempts
                if r["status"] == "failed"
            )
            summary["macro_context_recall_all"] = sum(
                r["context_recall"] for r in attempts if r["status"] == "completed"
            ) / len(attempts)
            summary["macro_all_at"] = {
                str(k): {
                    name: sum(
                        r["metrics_at"][str(k)][name]
                        for r in attempts
                        if r["status"] == "completed"
                    )
                    / len(attempts)
                    for name in ("recall", "mrr", "ndcg")
                }
                for k in (1, 3, 5)
            }
            report = {
                "schema_version": "supportops.retrieval-report.v1",
                "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                "transport": "real_http_bm25"
                if args.mode == "bm25"
                else "real_http_real_embedding",
                "mode": args.mode,
                "candidate_limit": args.candidate_limit,
                "rerank": args.rerank,
                "expand_parent": args.expand_parent,
                "split": args.split,
                "dataset_sha256": dataset_sha,
                "qrels_sha256": digest(qrels.model_dump(mode="json")),
                "human_reviewed": False,
                "index": index,
                "summary": summary,
                "attempts": attempts,
                "excluded_split_tasks": sum(t.split != args.split for t in dataset.tasks),
                "resume_from": resume_label,
                "attempt_semantics": "assembled_completed_cohort"
                if prior is not None
                else "single_run",
                "newly_executed_tasks": [
                    r["task_id"]
                    for r in attempts
                    if r["status"] != "not_run"
                    and (
                        r["task_id"] not in prior_rows
                        or prior_rows[r["task_id"]]["status"] != "completed"
                    )
                ],
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False))
        finally:
            client.post("/api/auth/logout")


if __name__ == "__main__":
    main()
