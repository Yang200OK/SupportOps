"""零模型核对比较、两臂原文与旧记录；重启前后输出摘要必须相同。"""

import argparse
import json

import httpx

from supportops.chunks.chunking import digest
from supportops.coordination.envelope import expand_source
from supportops.investigations.live_sources import verify_snapshot
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / "docs/verification/phase-7-round-3") or output.exists():
        raise ValueError("使用本轮新的项目内输出文件。")
    report = json.loads(
        (ROOT / "docs/verification/phase-7-round-3/pairs-attempt1.json").read_text(encoding="utf-8")
    )
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    proof = {"model_calls": 0, "records": [], "references": {}, "entries": 502}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        tokens = []
        try:
            for name in ("support_a", "support_b"):
                account = next(a for a in accounts if a["username"] == name)
                login = client.post(
                    "/api/auth/login", json={"username": name, "password": account["password"]}
                )
                login.raise_for_status()
                tokens.append({"Authorization": "Bearer " + login.json()["access_token"]})
            auth, other = tokens

            def get(path):
                response = client.get(path, headers=auth)
                response.raise_for_status()
                return response.json()

            body = get("/api/coordination-comparison")
            assert body["complete"] and all(
                p["same_total_budget_conditions"] for p in body["pairs"]
            )
            assert client.get("/api/coordination-comparison", headers=other).status_code == 404
            proof.update(
                comparison=body, comparison_sha256=digest(body), cross_organization_404=True
            )
            for item in report["attempts"]:
                original = item["response"]
                path = (
                    f"/api/investigations/{original['investigation_id']}"
                    if item["arm"] == "single"
                    else f"/api/coordination-executions/{original['execution_id']}"
                )
                current = get(path)
                assert current == original
                assert client.get(path, headers=other).status_code == 404
                proof["records"].append({"path": path, "sha256": digest(current)})
                if item["arm"] == "single":
                    evidence = current["evidence"]
                    for tool in current["tool_results"]:
                        if "snapshot" in tool["result"]:
                            verify_snapshot(tool["result"])
                else:
                    evidence = []
                    for task in current["tasks"].values():
                        evidence.extend(task["evidence"])
                        for result in task["results"]:
                            if "snapshot" in result["value"]:
                                value = result["value"]
                                verify_snapshot(
                                    {
                                        **value,
                                        "evidence": [
                                            {**e, "source": expand_source(e, value["snapshot"])}
                                            for e in value["evidence"]
                                        ],
                                    }
                                )
                for e in evidence:
                    reference_path = (
                        e["reference_url"]
                        if item["arm"] == "single"
                        else f"{path}/evidence/{e['evidence_id']}"
                    )
                    reference = get(reference_path)
                    if item["arm"] == "multi" or e["source"].get("source_type", "").startswith(
                        "current_"
                    ):
                        assert reference["evidence"]["text"] == e["text"]
                    else:
                        assert (
                            reference["text_verified"] and reference["chunk"]["text"] == e["text"]
                        )
                    assert e["product_version"] == "1.1"
                    proof["references"][reference_path] = digest(reference)
            # 旧执行和任务板保留，重启读回不触发任何模型。
            old = json.loads(
                (
                    ROOT / "docs/verification/phase-7-round-2/live-configuration-attempt2.json"
                ).read_text(encoding="utf-8")
            )
            assert get(f"/api/coordination-executions/{old['execution_id']}") == old["final"]
            assert get(f"/api/coordination-boards/{old['board']['board_id']}") == old["board"]
            proof["old_round_unchanged"] = True
            entries = []
            for offset in range(0, 502, 100):
                entries.extend(
                    get(
                        f"/api/retrieval/indexes/{frozen['index']['index_id']}/entries?offset={offset}&limit=100"
                    )["items"]
                )
            assert entries == frozen["entries"]
            proof["entries_sha256"] = digest(entries)
        finally:
            for auth in tokens:
                client.post("/api/auth/logout", headers=auth).raise_for_status()
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "records": len(proof["records"]),
                "references": len(proof["references"]),
                "entries": 502,
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
