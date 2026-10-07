"""对已启动的本机 API 做真实 HTTP 验证，连接失败直接报错。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="SupportOps 首轮真实 HTTP 验证")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--output", type=Path, default=Path("outputs/http-smoke.json"))
    args = parser.parse_args()
    payload = {
        "title": "升级后投递请求超时",
        "description": "RD_TIMEOUT，delivery_timeout_ms=2000。",
        "product": "relaydesk",
        "product_version": "1.1",
        "environment": "local_lab",
        "source_type": "synthetic_case",
    }
    checks = []
    # 关闭系统代理继承，让本机请求明确指向被验证的服务。
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}", timeout=5, trust_env=False
    ) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["service"] == "supportops"
        checks.append({"case": "liveness", "http_status": 200, "body": response.json()})

        response = client.post("/api/tickets/validate", json=payload)
        assert response.status_code == 200
        assert response.json()["intake_status"] == "ready_for_intake"
        assert response.json()["persisted"] is False
        assert response.json()["draft"]["description"] == payload["description"]
        checks.append({"case": "known_version", "http_status": 200, "body": response.json()})

        response = client.post("/api/tickets/validate", json={**payload, "product_version": None})
        assert response.status_code == 200
        assert response.json()["missing_fields"] == ["product_version"]
        assert response.json()["intake_status"] == "needs_clarification"
        checks.append({"case": "unknown_version", "http_status": 200, "body": response.json()})

        response = client.post(
            "/api/tickets/validate", json={**payload, "organization_id": "other"}
        )
        assert response.status_code == 422
        assert {"field": "organization_id", "type": "extra_forbidden"} in response.json()["error"][
            "details"
        ]
        checks.append({"case": "forbidden_field", "http_status": 422, "body": response.json()})

        response = client.get("/openapi.json")
        response.raise_for_status()
        assert "/api/tickets/validate" in response.json()["paths"]
        checks.append({"case": "openapi", "http_status": 200})

    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "real_http",
        "port": args.port,
        "checks": checks,
        "not_run": ["database", "model_api", "browser_ui"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"真实 HTTP 验证通过：{len(checks)} 个场景，记录：{args.output}")


if __name__ == "__main__":
    main()
