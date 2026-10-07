"""零模型 HTTP / MCP 快照检查；max_model_calls=1 在规划前按预算停止。"""

import argparse
import json
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT
from supportops.skills.catalog import sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preparation", type=Path)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists() != args.readback
    ):
        raise ValueError("新检查使用项目内新 JSON，读回使用已有报告。")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    report = json.loads(output.read_text(encoding="utf-8")) if args.readback else {"model_calls": 0}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        auth = {}
        for name in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            response = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            response.raise_for_status()
            auth[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        try:
            if not args.readback:
                preparation = args.preparation.resolve()
                if not preparation.is_relative_to(ROOT):
                    raise ValueError("准备文件必须位于本项目。")
                lab = json.loads(preparation.read_text(encoding="utf-8"))
                response = client.post(
                    "/api/tickets",
                    headers=auth["support_a"],
                    json={
                        "title": "第6阶段第1轮 Skill 快照验收",
                        "description": "RelayDesk 1.1 启动失败 RD_CONFIG_INVALID，检查版本配置。",
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
                    headers=auth["support_a"],
                    json={
                        "index_id": frozen["index"]["index_id"],
                        "lab_run_id": lab["lab_run_id"],
                        "use_skills": True,
                        "max_model_calls": 1,
                    },
                )
                response.raise_for_status()
                report["response"] = response.json()
                # 先落盘，后断言，保存任何非预期结果；不自动重试。
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            body = report["response"]
            assert body["model_calls"] == 0
            assert body["stop_reason"] == "model_budget"
            assert body["mcp"]["transport"] == "stdio"
            assert body["skills"] == body["input_snapshot"]["skills"]
            assert digest(body["input_snapshot"]) == body["input_sha256"]
            for row in body["skills"]["loaded"]:
                assert sha256(row["body"].encode("utf-8")) == row["body_sha256"]
            identity = body["investigation_id"]
            response = client.get(f"/api/investigations/{identity}", headers=auth["support_a"])
            response.raise_for_status()
            assert response.json() == body
            assert (
                client.get(f"/api/investigations/{identity}", headers=auth["support_b"]).status_code
                == 404
            )
            entries = []
            for offset in range(0, 502, 100):
                response = client.get(
                    f"/api/retrieval/indexes/{frozen['index']['index_id']}/entries?offset={offset}&limit=100",
                    headers=auth["support_a"],
                )
                response.raise_for_status()
                entries.extend(response.json()["items"])
            assert digest(entries) == digest(frozen["entries"])
            report["readback" if args.readback else "checks"] = {
                "snapshot_unchanged": True,
                "cross_organization_status": 404,
                "frozen_entries_unchanged": len(entries),
                "model_calls": 0,
                "transport": "real_http_postgres_mcp",
            }
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"investigation_id": identity, "model_calls": 0, "checks": "passed"}))
        finally:
            for headers in auth.values():
                client.post("/api/auth/logout", headers=headers)


if __name__ == "__main__":
    main()
