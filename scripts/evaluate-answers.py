"""显式执行开发集，逐项保存原响应；标签只在回答结束后评分。"""

import argparse
import json
import runpy
from pathlib import Path
from time import perf_counter

import httpx

from supportops.chunks.chunking import digest
from supportops.evaluations.answers import Dataset, Labels, Report, score
from supportops.models.provider import ModelSettings
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--dataset-directory", type=Path, default=ROOT / "data/evaluations/answers-v1"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    raw_path = output.with_name(output.stem + "-responses.json")
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists()
        or raw_path.exists()
    ):
        raise ValueError("报告必须使用项目内新的 JSON 路径，不覆盖尝试。")
    root = args.dataset_directory.resolve()
    if not root.is_relative_to(ROOT / "data/evaluations"):
        raise ValueError("本轮数据集必须来自项目评测目录。")
    dataset = Dataset.model_validate_json((root / "dataset.json").read_text(encoding="utf-8"))
    labels = Labels.model_validate_json((root / "labels.json").read_text(encoding="utf-8"))
    labels.bind(dataset)
    chosen = {t.task_id for t in dataset.tasks if t.split == "dev"}
    if args.case and not set(args.case) <= chosen:
        raise ValueError("本轮只允许显式选择开发任务，不能运行 holdout。")
    config = ModelSettings()
    report = {
        "schema_version": "supportops.answer-report.v1",
        "dataset_sha256": digest(dataset.model_dump(mode="json")),
        "labels_sha256": digest(labels.model_dump(mode="json")),
        "corpus_sha256": dataset.corpus_sha256,
        "index_id": str(dataset.index_id),
        "main_model": config.main_model,
        "light_model": config.light_model,
        "embedding_model": config.embedding_model,
        "parameters": "temperature=0;max_tokens=3500/2000;no_retry",
        "transport": "real_http_real_bailian",
        "human_reviewed": False,
        "task_ids": [t.task_id for t in dataset.tasks],
        "attempts": [
            {
                "task_id": t.task_id,
                "split": t.split,
                "category": t.category,
                "status": "not_run",
                "latency_ms": None,
                "error_code": None,
                "metrics": None,
                "usage": None,
                "reference_readback": None,
            }
            for t in dataset.tasks
        ],
    }
    raw = {
        "transport": "real_http_real_bailian",
        "records": [],
        "human_reviewed": False,
        "cost_cny": None,
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        Report.model_validate(report)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    audit = runpy.run_path(str(ROOT / "scripts/smoke-rag.py"))["audit"]
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    account = next(a for a in accounts if a["username"] == "support_a")
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=190, trust_env=False) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        response.raise_for_status()
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}
        path = f"/api/retrieval/indexes/{dataset.index_id}"
        actual = client.get(path, headers=auth)
        actual.raise_for_status()
        if actual.json()["corpus_sha256"] != dataset.corpus_sha256:
            raise ValueError("当前知识快照与数据集不同。")
        save()
        for task, row in zip(dataset.tasks, report["attempts"], strict=True):
            if task.split != "dev" or (args.case and task.task_id not in args.case):
                continue
            started = perf_counter()
            body = None
            record = {"case": task.task_id, "request": task.request.model_dump(mode="json")}
            try:
                response = client.post(path + "/answer", headers=auth, json=record["request"])
                body = response.json()
                record.update(status=response.status_code, response=body)
                usage = body.get("usage", {})
                row.update(
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    usage={
                        k: usage[k]
                        for k in (
                            "input_tokens",
                            "output_tokens",
                            "known_model_calls",
                            "unknown_usage_calls",
                            "cost_cny",
                        )
                    },
                )
                if response.status_code != 200:
                    row.update(status="failed", error_code=body["error"]["code"])
                else:
                    label = next(x for x in labels.labels if x.task_id == task.task_id)
                    row["metrics"] = score(body, record["request"], label.model_dump(mode="json"))
                    record["reference_digests"] = audit(client, auth, body)
                    row.update(status="completed", reference_readback=True)
            except (httpx.HTTPError, ValueError, KeyError, AssertionError) as exc:
                row.update(
                    status="failed",
                    error_code="EVALUATION_TRANSPORT_OR_READBACK_FAILED",
                    metrics=None,
                    reference_readback=False,
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                )
                if row["usage"] is None:
                    # 未收到服务端计量，不能猜测内部实际调用次数；另记未知管道尝试。
                    row["usage"] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "known_model_calls": 0,
                        "unknown_usage_calls": 0,
                        "cost_cny": None,
                    }
                    record["pipeline_usage_unknown"] = True
                    row["pipeline_usage_unknown"] = True
                record["failure_type"] = type(exc).__name__
            raw["records"].append(record)
            save()
            print(
                json.dumps(
                    {"task": task.task_id, "status": row["status"], "error": row["error_code"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    print(json.dumps(Report.model_validate(report).summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
