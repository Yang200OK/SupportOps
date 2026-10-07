"""真实 HTTP 回答与原文读回；逐次落盘，不自动重试或读取标签。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.retrieval.contexts import text_hash
from supportops.settings import ROOT


def audit(client, auth, body):
    hits = {h["evidence_id"]: h for h in body["retrieval"]["items"]}
    windows = {c["context_id"]: c for c in body["contexts"]}
    refs = {}
    for claim in body["claims"]:
        assert claim["support"]["verdict"] in ("supported", "unsupported", "insufficient")
        assert not claim["support"]["human_reviewed"]
        for citation in claim["citations"]:
            hit = hits[citation["evidence_id"]]
            window = windows[citation["context_id"]]
            assert hit["evidence_id"] in window["anchor_evidence_ids"]
            assert window["text"][citation["start"] : citation["end"]] == citation["quote"]
            assert text_hash(window["text"]) == citation["context_text_sha256"]
            response = client.get(hit["reference_url"], headers=auth)
            response.raise_for_status()
            detail = response.json()
            assert detail["text_verified"]
            source = hit["source"]
            if hit["kind"] == "log":
                assert detail["sha256"] == source["sha256"]
                assert detail["product_version"] == body["product_version"]
                assert detail["evidence_ids"][source["ordinal"]] == hit["evidence_id"]
                assert detail["artifact"]["observations"][source["ordinal"]] == source["event"]
            else:
                assert detail["chunk"]["text"] == hit["text"]
                assert detail["source"]["content_sha256"] == source["content_sha256"]
                assert detail["source"]["product_version"] == body["product_version"]
                if window["kind"] == "parent":
                    parent = detail["parent"]
                    assert (
                        window["text"]
                        == parent["text"][
                            window["start"] - parent["start"] : window["end"] - parent["start"]
                        ]
                    )
            refs[hit["evidence_id"]] = digest(detail)
    assert not body["persisted"] and not body["current_incident_verified"]
    return refs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.suffix != ".json":
        raise ValueError("报告必须写入项目内 JSON。")
    if output.exists() and not args.readback:
        raise ValueError("拒绝覆盖原运行报告，请使用新的输出路径。")
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {
            "transport": "real_http_real_bailian",
            "records": [],
            "boundary_checks": [],
            "human_reviewed": False,
            "cost_cny": None,
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=190, trust_env=False) as client:
        identities = {}
        for name in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            response = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            response.raise_for_status()
            identities[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        auth = identities["support_a"]
        path = f"/api/retrieval/indexes/{frozen['index']['index_id']}/answer"
        try:
            if args.readback:
                index_path = path.removesuffix("/answer")
                actual_index = client.get(index_path, headers=auth).json()
                assert actual_index["corpus_sha256"] == frozen["index"]["corpus_sha256"]
                entries = []
                for offset in range(0, actual_index["entry_count"], 100):
                    response = client.get(
                        index_path + f"/entries?offset={offset}&limit=100", headers=auth
                    )
                    response.raise_for_status()
                    entries.extend(response.json()["items"])
                assert digest(entries) == digest(frozen["entries"])
                refs = []
                for record in report["records"]:
                    if record["status"] == 200:
                        actual = audit(client, auth, record["response"])
                        assert actual == record["reference_digests"]
                        refs.append({"case": record["case"], "reference_count": len(actual)})
                report["restart_readback"] = {
                    "status": "passed",
                    "records": refs,
                    "frozen_entries_unchanged": len(entries),
                    "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                save()
                print(json.dumps(report["restart_readback"], ensure_ascii=False))
                return
            base = {
                "query": (
                    "RelayDesk 1.1 启动报 RD_CONFIG_INVALID，应核对哪些配置？"
                    "没有当前配置和日志，请勿确认根因。"
                ),
                "product_version": "1.1",
                "mode": "vector",
                "top_k": 5,
            }
            config = next(
                e
                for e in frozen["entries"]
                if e["product_version"] == "1.1"
                and e["kind"] == "document"
                and e["payload"]["source"]["source_key"] == "configuration"
            )
            log = next(
                e
                for e in frozen["entries"]
                if e["kind"] == "log"
                and e["product_version"] == "1.1"
                and "RD_CONFIG_INVALID" in e["payload"]["text"]
            )
            cases = [
                (
                    "document_config",
                    {
                        **base,
                        "source_kinds": ["document"],
                        "document_ids": [config["payload"]["source"]["document_id"]],
                        "expand_parent": True,
                    },
                ),
                (
                    "actual_lab_log",
                    {
                        **base,
                        "query": (
                            "请描述所选构造实验中 RD_CONFIG_INVALID 的实际观测及其范围，"
                            "不推断当前客户根因。"
                        ),
                        "source_kinds": ["log"],
                        "experiment_ids": [log["payload"]["source"]["experiment_id"]],
                    },
                ),
                (
                    "reported_case",
                    {
                        **base,
                        "query": "历史案例能否证明这次投递超时的原因？没有当前实例日志。",
                        "source_kinds": ["case"],
                    },
                ),
                (
                    "empty_scope",
                    {
                        **base,
                        "source_kinds": ["document"],
                        "document_ids": ["00000000-0000-0000-0000-000000000000"],
                    },
                ),
            ]
            for name, payload in cases:
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
                    assert body["corpus_sha256"] == frozen["index"]["corpus_sha256"]
                    record["reference_digests"] = audit(client, auth, body)
                    if name == "empty_scope":
                        assert (
                            body["status"] == "no_evidence"
                            and body["usage"]["known_model_calls"] == 0
                        )
                    save()
                print(
                    json.dumps(
                        {
                            "case": name,
                            "http_status": response.status_code,
                            "claims": len(body.get("claims", [])),
                            "usage": body.get("usage"),
                        },
                        ensure_ascii=False,
                    )
                )
            for name, identity, payload, expected in (
                ("cross_org", identities["support_b"], base, 404),
                ("no_auth", {}, base, 401),
                ("client_evidence", auth, {**base, "evidence": []}, 422),
                ("unknown_version", auth, {**base, "product_version": None}, 422),
            ):
                response = client.post(path, headers=identity, json=payload)
                report["boundary_checks"].append(
                    {"case": name, "status": response.status_code, "expected": expected}
                )
                save()
                assert response.status_code == expected
            report["all_cases_passed"] = all(r["status"] == 200 for r in report["records"])
            save()
            if not report["all_cases_passed"]:
                raise SystemExit(1)
        finally:
            for identity in identities.values():
                client.post("/api/auth/logout", headers=identity)


if __name__ == "__main__":
    main()
