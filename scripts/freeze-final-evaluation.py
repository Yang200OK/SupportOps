"""零模型冻结既有保留任务和新调查题，保存可验证的文件与配置身份。"""

import hashlib
import json
from datetime import datetime, timezone

import httpx

from supportops.chunks.chunking import digest
from supportops.evaluations.answers import Dataset, Labels
from supportops.evaluations.final import VARIANTS
from supportops.models.provider import ModelSettings
from supportops.rag.model import AnswerModelSettings
from supportops.retrieval.dataset import Qrels, RetrievalDataset
from supportops.settings import ROOT

DEST = ROOT / "data/evaluations/final-v1/manifest.json"
INPUTS = (
    "data/retrieval/baseline-v1/dataset.json",
    "data/retrieval/baseline-v1/qrels.json",
    "data/retrieval/baseline-v1/snapshot.json",
    "data/evaluations/answers-v2/dataset.json",
    "data/evaluations/answers-v2/labels.json",
)
QUESTIONS = {
    "configuration": (
        "启动诊断报告 RD_CONFIG_INVALID，服务没有上线。"
        "请核对本次启动证据与适用的文档约束，列出支持、反驳和缺少的信息。"
    ),
    "pool": (
        "投递请求出现 RD_POOL_WAIT。请比较本次运行状态和请求观测，说明还需要什么证据才可确认原因。"
    ),
    "cache": (
        "目标变更后一次投递报告 RD_CACHE_STALE。请依据本次状态和观测核查版本与目标信息，"
        "保留不能确认的假设。"
    ),
    "downstream": (
        "请求报告 RD_TIMEOUT，无法判断等待发生在哪里。请按本次请求范围核对运行状态与下游观测，"
        "给出有原文依据的调查结论。"
    ),
}


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if DEST.exists():
        raise ValueError("不覆盖最终冻结；后续重复沿用同一冻结。")
    retrieval = RetrievalDataset.model_validate(load(INPUTS[0]))
    qrels = Qrels.model_validate(load(INPUTS[1]))
    answers = Dataset.model_validate(load(INPUTS[3]))
    labels = Labels.model_validate(load(INPUTS[4]))
    labels.bind(answers)
    snapshot = load(INPUTS[2])
    if qrels.dataset_sha256 != digest(retrieval.model_dump(mode="json")):
        raise ValueError("检索初稿标签不绑定原任务。")
    if not (retrieval.corpus_sha256 == answers.corpus_sha256 == snapshot["index"]["corpus_sha256"]):
        raise ValueError("保留任务来源不一致。")
    tasks = {
        "retrieval": [t.model_dump(mode="json") for t in retrieval.tasks if t.split == "holdout"],
        "answers": [t.model_dump(mode="json") for t in answers.tasks if t.split == "holdout"],
        "agents": [],
    }
    ids = {t["task_id"] for group in tasks.values() for t in group}
    hits = []

    def visit(value, path):
        if isinstance(value, dict):
            if value.get("task_id") in ids and "status" in value:
                hits.append({"path": path, "task_id": value["task_id"], "status": value["status"]})
            for child in value.values():
                visit(child, path)
        elif isinstance(value, list):
            for child in value:
                visit(child, path)

    for path in (ROOT / "docs/verification").rglob("*.json"):
        # 历史 PowerShell 生成的 JSON 允许 UTF-8 BOM，正文仍严格按 UTF-8 解码。
        visit(json.loads(path.read_text(encoding="utf-8-sig")), path.relative_to(ROOT).as_posix())
    if any(hit["status"] != "not_run" for hit in hits):
        raise ValueError("发现原 holdout 已尝试的报告，不能声称未运行。")
    account = next(
        a for a in load("local/demo-accounts.json")["accounts"] if a["username"] == "support_a"
    )
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def call(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        try:
            index_path = f"/api/retrieval/indexes/{snapshot['index']['index_id']}"
            if call("GET", index_path) != snapshot["index"]:
                raise ValueError("实际索引与冻结不同。")
            entries = []
            for offset in range(0, 502, 100):
                entries.extend(
                    call("GET", index_path + f"/entries?offset={offset}&limit=100")["items"]
                )
            if entries != snapshot["entries"]:
                raise ValueError("实际 502 来源与冻结不同。")
            for family, description in QUESTIONS.items():
                ticket = call(
                    "POST",
                    "/api/tickets",
                    {
                        "title": "最终冻结调查任务",
                        "description": "RelayDesk 1.1：" + description,
                        "product": "relaydesk",
                        "product_version": "1.1",
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                mode = "startup" if family == "configuration" else "online"
                memory = call(
                    "GET", f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode={mode}"
                )
                if not memory["loaded"]:
                    raise ValueError("记忆对照必须已有合格方法，不能事后创建或替换。")
                tasks["agents"].append(
                    {
                        "task_id": "prospective-" + family,
                        "family": family,
                        "ticket": ticket,
                        "memory": memory,
                        "mode": mode,
                    }
                )
        finally:
            client.post("/api/auth/logout").raise_for_status()
    files = [ROOT / p for p in INPUTS]
    for directory, suffix in (("src", ".py"), ("skills", ".md")):
        files.extend((ROOT / directory).rglob("*" + suffix))
    files.extend(
        ROOT / p
        for p in (
            "scripts/freeze-final-evaluation.py",
            "scripts/run-final-evaluation.py",
            "scripts/report-final-evaluation.py",
            "scripts/prepare-investigation-lab.py",
            "scripts/smoke-rag.py",
            "pyproject.toml",
            "requirements.lock",
        )
    )
    model = ModelSettings()
    value = {
        "schema_version": "supportops.final-freeze.v1",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "repeats": 2,
        "tasks": tasks,
        "variants": VARIANTS,
        "labels": {
            "retrieval": [
                item.model_dump(mode="json") for item in qrels.labels if item.task_id in ids
            ],
            "answers": [
                item.model_dump(mode="json") for item in labels.labels if item.task_id in ids
            ],
        },
        "files": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(set(files))},
        "models": model.model_dump(mode="json", exclude={"api_key"}),
        "answer_timeout_seconds": AnswerModelSettings().timeout_seconds,
        "index": snapshot["index"],
        "entries_sha256": digest(snapshot["entries"]),
        "agent_budget": {
            "tool_calls": 6,
            "model_calls": 8,
            "time_budget_ms": 240000,
            "context_chars": 16000,
        },
        "prior_task_status_audit": hits,
        "model_calls": 0,
        "boundaries": [
            "原任务身份未运行不代表家族 / 知识未见，开发期间已使用全部知识语料。",
            "检索缓存家族、已知包内日志定位；回答四题均为事实题。",
            "调查是熟悉四类场景的新措辞，同一固定故障设置，非家族保留集。",
            "两次重复非独立家族样本；初稿规则与模型评审非人工金标准。",
        ],
    }
    value["sha256"] = digest(value)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "sha256": value["sha256"],
                "tasks": {k: len(v) for k, v in tasks.items()},
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
