"""真实 HTTP 核对固定向量索引、来源范围与进程重启读回。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    frozen = json.loads(args.snapshot.read_text(encoding="utf-8"))
    index_id = frozen["index"]["index_id"]
    report = {"transport": "real_http_real_embedding", "checks": [], "records": []}
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=70, trust_env=False) as client:
        identities = {}
        for name in ("support_a", "support_a2", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            r = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            r.raise_for_status()
            identities[name] = {"Authorization": "Bearer " + r.json()["access_token"]}
        a = identities["support_a"]

        def request(method, path, status=200, auth=a, **kwargs):
            r = client.request(method, path, headers=auth, **kwargs)
            assert r.status_code == status, f"HTTP {path}: {r.status_code}"
            return r.json()

        try:
            path = f"/api/retrieval/indexes/{index_id}"
            index = request("GET", path)
            assert index == {**frozen["index"], "reused": False}
            entries = []
            for offset in range(0, index["entry_count"], 100):
                entries.extend(
                    request("GET", path + f"/entries?offset={offset}&limit=100")["items"]
                )
            assert digest(entries) == digest(frozen["entries"])
            if args.readback:
                report = json.loads(args.output.read_text(encoding="utf-8"))
                restart_attempts = []
                for row in report["records"]:
                    actual = request("POST", path + "/search", json=row["request"])
                    assert [h["evidence_id"] for h in actual["items"]] == row["evidence_ids"]
                    assert (
                        digest(
                            [
                                {k: h[k] for k in ("evidence_id", "source", "text")}
                                for h in actual["items"]
                            ]
                        )
                        == row["source_sha256"]
                    )
                    restart_attempts.append(
                        {
                            "request": row["request"],
                            "usage": actual["usage"],
                            "latency_ms": actual["latency_ms"],
                            "evidence_ids": [h["evidence_id"] for h in actual["items"]],
                        }
                    )
                report["restart_readback"] = {
                    "status": "passed",
                    "index_and_entries_identical": True,
                    "query_rankings_identical": True,
                    "queries": len(report["records"]),
                    "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "attempts": restart_attempts,
                }
            else:
                assert request("GET", path, auth=identities["support_a2"]) == index
                request("GET", path, status=404, auth=identities["support_b"])
                request("GET", path + "/entries", status=404, auth=identities["support_b"])
                request("GET", path, status=401, auth={})
                reused = request("POST", "/api/retrieval/indexes", json=index["sources"])
                assert reused["reused"] and reused["index_id"] == index_id
                report["checks"].extend(
                    [
                        "index_and_all_entries_match_frozen_snapshot",
                        "same_org_shared",
                        "cross_org_404",
                        "no_auth_401",
                        "same_sources_reused",
                    ]
                )
                queries = [
                    {
                        "query": "RD_POOL_WAIT 应核对哪些证据",
                        "product_version": version,
                        "source_kinds": ["document"],
                        "top_k": 5,
                    }
                    for version in ("1.0", "1.1", "2.0")
                ]
                queries.append(
                    {
                        "query": "投递超时的用户报告",
                        "product_version": "1.1",
                        "source_kinds": ["case"],
                        "top_k": 5,
                    }
                )
                log = next(
                    e for e in entries if e["kind"] == "log" and e["product_version"] == "1.1"
                )
                queries.append(
                    {
                        "query": "启动或投递失败的终态",
                        "product_version": "1.1",
                        "source_kinds": ["log"],
                        "experiment_ids": [log["payload"]["source"]["experiment_id"]],
                        "top_k": 5,
                    }
                )
                for query in queries:
                    actual = request("POST", path + "/search", json=query)
                    assert actual["items"] and all(
                        h["product_version"] == query["product_version"]
                        and h["kind"] in query["source_kinds"]
                        for h in actual["items"]
                    )
                    for hit in actual["items"]:
                        source = request("GET", hit["reference_url"])
                        assert source["text_verified"] and not hit["support_verified"]
                    report["records"].append(
                        {
                            "request": query,
                            "evidence_ids": [h["evidence_id"] for h in actual["items"]],
                            "source_sha256": digest(
                                [
                                    {k: h[k] for k in ("evidence_id", "source", "text")}
                                    for h in actual["items"]
                                ]
                            ),
                            "usage": actual["usage"],
                            "latency_ms": actual["latency_ms"],
                        }
                    )
                request(
                    "POST",
                    path + "/search",
                    status=422,
                    json={**queries[0], "product_version": None},
                )
                request("POST", path + "/search", status=422, json={**queries[0], "expected": {}})
                empty = request(
                    "POST",
                    path + "/search",
                    json={
                        **queries[0],
                        "source_kinds": ["case"],
                        "document_ids": ["00000000-0000-0000-0000-000000000001"],
                    },
                )
                assert empty["items"] == [] and not empty["usage"]["model_called"]
                report["checks"].extend(
                    [
                        "three_versions_and_three_source_kinds",
                        "fixed_source_readback",
                        "version_required_422",
                        "label_injection_rejected_422",
                        "empty_scope_no_model_call",
                    ]
                )
                report["executed_at_utc"] = datetime.now(timezone.utc).isoformat()
                report["index_id"] = index_id
                report["entry_count"] = index["entry_count"]
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                f"真实检索 HTTP 通过：{len(report['checks'])} 场景，{len(report['records'])} 查询。"
            )
        finally:
            for auth in identities.values():
                client.post("/api/auth/logout", headers=auth)


if __name__ == "__main__":
    main()
