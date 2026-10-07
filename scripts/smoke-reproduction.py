"""新环境零模型 HTTP 证据与重启读回，保持构造数据及原文字节可核对。"""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.models.provider import ModelSettings
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "local") or output.exists() != args.readback:
        raise ValueError("只允许新的私有报告；读回不得重新执行数据或覆盖原尝试。")
    if ModelSettings().api_key is not None:
        raise ValueError("本检查要求未配置模型凭据。")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {"status": "started", "records": [], "sources": [], "model_calls": 0}
    )

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.readback:
        save()
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        # 同一端点的启动就绪等待，不选择备用服务或重复业务请求。
        for i in range(30):
            try:
                ready = client.get("/health/ready").status_code == 200
            except httpx.ConnectError:
                ready = False
            if ready:
                break
            if i == 29:
                raise RuntimeError("独立 API 未在就绪窗口内启动。")
            time.sleep(1)
        auth = []
        try:
            for name in ("support_a", "support_b"):
                account = next(a for a in accounts if a["username"] == name)
                response = client.post(
                    "/api/auth/login", json={k: account[k] for k in ("username", "password")}
                )
                response.raise_for_status()
                auth.append({"Authorization": "Bearer " + response.json()["access_token"]})
            client.headers.update(auth[0])

            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response

            def remember(path):
                body = request("GET", path).json()
                assert client.get(path, headers=auth[1]).status_code == 404
                report["records"].append({"path": path, "response": body, "sha256": digest(body)})
                save()
                return body

            if args.readback:
                assert report["status"] == "completed"
                proof = {"records": {}, "model_calls": 0, "cross_organization_404": True}
                for record in report["records"]:
                    body = request("GET", record["path"]).json()
                    assert body == record["response"] and digest(body) == record["sha256"]
                    assert client.get(record["path"], headers=auth[1]).status_code == 404
                    proof["records"][record["path"]] = digest(body)
                path = output.with_name(output.stem + "-after-restart.json")
                if path.exists():
                    raise ValueError("不覆盖重启读回证据。")
                path.write_text(
                    json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(json.dumps({"readback_records": len(proof["records"]), "model_calls": 0}))
                return
            ticket = request(
                "POST",
                "/api/tickets",
                json={
                    "title": "独立环境输入检查",
                    "description": "RD_TIMEOUT；未提供版本和现场证据。",
                    "product": "relaydesk",
                    "product_version": None,
                    "environment": "local_lab",
                    "source_type": "synthetic_case",
                },
            ).json()
            remember(f"/api/tickets/{ticket['ticket_id']}")
            run = request("POST", f"/api/tickets/{ticket['ticket_id']}/runs", json={}).json()
            assert run["status"] == "blocked"
            remember(f"/api/runs/{run['run_id']}")
            manifest = json.loads(
                (ROOT / "data/relaydesk/manifest.json").read_text(encoding="utf-8")
            )
            for source in manifest["sources"]:
                # 1.1 提供 Markdown / JSON；唯一 PDF 属于 2.0，保留其真实版本身份。
                if (
                    source["product_version"] != "1.1"
                    and source["path"] != "2.0/quick-reference.pdf"
                ):
                    continue
                raw = (ROOT / "data/relaydesk" / source["path"]).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == source["content_sha256"]
                payload = {
                    k: source[k]
                    for k in (
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
                doc = request("POST", "/api/documents/import", json=payload).json()
                path = f"/api/documents/{doc['document_id']}/revisions/{doc['revision_id']}"
                assert request("GET", path + "/original").content == raw
                remember(path)
                snapshot = request("POST", path + "/chunk-sets", json={}).json()
                chunks_path = f"/api/chunk-sets/{snapshot['chunk_set_id']}/chunks"
                chunks = []
                for offset in range(0, snapshot["chunk_count"], 100):
                    chunks.extend(
                        request("GET", chunks_path + f"?offset={offset}&limit=100").json()["items"]
                    )
                assert len(chunks) == snapshot["chunk_count"] > 0
                for chunk in chunks:
                    citation = remember(chunks_path + f"/{chunk['chunk_id']}/citation")
                    assert citation["text_verified"] and not citation["support_verified"]
                    assert "".join(p["excerpt"] for p in citation["parts"]) == chunk["text"]
                report["sources"].append(
                    {
                        "path": source["path"],
                        "format": source["format"],
                        "sha256": source["content_sha256"],
                        "chunks": len(chunks),
                    }
                )
            report.update(
                status="completed", model_key_configured=False, human_semantic_verified=False
            )
            save()
            print(
                json.dumps(
                    {
                        "records": len(report["records"]),
                        "sources": len(report["sources"]),
                        "model_calls": 0,
                    }
                )
            )
        finally:
            for headers in auth:
                client.post("/api/auth/logout", headers=headers).raise_for_status()


if __name__ == "__main__":
    main()
