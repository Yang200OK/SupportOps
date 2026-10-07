"""真实 HTTP / MCP / 百炼基线，逐次保存失败，不自动续试。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.retrieval.contexts import text_hash
from supportops.settings import ROOT


def audit(client, auth, body):
    by_id = {e["evidence_id"]: e for e in body["evidence"]}
    contexts = {c["context_id"]: c for c in body["contexts"]}
    version = body["input_snapshot"]["ticket"]["product_version"]
    assert all(e["product_version"] == version and e["text_verified"] for e in by_id.values())
    assert not body["current_incident_verified"] and not body["human_reviewed"]
    assert digest(body["input_snapshot"]) == body["input_sha256"]
    for tool in body["tool_results"]:
        assert digest(tool["result"]) == tool["result_sha256"]
    refs = {}
    if body["report"] is not None:
        for claim in body["report"]["claims"]:
            assert not claim["support"]["human_reviewed"]
            for citation in claim["citations"]:
                e = by_id[citation["evidence_id"]]
                c = contexts[citation["context_id"]]
                assert c["text"][citation["start"] : citation["end"]] == citation["quote"]
                assert text_hash(c["text"]) == citation["context_text_sha256"]
                response = client.get(e["reference_url"], headers=auth)
                response.raise_for_status()
                actual = response.json()
                assert actual["text_verified"]
                if e["kind"] == "log":
                    source = e["source"]
                    assert actual["sha256"] == source["sha256"]
                    assert actual["artifact"]["observations"][source["ordinal"]] == source["event"]
                    assert source["event"]["phase"] == "failure"
                else:
                    assert actual["chunk"]["text"] == e["text"]
                    assert actual["source"]["product_version"] == version
                refs[e["evidence_id"]] = digest(actual)
    return refs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--readback", action="store_true")
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or (output.exists() and not args.readback)
    ):
        raise ValueError("必须使用项目内新的 JSON 路径。")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {
            "transport": "real_http_zero_model_local_validation"
            if args.local_only
            else "real_http_real_bailian_real_stdio_mcp",
            "records": [],
            "boundary_checks": [],
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "human_reviewed": False,
            "cost_cny": None,
        }
    )

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=200, trust_env=False) as client:
        identities = {}
        for name in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            response = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            response.raise_for_status()
            identities[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        auth = identities["support_a"]
        try:
            if args.readback:
                reads = []
                for record in report["records"]:
                    before = record["response"]
                    response = client.get(
                        f"/api/investigations/{before['investigation_id']}", headers=auth
                    )
                    response.raise_for_status()
                    actual = response.json()
                    assert actual == before
                    assert audit(client, auth, actual) == record["reference_digests"]
                    reads.append(before["investigation_id"])
                entries = []
                for offset in range(0, frozen["index"]["entry_count"], 100):
                    response = client.get(
                        f"/api/retrieval/indexes/{frozen['index']['index_id']}/entries?offset={offset}&limit=100",
                        headers=auth,
                    )
                    response.raise_for_status()
                    entries.extend(response.json()["items"])
                assert digest(entries) == digest(frozen["entries"])
                report["restart_readback"] = {
                    "records_unchanged": reads,
                    "frozen_entries_unchanged": len(entries),
                }
                save()
                print(json.dumps(report["restart_readback"]))
                return
            candidates = [
                e
                for e in frozen["entries"]
                if e["kind"] == "log"
                and e["product_version"] == "1.1"
                and e["payload"]["source"]["event"].get("error_code") == "RD_TIMEOUT"
                and e["payload"]["source"]["event"]["phase"] == "failure"
            ]
            exp = candidates[0]["payload"]["source"]["experiment_id"]
            cases = [
                (
                    "configuration_document",
                    "RD_CONFIG_INVALID 配置要求，只解释资料，没有当前现场日志。",
                    "1.1",
                    None,
                ),
                (
                    "timeout_historical",
                    "RD_TIMEOUT 请检索版本文档并读取绑定历史实验的异常观测，"
                    "整理该历史实验记录的事实和待检查项，不确认当前客户根因。",
                    "1.1",
                    exp,
                ),
                ("missing_version", "RD_TIMEOUT 版本未知。", None, None),
            ]
            for name, description, version, experiment in cases:
                if args.local_only and version is not None:
                    continue
                response = client.post(
                    "/api/tickets",
                    headers=auth,
                    json={
                        "title": "只读调查构造案例 " + name,
                        "description": description,
                        "product": "relaydesk",
                        "product_version": version,
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                response.raise_for_status()
                ticket = response.json()
                path = f"/api/tickets/{ticket['ticket_id']}/investigations"
                response = client.post(
                    path,
                    headers=auth,
                    json={"index_id": frozen["index"]["index_id"], "experiment_id": experiment},
                )
                row = {
                    "case": name,
                    "http_status": response.status_code,
                    "response": response.json(),
                    "reference_digests": {},
                }
                report["records"].append(row)
                save()
                response.raise_for_status()
                body = row["response"]
                row["reference_digests"] = audit(client, auth, body)
                denied = client.get(
                    f"/api/investigations/{body['investigation_id']}",
                    headers=identities["support_b"],
                )
                assert denied.status_code == 404
                report["boundary_checks"].append({"case": name, "cross_organization": 404})
                save()
                print(
                    json.dumps(
                        {
                            "case": name,
                            "status": body["status"],
                            "reason": body["stop_reason"],
                            "usage": body["usage"],
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            for value in identities.values():
                client.post("/api/auth/logout", headers=value)


if __name__ == "__main__":
    main()
