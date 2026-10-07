"""保留真实四组结果、逐题退化和首次失败，覆盖与排名分开。"""

import argparse
import json
from pathlib import Path

from supportops.retrieval.evaluation import summarize
from supportops.settings import ROOT

OUTPUT = ROOT / "docs/verification/phase-3-round-3"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ids(attempt):
    return [h["evidence_id"] for h in attempt.get("result", {}).get("items", [])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-report", type=Path, required=True)
    parser.add_argument("--additional-failed-report", type=Path, action="append", default=[])
    args = parser.parse_args()
    target = OUTPUT / "ablation-comparison.json"
    if target.exists():
        raise RuntimeError("拒绝覆盖消融比较。")
    reports = {
        "rrf": read(OUTPUT / "dev-rrf.json"),
        "rrf-parent": read(OUTPUT / "dev-rrf-parent.json"),
        "rrf-rerank": read(OUTPUT / "attempt-3/dev-rrf-rerank.json"),
        "rrf-rerank-parent": read(args.parent_report),
    }
    base = reports["rrf"]
    previous = read(ROOT / "docs/verification/phase-3-round-2/dev-rrf.json")
    for report in [previous, *reports.values()]:
        for key in ("dataset_sha256", "qrels_sha256", "index", "split", "candidate_limit"):
            assert base[key] == report[key], key
        assert [r["task_id"] for r in base["attempts"]] == [
            r["task_id"] for r in report["attempts"]
        ]
        assert report["summary"]["completed"] == 36 and not report["human_reviewed"]
        for before, after in zip(base["attempts"], report["attempts"], strict=True):

            def scope(r):
                return {
                    k: v for k, v in r["request"].items() if k not in {"rerank", "expand_parent"}
                }

            assert scope(before) == scope(after)
    assert all(
        ids(a) == ids(b) for a, b in zip(base["attempts"], previous["attempts"], strict=True)
    )
    cases = []
    for n, original in enumerate(base["attempts"]):
        rows = {name: r["attempts"][n] for name, r in reports.items()}
        cases.append(
            {
                "task_id": original["task_id"],
                "query": original["request"]["query"],
                "ranked_ids": {name: ids(row) for name, row in rows.items()},
                "metrics_at_5": {name: row["metrics"] for name, row in rows.items()},
                "context_recall": {name: row["context_recall"] for name, row in rows.items()},
                "context_chars": {name: row["context_chars"] for name, row in rows.items()},
                "rerank_minus_rrf": {
                    k: rows["rrf-rerank"]["metrics"][k] - original["metrics"][k]
                    for k in ("recall", "mrr", "ndcg")
                },
                "parent_rank_changed": ids(rows["rrf-parent"]) != ids(original),
                "rerank_parent_rank_changed": ids(rows["rrf-rerank-parent"])
                != ids(rows["rrf-rerank"]),
                "parent_coverage_delta": rows["rrf-parent"]["context_recall"]
                - original["context_recall"],
            }
        )
    prior_failures = [
        read(OUTPUT / "dev-rrf-rerank.json"),
        read(OUTPUT / "attempt-2/dev-rrf-rerank.json"),
        read(OUTPUT / "attempt-3/dev-rrf-rerank-parent.json"),
        read(OUTPUT / "attempt-5/dev-rrf-rerank-parent.json"),
        read(OUTPUT / "attempt-6/dev-rrf-rerank-parent.json"),
    ]
    prior_failures.extend(read(path) for path in args.additional_failed_report)
    actual_rows = [
        row
        for run in [*reports.values(), *prior_failures]
        for row in run["attempts"]
        if row["status"] != "not_run"
        and ("newly_executed_tasks" not in run or row["task_id"] in run["newly_executed_tasks"])
    ]
    report = {
        "dataset_sha256": base["dataset_sha256"],
        "qrels_sha256": base["qrels_sha256"],
        "human_reviewed": False,
        "split": "dev",
        "holdout_executed": False,
        "previous_rrf_rankings_equal": 36,
        "summaries": {name: r["summary"] for name, r in reports.items()},
        "by_source": {
            name: {
                kind: summarize(
                    [a for a in r["attempts"] if a["request"]["source_kinds"] == [kind]]
                )
                for kind in ("document", "log")
            }
            for name, r in reports.items()
        },
        "rerank_vs_rrf": {
            metric: {
                label: sum(predicate(c["rerank_minus_rrf"][metric]) for c in cases)
                for label, predicate in (
                    ("improved", lambda v: v > 1e-12),
                    ("regressed", lambda v: v < -1e-12),
                    ("equal", lambda v: abs(v) <= 1e-12),
                )
            }
            for metric in ("recall", "mrr", "ndcg")
        },
        "parent_changed_rankings": sum(c["parent_rank_changed"] for c in cases),
        "repeated_rerank_changed_rankings": sum(c["rerank_parent_rank_changed"] for c in cases),
        "cases": cases,
        "previous_failed_runs": [
            {
                "summary": r["summary"],
                "failures": [a for a in r["attempts"] if a["status"] == "failed"],
            }
            for r in prior_failures
        ],
        "cohort_semantics": (
            "parent+rerank assembled from explicit continuation; initial reports preserved"
        ),
        "known_input_tokens_including_failed_runs": sum(
            r["input_tokens"] or 0 for r in actual_rows
        ),
        "unknown_usage_calls": sum(
            (r.get("partial_usage") or {}).get("unknown_usage_calls", 1)
            for r in actual_rows
            if r["status"] == "failed"
        ),
        "actual_model_calls": sum(
            r["result"]["usage"].get("model_calls", int(r["result"]["usage"]["model_called"]))
            if r["status"] == "completed"
            else (r.get("partial_usage") or {}).get("known_model_calls", 0)
            + (r.get("partial_usage") or {}).get("unknown_usage_calls", 1)
            for r in actual_rows
        ),
        "cost_cny": None,
        "lost_response_incident": read(OUTPUT / "report-write-failure.json"),
    }
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("cases", "by_source", "previous_failed_runs")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
