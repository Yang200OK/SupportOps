"""以真实 HTTP 复核运行持久化；公开证据不含口令或会话。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from supportops.settings import ROOT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readback", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    accounts = {
        item["username"]: item
        for item in json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
            "accounts"
        ]
    }
    report = {"transport": "real_http", "data_kind": "synthetic_case", "checks": [], "records": []}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=10, trust_env=False) as client:
        identities = {}
        for name in ("support_a", "support_a2", "support_b"):
            response = client.post(
                "/api/auth/login", json={"username": name, "password": accounts[name]["password"]}
            )
            assert response.status_code == 200, "本地演示登录失败。"
            identities[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        a, colleague, b = (identities[name] for name in ("support_a", "support_a2", "support_b"))
        if args.readback:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            for record in report["records"]:
                response = client.get(
                    "/api/runs/" + record["run_id"], headers=identities[record["owner"]]
                )
                assert response.status_code == 200, "重启后运行读回失败。"
                assert response.json()["input_sha256"] == record["input_sha256"]
                assert response.json()["status"] == record["status"]
            report["restart_readback"] = {
                "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "passed",
                "same_sha256": True,
                "records": len(report["records"]),
            }
        else:
            report["executed_at_utc"] = datetime.now(timezone.utc).isoformat()
            assert client.get("/health/ready").status_code == 200
            assert client.get("/api/runs").status_code == 401
            report["checks"].append("database_ready_and_runs_require_authentication")
            for name, auth, version in (
                ("support_a", a, "1.1"),
                ("support_a", a, None),
                ("support_b", b, "2.0"),
            ):
                ticket = client.post(
                    "/api/tickets",
                    headers=auth,
                    json={
                        "title": "本轮运行持久化构造工单",
                        "description": "RD_TIMEOUT；本案例只验证输入检查与运行记录。",
                        "product": "relaydesk",
                        "product_version": version,
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                assert ticket.status_code == 201
                response = client.post(
                    f"/api/tickets/{ticket.json()['ticket_id']}/runs",
                    headers=auth,
                    json={"kind": "intake_check"},
                )
                assert response.status_code == 201
                run = response.json()
                canonical = json.dumps(
                    run["input_snapshot"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == run["input_sha256"]
                assert len(run["events"]) == 3
                assert run["status"] == ("blocked" if version is None else "succeeded")
                assert run["model"] is run["usage"] is run["cost_cny"] is None
                report["records"].append(
                    {
                        "owner": name,
                        "ticket_id": run["ticket_id"],
                        "run_id": run["run_id"],
                        "status": run["status"],
                        "input_sha256": run["input_sha256"],
                    }
                )
            report["checks"].append("three_runs_sha256_events_and_succeeded_blocked")
            first = report["records"][0]
            assert client.get("/api/runs/" + first["run_id"], headers=colleague).status_code == 200
            foreign = client.get("/api/runs/" + first["run_id"], headers=b)
            missing = client.get("/api/runs/" + str(uuid4()), headers=b)
            assert foreign.status_code == missing.status_code == 404
            assert foreign.json() == missing.json()
            report["checks"].append("colleague_shared_foreign_and_missing_identical_404")
            response = client.post(
                f"/api/tickets/{first['ticket_id']}/runs",
                headers=a,
                json={"kind": "intake_check", "status": "succeeded"},
            )
            assert response.status_code == 422
            report["checks"].append("forged_run_state_rejected_422")
            example = json.loads(
                (ROOT / "examples/evaluation-dataset.v1.json").read_text(encoding="utf-8")
            )
            response = client.post("/api/evaluations/validate", headers=a, json=example)
            assert response.status_code == 200 and response.json()["executed"] is False
            assert response.json()["total"] == 2 and "expected" not in response.json()
            example["tasks"][1]["source_group"] = example["tasks"][0]["source_group"]
            assert (
                client.post("/api/evaluations/validate", headers=a, json=example).status_code == 422
            )
            report["checks"].append("evaluation_valid_and_group_leak_rejected")
        for auth in identities.values():
            assert client.post("/api/auth/logout", headers=auth).status_code == 204
            assert client.get("/api/auth/me", headers=auth).status_code == 401
        report["status"] = "passed"
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "真实 HTTP 工作台验证通过。" if not args.readback else "API 重启后运行记录与摘要读回一致。"
    )


if __name__ == "__main__":
    main()
