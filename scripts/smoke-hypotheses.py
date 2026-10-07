"""真实隔离实验 / HTTP / MCP / 百炼，逐次记录；不自动重试失败。"""

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.investigations.live_sources import verify_snapshot
from supportops.retrieval.contexts import text_hash
from supportops.settings import ROOT


def preparation_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_lab", ROOT / "scripts/prepare-investigation-lab.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit(client, auth, body):
    assert digest(body["input_snapshot"]) == body["input_sha256"]
    assert not body["current_incident_verified"] and not body["human_reviewed"]
    for tool in body["tool_results"]:
        assert digest(tool["result"]) == tool["result_sha256"]
        if "snapshot" in tool["result"]:
            verify_snapshot(tool["result"])
            assert (
                tool["result"]["snapshot"]["registration_sha256"]
                == body["input_snapshot"]["registration_sha256"]
            )
    contexts = {c["context_id"]: c for c in body["contexts"]}
    evidence = {e["evidence_id"]: e for e in body["evidence"]}
    refs = {}
    for item in evidence.values():
        assert item["product_version"] == body["input_snapshot"]["ticket"]["product_version"]
        response = client.get(item["reference_url"], headers=auth)
        response.raise_for_status()
        value = response.json()
        assert value["text_verified"]
        if item["source"].get("source_type", "").startswith("current_"):
            assert value["evidence"] == item
            assert json.loads(item["text"]) == value["source_data"]
            assert value["snapshot"]["sha256"] == item["source"]["snapshot_sha256"]
        else:
            assert value["chunk"]["text"] == item["text"]
        refs[item["evidence_id"]] = digest(value)
    if body["report"]:
        for claim in body["report"]["claims"]:
            for citation in claim["citations"]:
                context = contexts[citation["context_id"]]
                assert context["text"][citation["start"] : citation["end"]] == citation["quote"]
                assert text_hash(context["text"]) == citation["context_text_sha256"]
    return refs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=("configuration", "pool", "cache", "downstream", "missing"),
        default=("configuration", "pool", "cache", "downstream", "missing"),
        help="显式选择新的 dev 尝试；不自动重试或覆盖既有记录",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists() != args.readback
    ):
        raise ValueError("新运行使用新的项目内 JSON，读回必须使用已有报告。")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    prepare = preparation_module()
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {
            "transport": "real_docker_http_mcp_bailian",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "fixed_cases": list(prepare.FAMILIES),
            "selected_cases": args.cases,
            "records": [],
            "attempts": [],
            "cost_cny": None,
            "human_reviewed": False,
        }
    )

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=260, trust_env=False) as client:
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
                    body = record["response"]
                    response = client.get(
                        f"/api/investigations/{body['investigation_id']}", headers=auth
                    )
                    response.raise_for_status()
                    assert response.json() == body
                    assert audit(client, auth, body) == record["reference_digests"]
                    reads.append(body["investigation_id"])
                entries = []
                for offset in range(0, 502, 100):
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
                    "model_calls": 0,
                }
                save()
                print(json.dumps(report["restart_readback"]))
                return
            save()
            for family in args.cases:
                attempt = {
                    "family_for_test_harness_only": family,
                    "attempt": 1,
                    "status": "started",
                }
                report["attempts"].append(attempt)
                save()
                try:
                    prepared = prepare.prepare(
                        family, output.with_name(output.stem + "-" + family + "-prepare.json")
                    )
                    code = prepared["probe_error_code"] or "症状信息缺失，没有本次异常请求"
                    response = client.post(
                        "/api/tickets",
                        headers=auth,
                        json={
                            "title": "本次实验调查构造案例 " + prepared["run_id"][:8],
                            "description": (
                                f"RelayDesk 1.1 本次实验报告 {code}。请读取绑定运行的真实观测，"
                                "提出和核对原因候选；证据不足时保持未决，不执行修复。"
                            ),
                            "product": "relaydesk",
                            "product_version": "1.1",
                            "environment": "local_lab",
                            "source_type": "synthetic_case",
                        },
                    )
                    response.raise_for_status()
                    ticket = response.json()
                    response = client.post(
                        f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
                        headers=auth,
                        json={
                            "index_id": frozen["index"]["index_id"],
                            "lab_run_id": prepared["lab_run_id"],
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    # 先保存实际返回，再进行强断言；失败不丢失模型用量。
                    record = {
                        "case": family,
                        "http_status": response.status_code,
                        "response": body,
                        "reference_digests": {},
                    }
                    report["records"].append(record)
                    attempt.update(
                        status=body["status"],
                        stop_reason=body["stop_reason"],
                        investigation_id=body["investigation_id"],
                    )
                    save()
                    record["reference_digests"] = audit(client, auth, body)
                    other = client.get(
                        f"/api/investigations/{body['investigation_id']}",
                        headers=identities["support_b"],
                    )
                    assert other.status_code == 404
                    record["cross_organization_status"] = other.status_code
                    if family != "missing":
                        origin = (
                            "current_startup_diagnostic"
                            if family == "configuration"
                            else "current_lab_observations"
                        )
                        assert any(
                            e["source"].get("source_type") == origin for e in body["evidence"]
                        ), "没有读取本次故障观测。"
                    if family == "missing":
                        assert not any(
                            h["status"] in ("supported", "refuted") for h in body["hypotheses"]
                        ), "缺失异常请求不能决定其原因。"
                    assert body["status"] in ("completed", "needs_clarification"), body[
                        "stop_reason"
                    ]
                    attempt["checks"] = "passed"
                    print(
                        json.dumps(
                            {
                                "case": family,
                                "investigation_id": body["investigation_id"],
                                "status": body["status"],
                                "stop_reason": body["stop_reason"],
                                "hypotheses": {
                                    h["hypothesis_id"]: h["status"] for h in body["hypotheses"]
                                },
                                "tool_calls": body["tool_calls"],
                                "usage": body["usage"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                except Exception as error:
                    attempt.update(checks="failed", error_type=type(error).__name__)
                    print(
                        json.dumps(
                            {
                                "case": family,
                                "status": "validation_failed",
                                "error_type": type(error).__name__,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                finally:
                    prepare.cleanup()
                    save()
            if any(a.get("checks") != "passed" for a in report["attempts"]):
                raise SystemExit(1)
        finally:
            for identity in identities.values():
                client.post("/api/auth/logout", headers=identity).raise_for_status()


if __name__ == "__main__":
    main()
