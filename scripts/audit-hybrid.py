"""只读核对本轮冻结来源与上一轮向量排名，不输出会话或凭据。"""

import argparse
import json
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("拒绝覆盖来源审计。")
    before = json.loads(
        (ROOT / "docs/verification/phase-3-round-1/evaluation-dev.json").read_text(encoding="utf-8")
    )
    after = json.loads(
        (ROOT / "docs/verification/phase-3-round-2/dev-vector.json").read_text(encoding="utf-8")
    )
    for key in ("dataset_sha256", "qrels_sha256", "index"):
        assert before[key] == after[key], key
    assert [r["task_id"] for r in before["attempts"]] == [r["task_id"] for r in after["attempts"]]
    assert [[h["evidence_id"] for h in r["result"]["items"]] for r in before["attempts"]] == [
        [h["evidence_id"] for h in r["result"]["items"]] for r in after["attempts"]
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    account = next(
        a
        for a in json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
            "accounts"
        ]
        if a["username"] == "support_a"
    )
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        try:
            endpoint = f"/api/retrieval/indexes/{frozen['index']['index_id']}"
            response = client.get(endpoint)
            response.raise_for_status()
            assert response.json() == {**frozen["index"], "reused": False}
            entries = []
            for offset in range(0, frozen["index"]["entry_count"], 100):
                response = client.get(endpoint + f"/entries?offset={offset}&limit=100")
                response.raise_for_status()
                entries.extend(response.json()["items"])
            assert digest(entries) == digest(frozen["entries"])
        finally:
            client.post("/api/auth/logout").raise_for_status()
    report = {
        "transport": "real_http_read_only",
        "index_and_entries_match_frozen": True,
        "entries": len(entries),
        "entries_sha256": digest(entries),
        "previous_vector_rankings_equal": len(before["attempts"]),
        "dataset_sha256": after["dataset_sha256"],
        "qrels_sha256": after["qrels_sha256"],
        "model_called": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
