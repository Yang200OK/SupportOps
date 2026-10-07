"""真实 HTTP 导入自建资料并核对原文；报告不含凭据或 token。"""

import argparse
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = {"transport": "real_http", "checks": [], "records": [], "model_calls": 0}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        identities = {}
        for account in accounts:
            response = client.post(
                "/api/auth/login",
                json={name: account[name] for name in ("username", "password")},
            )
            assert response.status_code == 200, "演示登录失败。"
            identities[account["username"]] = {
                "Authorization": "Bearer " + response.json()["access_token"]
            }
        auth = identities["support_a"]
        if args.readback:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            for record in report["records"]:
                path = (
                    f"/api/documents/{record['document_id']}/revisions/"
                    f"{record['revision_id']}/original"
                )
                response = client.get(path, headers=auth)
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == record["content_sha256"]
            report["restart_readback"] = {
                "status": "passed",
                "records": len(report["records"]),
                "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        else:
            sources = json.loads(
                (ROOT / "data/relaydesk/manifest.json").read_text(encoding="utf-8")
            )["sources"]
            for source in sources:
                raw = (ROOT / "data/relaydesk" / source["path"]).read_bytes()
                payload = {
                    name: source[name]
                    for name in (
                        "source_key",
                        "title",
                        "product",
                        "product_version",
                        "source_type",
                        "license",
                        "filename",
                        "format",
                    )
                }
                payload["content_base64"] = base64.b64encode(raw).decode()
                response = client.post("/api/documents/import", headers=auth, json=payload)
                assert response.status_code == 200, "资料导入 HTTP 失败。"
                item = response.json()
                assert item["status"] == "parsed", "样例资料解析失败。"
                assert item["content_sha256"] == source["content_sha256"]
                repeated = client.post("/api/documents/import", headers=auth, json=payload).json()
                assert repeated["reused"] and repeated["revision_id"] == item["revision_id"]
                path = f"/api/documents/{item['document_id']}/revisions/{item['revision_id']}"
                assert client.get(path, headers=identities["support_a2"]).status_code == 200
                assert client.get(path, headers=identities["support_b"]).status_code == 404
                assert (
                    client.get(path + "/original", headers=identities["support_b"]).status_code
                    == 404
                )
                assert client.get(path + "/original", headers=auth).content == raw
                report["records"].append(
                    {
                        name: item[name]
                        for name in (
                            "document_id",
                            "revision_id",
                            "content_sha256",
                            "product_version",
                            "status",
                            "format",
                        )
                    }
                )
            report["checks"].extend(
                [
                    "16_sources_three_versions_and_formats",
                    "idempotent_reimport",
                    "original_bytes_and_sha256",
                    "same_org_shared",
                    "cross_org_detail_and_original_404",
                ]
            )
            key = "http-concurrent-" + uuid4().hex[:12]
            payload = {
                "source_key": key,
                "title": "HTTP 并发验证资料",
                "product": "relaydesk",
                "product_version": "1.1",
                "source_type": "demo_product",
                "license": "CC0-1.0",
                "filename": "concurrent.md",
                "format": "md",
                "content_base64": base64.b64encode(b"# Concurrent\n\nRD_TIMEOUT").decode(),
            }
            with ThreadPoolExecutor(max_workers=4) as executor:
                responses = list(
                    executor.map(
                        lambda _: client.post("/api/documents/import", headers=auth, json=payload),
                        range(4),
                    )
                )
            assert all(response.status_code == 200 for response in responses)
            assert len({response.json()["revision_id"] for response in responses}) == 1
            assert sum(not response.json()["reused"] for response in responses) == 1
            report["checks"].append("four_concurrent_real_http_requests_one_revision")
            payload.update(content_base64=base64.b64encode(b"# Updated\n\nRD_TIMEOUT").decode())
            changed = client.post("/api/documents/import", headers=auth, json=payload).json()
            assert changed["revision_number"] == 2
            report["checks"].append("content_change_creates_revision_2")
            payload.update(
                source_key=key + "-failed",
                filename="broken.pdf",
                format="pdf",
                content_base64=base64.b64encode(b"%PDF-broken").decode(),
            )
            failed = client.post("/api/documents/import", headers=auth, json=payload).json()
            assert failed["status"] == "failed" and failed["text"] is None and not failed["blocks"]
            report["checks"].append("failed_parse_saved_without_usable_text")
            report["executed_at_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"真实资料 HTTP 检查通过：{len(report['checks'])} 场景。")


if __name__ == "__main__":
    main()
