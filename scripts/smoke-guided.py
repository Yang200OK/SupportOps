"""记录真实分步 HTTP；每次立即落盘，失败不重试，不读取评测标签。"""

import argparse
import json
import runpy
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    parser.add_argument(
        "--case",
        choices=[
            "unknown_version",
            "vague_problem",
            "empty_scope",
            "document_answer",
            "supplement",
            "supplement_rules",
            "narrow_additional",
            "model_budget",
        ],
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.suffix != ".json":
        raise ValueError("报告必须写入项目内 JSON。")
    if output.exists() and not args.readback:
        raise ValueError("拒绝覆盖原尝试。")
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    audit = runpy.run_path(str(ROOT / "scripts/smoke-rag.py"))["audit"]
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {
            "transport": "real_http_real_bailian",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "records": [],
            "boundary_checks": [],
            "human_reviewed": False,
            "cost_cny": None,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=250, trust_env=False) as client:
        identities = {}
        for name in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            response = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            response.raise_for_status()
            identities[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        auth = identities["support_a"]
        index_path = f"/api/retrieval/indexes/{frozen['index']['index_id']}"
        path = index_path + "/guided-answer"
        try:
            if args.readback:
                entries = []
                for offset in range(0, 502, 100):
                    response = client.get(
                        index_path + f"/entries?offset={offset}&limit=100", headers=auth
                    )
                    response.raise_for_status()
                    entries.extend(response.json()["items"])
                assert digest(entries) == digest(frozen["entries"])
                anchors = 0
                for record in report["records"]:
                    answer = record["response"].get("answer")
                    if answer:
                        actual = audit(client, auth, answer)
                        assert actual == record["reference_digests"]
                        anchors += len(actual)
                report["restart_readback"] = {
                    "status": "passed",
                    "frozen_entries_unchanged": len(entries),
                    "citation_anchors": anchors,
                }
                save()
                print(json.dumps(report["restart_readback"], ensure_ascii=False))
                return
            doc = next(
                e
                for e in frozen["entries"]
                if e["kind"] == "document"
                and e["product_version"] == "1.1"
                and e["payload"]["source"]["source_key"] == "configuration"
            )
            base = {
                "query": (
                    "RelayDesk 1.1 RD_CONFIG_INVALID 应核对哪些配置？"
                    "仅解释文档，不确认当前客户根因。"
                ),
                "product_version": "1.1",
                "mode": "vector",
                "source_kinds": ["document"],
                "document_ids": [doc["payload"]["source"]["document_id"]],
                "top_k": 5,
            }
            cases = [
                ("unknown_version", {**base, "product_version": None}),
                ("vague_problem", {**base, "query": "有问题，帮看看"}),
                ("empty_scope", {**base, "document_ids": ["00000000-0000-0000-0000-000000000000"]}),
                ("document_answer", {**base, "expand_parent": True}),
                (
                    "supplement",
                    {
                        **base,
                        "query": "启动失败应该看哪些配置？",
                        "clarification": (
                            "产品为 RelayDesk 1.1，错误为 RD_CONFIG_INVALID；仅问文档规则。"
                        ),
                        "expand_parent": True,
                    },
                ),
                (
                    "narrow_additional",
                    {
                        **base,
                        "query": (
                            "RelayDesk 1.1 的 timeout_ms 与 db_pool_size 配置有哪些要求？"
                            "仅问文档规则。"
                        ),
                        "top_k": 1,
                    },
                ),
                (
                    "supplement_rules",
                    {
                        **base,
                        "query": "启动时哪些情况应被拒绝？",
                        "clarification": (
                            "产品为 RelayDesk 1.1，错误为 RD_CONFIG_INVALID；"
                            "只查询文档的拒绝启动规则。"
                        ),
                        "top_k": 1,
                        "expand_parent": True,
                    },
                ),
                ("model_budget", {**base, "mode": "bm25", "max_model_calls": 3}),
            ]
            for name, payload in cases:
                if args.case and name != args.case:
                    continue
                response = client.post(path, headers=auth, json=payload)
                body = response.json()
                record = {
                    "case": name,
                    "request": payload,
                    "status": response.status_code,
                    "response": body,
                }
                report["records"].append(record)
                save()
                if response.status_code == 200:
                    assert body["original_query"] == payload["query"] and not body["persisted"]
                    if body.get("answer"):
                        record["reference_digests"] = audit(client, auth, body["answer"])
                        assert body["answer"]["query"] == payload["query"]
                    assert body["usage"]["known_model_calls"] <= payload.get("max_model_calls", 9)
                    assert len(body["retrieval"]["items"]) <= 10 if body["retrieval"] else True
                    save()
                print(
                    json.dumps(
                        {
                            "case": name,
                            "http": response.status_code,
                            "status": body.get("status"),
                            "stop": body.get("stop_reason"),
                            "usage": {
                                k: v for k, v in body.get("usage", {}).items() if k != "stages"
                            },
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            for name, identity, payload, expected in (
                ("cross_org", identities["support_b"], base, 404),
                ("no_auth", {}, base, 401),
                ("client_scope", auth, {**base, "organization_id": "other"}, 422),
                ("client_context", auth, {**base, "contexts": []}, 422),
            ):
                response = client.post(path, headers=identity, json=payload)
                report["boundary_checks"].append(
                    {"case": name, "status": response.status_code, "expected": expected}
                )
                save()
                assert response.status_code == expected
            report["all_http_success"] = all(r["status"] == 200 for r in report["records"])
            save()
            if not report["all_http_success"]:
                raise SystemExit(1)
        finally:
            for identity in identities.values():
                client.post("/api/auth/logout", headers=identity)


if __name__ == "__main__":
    main()
