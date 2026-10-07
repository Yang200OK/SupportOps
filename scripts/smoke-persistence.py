"""真实 HTTP 验证；重启前后的记录公开保存，临时 token 只留本地。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "local/restart-probe.json"


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def account_map() -> dict:
    return {
        item["username"]: item
        for item in json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
            "accounts"
        ]
    }


def login(client, accounts, username):
    result = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": accounts[username]["password"],
        },
    )
    assert result.status_code == 200, "演示登录失败。"
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def readback(client, accounts, output):
    report = json.loads(output.read_text(encoding="utf-8"))
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    b = {"Authorization": "Bearer " + probe["token_b"]}
    assert client.get("/api/auth/me", headers=b).status_code == 200
    a = login(client, accounts, "support_a")
    checks = []
    for record in report["records"]:
        auth = a if record["owner"] == "support_a" else b
        response = client.get("/api/tickets/" + record["ticket_id"], headers=auth)
        assert response.status_code == 200
        assert digest(response.json()) == record["sha256"]
        checks.append({"ticket_id": record["ticket_id"], "same_content_sha256": True})
    assert (
        client.get("/api/tickets/" + report["records"][0]["ticket_id"], headers=b).status_code
        == 404
    )
    for auth in (a, b):
        assert client.post("/api/auth/logout", headers=auth).status_code == 204
    report["restart_readback"] = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "same_session_after_restart": True,
        "cross_organization_still_denied": True,
        "tickets": checks,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 临时验证 token 已在服务端撤销，只删除本脚本的固定本地临时文件。
    PROBE.unlink()
    print("重启后真实 HTTP 读回通过：3 条工单内容一致，会话保留且组织隔离继续成立。")


def main() -> None:
    parser = argparse.ArgumentParser(description="SupportOps 持久化主链验收")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/persistence-smoke.json")
    parser.add_argument("--readback", action="store_true")
    parser.add_argument("--expect-database-down", action="store_true")
    args = parser.parse_args()
    accounts = account_map()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}", timeout=10, trust_env=False
    ) as client:
        if args.expect_database_down:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            probe = json.loads(PROBE.read_text(encoding="utf-8"))
            auth = {"Authorization": "Bearer " + probe["token_b"]}
            assert client.get("/health/live").status_code == 200
            for path in ("/health/ready", "/api/tickets"):
                response = client.get(path, headers=auth)
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
                assert "postgresql" not in response.text
            report["database_outage"] = {
                "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                "liveness_status": 200,
                "readiness_status": 503,
                "authenticated_list_status": 503,
                "no_storage_switch": True,
            }
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print("真实数据库停机验证通过：存活 200，就绪 / 工单 503，没有转入其它存储。")
            return
        if args.readback:
            readback(client, accounts, args.output)
            return
        if PROBE.exists():
            raise RuntimeError("前次重启验证尚未读回，请先对对应报告执行 --readback。")
        checks = []
        response = client.get("/health/ready")
        assert response.status_code == 200
        checks.append({"case": "readiness", "http_status": 200})
        assert client.get("/api/tickets").status_code == 401
        checks.append({"case": "unauthenticated", "http_status": 401})
        a, b, colleague = (
            login(client, accounts, name) for name in ("support_a", "support_b", "support_a2")
        )
        checks.append({"case": "three_logins_two_organizations", "http_status": 200})
        before = client.get("/api/tickets", headers=a).json()["total"]
        base = {
            "title": "本轮真实 HTTP 合成超时工单",
            "description": "RD_TIMEOUT，delivery_timeout_ms=2000。",
            "product": "relaydesk",
            "product_version": "1.1",
            "environment": "local_lab",
            "source_type": "synthetic_case",
        }
        records = []
        for name, auth, version in (
            ("support_a", a, "1.1"),
            ("support_b", b, "2.0"),
            ("support_a", a, None),
        ):
            response = client.post(
                "/api/tickets", json={**base, "product_version": version}, headers=auth
            )
            assert response.status_code == 201
            body = response.json()
            assert body["organization_id"] == accounts[name]["organization_id"]
            assert body["persisted"] is True
            assert body["intake_status"] == (
                "needs_clarification" if version is None else "ready_for_intake"
            )
            records.append(
                {
                    "owner": name,
                    "ticket_id": body["ticket_id"],
                    "sha256": digest(body),
                    "source_type": "synthetic_case",
                }
            )
        checks.append(
            {"case": "three_persisted_tickets_including_unknown_version", "http_status": 201}
        )
        response = client.post(
            "/api/tickets", json={**base, "organization_id": "forged"}, headers=a
        )
        assert response.status_code == 422
        assert client.get("/api/tickets", headers=a).json()["total"] == before + 2
        checks.append({"case": "forbidden_field_no_insert", "http_status": 422})
        own_id = records[0]["ticket_id"]
        response = client.get("/api/tickets/" + own_id, headers=colleague)
        assert response.status_code == 200
        checks.append({"case": "same_organization_colleague", "http_status": 200})
        other = client.get("/api/tickets/" + own_id, headers=b)
        missing = client.get("/api/tickets/" + str(uuid4()), headers=b)
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json()
        checks.append({"case": "cross_organization_and_missing_identical", "http_status": 404})
        items = client.get("/api/tickets", headers=b).json()["items"]
        assert all(
            item["organization_id"] == accounts["support_b"]["organization_id"] for item in items
        )
        checks.append({"case": "scoped_list", "http_status": 200})
        forged = {**a, "X-Organization-ID": accounts["support_b"]["organization_id"]}
        assert (
            client.get("/api/auth/me", headers=forged).json()["organization_id"]
            == accounts["support_a"]["organization_id"]
        )
        checks.append({"case": "forged_header_ignored", "http_status": 200})
        assert client.post("/api/auth/logout", headers=a).status_code == 204
        assert client.get("/api/auth/me", headers=a).status_code == 401
        assert client.post("/api/auth/logout", headers=colleague).status_code == 204
        checks.append({"case": "server_side_logout_revocation", "http_status": 401})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "transport": "real_http",
                    "data_kind": "synthetic_case",
                    "port": args.port,
                    "checks": checks,
                    "records": records,
                    "not_run": ["model_api", "browser_ui"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        PROBE.write_text(
            json.dumps({"token_b": b["Authorization"].removeprefix("Bearer ")}), encoding="utf-8"
        )
        print(f"真实 HTTP 主链通过：{len(checks)} 个场景；下一步重启 API / 数据库后 --readback。")


if __name__ == "__main__":
    main()
