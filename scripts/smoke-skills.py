"""固定构造工单的真实 Skill / MCP / 百炼检查；不重试失败。"""

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT
from supportops.skills.catalog import sha256


def investigation_module():
    spec = importlib.util.spec_from_file_location(
        "hypothesis_smoke", ROOT / "scripts/smoke-hypotheses.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_skills(body):
    bundle = body["input_snapshot"]["skills"]
    assert body["skills"] == bundle
    assert bundle["loaded"] and len(bundle["loaded"]) <= 2
    assert all("body" not in row for row in bundle["catalog"]["items"])
    for row in bundle["loaded"]:
        assert sha256(row["body"].encode("utf-8")) == row["body_sha256"]
        assert (
            row["selected_product_version"] == body["input_snapshot"]["ticket"]["product_version"]
        )
        assert row["selected_mode"] == body["input_snapshot"]["lab_registration"]["mode"]
        assert row["body_verified"] and row["sources_verified"]
        assert row["matched_signals"]
    assert body["tool_calls"] <= 6 and body["model_calls"] <= 8
    assert any(event["event"] == "skills_loaded" for event in body["events"])
    assert not any(e["evidence_id"].startswith("skill:") for e in body["evidence"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("configuration", "pool", "cache", "downstream"))
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists() != args.readback
    ):
        raise ValueError("新尝试使用项目内新 JSON，读回使用已有报告。")
    if not args.readback and not args.case:
        raise ValueError("新运行必须明确选择固定场景。")
    previous = investigation_module()
    prepare = previous.preparation_module()
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
            "transport": "real_docker_http_mcp_bailian",
            "case_for_test_harness_only": args.case,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "started",
            "cost_cny": None,
            "human_semantic_reviewed": False,
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
            save()
            if args.readback:
                body = report["response"]
                response = client.get(
                    f"/api/investigations/{body['investigation_id']}", headers=auth
                )
                response.raise_for_status()
                assert response.json() == body
                audit_skills(body)
                assert previous.audit(client, auth, body) == report["reference_digests"]
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
                    "snapshot_unchanged": True,
                    "references_unchanged": True,
                    "frozen_entries_unchanged": len(entries),
                    "model_calls": 0,
                }
                save()
                print(json.dumps(report["restart_readback"]))
                return
            assert client.get("/api/skills").status_code == 401
            catalogue = client.get("/api/skills", headers=auth)
            catalogue.raise_for_status()
            report["catalog"] = catalogue.json()
            prepared = prepare.prepare(args.case, output.with_name(output.stem + "-prepare.json"))
            code = prepared["probe_error_code"]
            response = client.post(
                "/api/tickets",
                headers=auth,
                json={
                    "title": "第6阶段Skill验收 " + prepared["run_id"][:8],
                    "description": (
                        f"RelayDesk 1.1 本次隔离实验报告 {code}。"
                        "请只读本次观测调查，证据不足保持未决。"
                    ),
                    "product": "relaydesk",
                    "product_version": "1.1",
                    "environment": "local_lab",
                    "source_type": "synthetic_case",
                },
            )
            response.raise_for_status()
            ticket = response.json()
            report["ticket_id"] = ticket["ticket_id"]
            save()
            response = client.post(
                f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
                headers=auth,
                json={
                    "index_id": frozen["index"]["index_id"],
                    "lab_run_id": prepared["lab_run_id"],
                    "use_skills": True,
                },
            )
            response.raise_for_status()
            body = response.json()
            report.update(response=body, status=body["status"], reference_digests={})
            save()
            audit_skills(body)
            report["reference_digests"] = previous.audit(client, auth, body)
            other = client.get(
                f"/api/investigations/{body['investigation_id']}", headers=identities["support_b"]
            )
            assert other.status_code == 404
            report["cross_organization_status"] = other.status_code
            save()
            assert body["status"] == "completed", body["stop_reason"]
            report["checks"] = "passed"
            save()
            print(
                json.dumps(
                    {
                        "status": body["status"],
                        "investigation_id": body["investigation_id"],
                        "skills": [s["skill_id"] for s in body["skills"]["loaded"]],
                        "known_model_calls": body["usage"]["known_model_calls"],
                        "unknown_model_calls": body["usage"]["unknown_model_calls"],
                    }
                )
            )
        except Exception as error:
            report["check_error_type"] = type(error).__name__
            save()
            raise
        finally:
            if not args.readback:
                prepare.cleanup()
            for headers in identities.values():
                client.post("/api/auth/logout", headers=headers)


if __name__ == "__main__":
    main()
