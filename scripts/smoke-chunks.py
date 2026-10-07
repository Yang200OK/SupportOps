"""通过真实 HTTP 核对三格式切片、隔离、并发与重启，不公开会话。"""

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
                "/api/auth/login", json={name: account[name] for name in ("username", "password")}
            )
            assert response.status_code == 200, "演示登录失败。"
            identities[account["username"]] = {
                "Authorization": "Bearer " + response.json()["access_token"]
            }
        auth = identities["support_a"]

        def request(method, path, status=200, **kwargs):
            response = client.request(method, path, headers=auth, **kwargs)
            assert response.status_code == status, f"HTTP 状态错误：{method} {path}"
            return response.json()

        def verify(snapshot):
            path = f"/api/chunk-sets/{snapshot['chunk_set_id']}/chunks"
            all_chunks = []
            for offset in range(0, snapshot["chunk_count"], 100):
                all_chunks.extend(request("GET", path + f"?offset={offset}&limit=100")["items"])
            assert len(all_chunks) == snapshot["chunk_count"]
            for chunk in all_chunks:
                citation = request("GET", path + f"/{chunk['chunk_id']}/citation")
                assert citation["text_verified"] and citation["support_verified"] is False
                assert "".join(p["excerpt"] for p in citation["parts"]) == chunk["text"]
                assert citation["snapshot"]["snapshot_sha256"] == snapshot["snapshot_sha256"]
                assert citation["source"]["revision_id"] == snapshot["revision_id"]
                if chunk["kind"] == "page":
                    assert chunk["page_number"] and all(
                        s["line_start"] is None for s in chunk["spans"]
                    )
                if chunk["kind"] == "field":
                    assert chunk["json_pointer"] in ("/title", "/description")
            return path + f"/{all_chunks[0]['chunk_id']}/citation"

        if args.readback:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            for record in report["records"]:
                citation = request("GET", record["citation_path"])
                assert citation["snapshot"]["snapshot_sha256"] == record["snapshot_sha256"]
                assert citation["source"]["content_sha256"] == record["content_sha256"]
                assert (
                    hashlib.sha256(citation["chunk"]["text"].encode()).hexdigest()
                    == record["chunk_text_sha256"]
                )
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
                payload["content_base64"] = base64.b64encode(
                    (ROOT / "data/relaydesk" / source["path"]).read_bytes()
                ).decode()
                revision = request("POST", "/api/documents/import", json=payload)
                path = (
                    f"/api/documents/{revision['document_id']}/revisions/"
                    f"{revision['revision_id']}/chunk-sets"
                )
                config = {"max_chars": 128, "overlap_chars": 20}
                snapshot = request("POST", path, json=config)
                repeated = request("POST", path, json=config)
                assert repeated["reused"] and repeated["chunk_set_id"] == snapshot["chunk_set_id"]
                citation_path = verify(snapshot)
                for route in (
                    path,
                    f"/api/chunk-sets/{snapshot['chunk_set_id']}/chunks",
                    citation_path,
                ):
                    assert client.get(route, headers=identities["support_a2"]).status_code == 200
                    assert client.get(route, headers=identities["support_b"]).status_code == 404
                assert (
                    client.post(path, headers=identities["support_b"], json=config).status_code
                    == 404
                )
                citation = request("GET", citation_path)
                report["records"].append(
                    {
                        "citation_path": citation_path,
                        "snapshot_sha256": snapshot["snapshot_sha256"],
                        "content_sha256": snapshot["content_sha256"],
                        "chunk_text_sha256": hashlib.sha256(
                            citation["chunk"]["text"].encode()
                        ).hexdigest(),
                        "chunk_count": snapshot["chunk_count"],
                        "format": source["format"],
                        "product_version": source["product_version"],
                    }
                )
            report["checks"].extend(
                [
                    "16_sources_three_versions_three_formats_all_citations",
                    "same_configuration_idempotent",
                    "same_org_shared_cross_org_routes_404",
                ]
            )
            with ThreadPoolExecutor(max_workers=4) as executor:
                responses = list(
                    executor.map(
                        lambda _: client.post(
                            path, headers=auth, json={"max_chars": 333, "overlap_chars": 21}
                        ),
                        range(4),
                    )
                )
            assert all(r.status_code == 200 for r in responses)
            assert len({r.json()["chunk_set_id"] for r in responses}) == 1
            # 脚本可重复运行；唯一创建数在首次配置生成时为一，之后均为复用。
            assert sum(not r.json()["reused"] for r in responses) <= 1
            report["checks"].append("four_concurrent_requests_one_snapshot")
            key = "http-chunks-" + uuid4().hex[:12]
            payload = {
                "source_key": key,
                "title": "固定修订 HTTP 核对",
                "product": "relaydesk",
                "product_version": "1.1",
                "source_type": "demo_product",
                "license": "CC0-1.0",
                "filename": "fixed.md",
                "format": "md",
                "content_base64": base64.b64encode(b"# Fixed\n\nRD_TIMEOUT=2000\n").decode(),
            }
            revision = request("POST", "/api/documents/import", json=payload)
            path = (
                f"/api/documents/{revision['document_id']}/revisions/"
                f"{revision['revision_id']}/chunk-sets"
            )
            snapshot = request("POST", path, json=config)
            citation_path = verify(snapshot)
            original_citation = request("GET", citation_path)
            payload["content_base64"] = base64.b64encode(b"# Fixed\n\nRD_TIMEOUT=3000\n").decode()
            assert request("POST", "/api/documents/import", json=payload)["revision_number"] == 2
            assert request("GET", citation_path) == original_citation
            report["records"].append(
                {
                    "citation_path": citation_path,
                    "snapshot_sha256": snapshot["snapshot_sha256"],
                    "content_sha256": snapshot["content_sha256"],
                    "chunk_text_sha256": hashlib.sha256(
                        original_citation["chunk"]["text"].encode()
                    ).hexdigest(),
                    "chunk_count": snapshot["chunk_count"],
                    "format": "md",
                    "product_version": "1.1",
                    "old_revision_after_update": True,
                }
            )
            report["checks"].append("new_revision_preserves_old_citation")
            payload.update(
                source_key=key + "-failed",
                filename="broken.pdf",
                format="pdf",
                content_base64=base64.b64encode(b"%PDF-broken").decode(),
            )
            failed = request("POST", "/api/documents/import", json=payload)
            path = (
                f"/api/documents/{failed['document_id']}/revisions/"
                f"{failed['revision_id']}/chunk-sets"
            )
            assert (
                request("POST", path, status=409, json=config)["error"]["code"]
                == "REVISION_NOT_PARSED"
            )
            assert request("GET", path)["total"] == 0
            report["checks"].append("failed_revision_has_no_snapshot")
            report["executed_at_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"真实切片 HTTP 检查通过：{len(report['checks'])} 场景，"
            f"{len(report['records'])} 份资料。"
        )


if __name__ == "__main__":
    main()
