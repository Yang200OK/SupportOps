"""显式准备固定检索快照和独立初稿标签，不把标签提交给 API。"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
from time import perf_counter

import httpx

from supportops.chunks.chunking import digest
from supportops.retrieval.dataset import Qrels, RetrievalDataset
from supportops.settings import ROOT


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="准备检索基线；会产生真实 embedding 调用")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("snapshot.json", "dataset.json", "qrels.json")):
        raise RuntimeError("拒绝覆盖已有冻结检索集，请指定新的目录。")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    account = next(a for a in accounts if a["username"] == "support_a")
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=600, trust_env=False) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def request(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if r.status_code != 200:
                code = r.json().get("error", {}).get("code", "HTTP_ERROR")
                raise RuntimeError(f"{path} 失败：{r.status_code} {code}")
            return r.json()

        try:
            corpus = json.loads((ROOT / "data/relaydesk/manifest.json").read_text(encoding="utf-8"))
            set_ids = []
            for source in corpus["sources"]:
                raw = (ROOT / "data/relaydesk" / source["path"]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != source["content_sha256"]:
                    raise RuntimeError("资料原文摘要不同。")
                imported = request(
                    "POST",
                    "/api/documents/import",
                    json={
                        **{
                            k: source[k]
                            for k in (
                                "source_key",
                                "title",
                                "product",
                                "product_version",
                                "source_type",
                                "license",
                                "filename",
                                "format",
                            )
                        },
                        "content_base64": base64.b64encode(raw).decode(),
                    },
                )
                snapshot = request(
                    "POST",
                    f"/api/documents/{imported['document_id']}/revisions/{imported['revision_id']}/chunk-sets",
                    json={"max_chars": 256, "overlap_chars": 32},
                )
                set_ids.append(snapshot["chunk_set_id"])
            # 建索引只读公开观测文件，根因与控制标签不参与 corpus。
            lab = json.loads((ROOT / "data/lab/manifest.json").read_text(encoding="utf-8"))
            experiments = []
            for source in lab["records"]:
                artifact = json.loads(
                    (ROOT / "data/lab" / source["path"]).read_text(encoding="utf-8")
                )
                imported = request(
                    "POST",
                    "/api/experiments/import",
                    json={"artifact": artifact, "sha256": source["sha256"]},
                )
                experiments.append(imported["experiment_id"])
            build_started = perf_counter()
            index = request(
                "POST",
                "/api/retrieval/indexes",
                json={"chunk_set_ids": set_ids, "experiment_ids": experiments},
            )
            build_http_latency_ms = round((perf_counter() - build_started) * 1000, 3)
            entries = []
            for offset in range(0, index["entry_count"], 100):
                entries.extend(
                    request(
                        "GET",
                        f"/api/retrieval/indexes/{index['index_id']}/entries?offset={offset}&limit=100",
                    )["items"]
                )
            save(
                output / "snapshot.json",
                {
                    "index": index,
                    "entries": entries,
                    "annotation_in_index": False,
                    "build_http_latency_ms": build_http_latency_ms,
                },
            )
            tasks, labels = [], []
            topics = {
                "configuration": (
                    "配置不匹配",
                    [
                        "启动报 RD_CONFIG_INVALID，应该核对哪些配置信息？",
                        "超时参数的键名或单位不匹配时如何排查？",
                    ],
                ),
                "pool": (
                    "连接池等待",
                    [
                        "连接获取一直等待，出现 RD_POOL_WAIT，需要哪些证据？",
                        "数据库连接池耗尽时应该核对占用、等待还是直接加连接？",
                    ],
                ),
                "cache": (
                    "缓存旧值",
                    [
                        "目标配置更新后仍读旧值，RD_CACHE_STALE 应核对什么？",
                        "缓存和数据库目标或代次不一致时如何调查？",
                    ],
                ),
                "downstream": (
                    "下游超时",
                    [
                        "投递返回 RD_TIMEOUT，下游链路应该如何取证？",
                        "怎样区分下游实际延迟和有效超时配置，能只凭错误码认定根因吗？",
                    ],
                ),
            }
            for version in ("1.0", "1.1", "2.0"):
                for family, (heading, questions) in topics.items():
                    relevant = {
                        e["evidence_id"]: 1 if e["payload"]["text"].strip().startswith("##") else 3
                        for e in entries
                        if e["kind"] == "document"
                        and e["product_version"] == version
                        and e["payload"]["source"].get("source_key") == "troubleshooting"
                        and heading in e["payload"]["source"]["heading_path"]
                    }
                    for variant, question in enumerate(questions):
                        identity = f"doc-{family}-{version}-{variant}"
                        tasks.append(
                            {
                                "task_id": identity,
                                "source_group": family,
                                "split": "holdout" if family == "cache" else "dev",
                                "request": {
                                    "query": question,
                                    "product_version": version,
                                    "source_kinds": ["document"],
                                    "top_k": 5,
                                },
                            }
                        )
                        labels.append(
                            {
                                "task_id": identity,
                                "relevance": relevant,
                                "annotation_origin": "section_rule_draft",
                                "human_reviewed": False,
                            }
                        )
            # 标签构造在评测侧独立进行：复用既有实验锚点，不读入 API 或 embedding 文本。
            lab_tasks = json.loads(
                (ROOT / "data/lab/evaluation-dataset.v1.json").read_text(encoding="utf-8")
            )["tasks"]
            for task in lab_tasks:
                task_id = "log-" + task["task_id"]
                version = task["input"]["product_version"]
                evidence_ids = task["expected"]["evidence_ids"]
                entry = next(e for e in entries if e["evidence_id"] == evidence_ids[0])
                tasks.append(
                    {
                        "task_id": task_id,
                        "source_group": task["source_group"],
                        "split": task["split"],
                        "request": {
                            "query": (
                                "查找本次启动或投递失败的原始终态，包括状态、错误码与实测耗时。"
                            ),
                            "product_version": version,
                            "source_kinds": ["log"],
                            "experiment_ids": [entry["payload"]["source"]["experiment_id"]],
                            "top_k": 5,
                        },
                    }
                )
                labels.append(
                    {
                        "task_id": task_id,
                        "relevance": {e: 3 for e in evidence_ids},
                        "annotation_origin": "controlled_experiment_draft",
                        "human_reviewed": False,
                    }
                )
            dataset = RetrievalDataset.model_validate(
                {
                    "schema_version": "supportops.retrieval-dataset.v1",
                    "index_id": index["index_id"],
                    "corpus_sha256": index["corpus_sha256"],
                    "tasks": tasks,
                }
            )
            dataset_data = dataset.model_dump(mode="json")
            qrels = Qrels.model_validate(
                {
                    "schema_version": "supportops.retrieval-qrels.v1",
                    "dataset_sha256": digest(dataset_data),
                    "labels": labels,
                }
            )
            save(output / "dataset.json", dataset_data)
            save(output / "qrels.json", qrels.model_dump(mode="json"))
            print(
                f"固定索引：{index['entry_count']} 条；检索初稿：{len(tasks)} 项。标签未送入模型。"
            )
        finally:
            client.post("/api/auth/logout")


if __name__ == "__main__":
    main()
