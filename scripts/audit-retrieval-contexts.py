"""真实原文回查扩展上下文，核对所有命中仍指向冻结来源。"""

import hashlib
import json
from uuid import UUID

import httpx

from supportops.settings import ROOT


def main():
    directory = ROOT / "docs/verification/phase-3-round-3"
    output = directory / "context-audit.json"
    if output.exists():
        raise RuntimeError("拒绝覆盖上下文核对。")
    snapshot = json.loads((ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text("utf-8"))
    entries = {e["evidence_id"]: e for e in snapshot["entries"]}
    account = next(
        a
        for a in json.loads((ROOT / "local/demo-accounts.json").read_text("utf-8"))["accounts"]
        if a["username"] == "support_a"
    )
    counts = {"ranked_hits_checked": 0, "contexts_checked": 0, "parent_windows_checked": 0}
    citations = {}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        try:
            for path in (
                "dev-rrf.json",
                "dev-rrf-parent.json",
                "attempt-3/dev-rrf-rerank.json",
                "attempt-9/dev-rrf-rerank-parent.json",
            ):
                report = json.loads((directory / path).read_text("utf-8"))
                assert report["summary"]["completed"] == 36
                for attempt in report["attempts"]:
                    result = attempt["result"]
                    for hit in result["items"]:
                        frozen = entries[hit["evidence_id"]]
                        assert hit["text"] == frozen["payload"]["text"]
                        assert hit["source"] == frozen["payload"]["source"]
                        assert hit["product_version"] == attempt["request"]["product_version"]
                        counts["ranked_hits_checked"] += 1
                    for context in result.get("contexts", []):
                        assert (
                            context["text_sha256"]
                            == hashlib.sha256(context["text"].encode()).hexdigest()
                        )
                        assert context["text_verified"] and not context["support_verified"]
                        anchors = [
                            h
                            for h in result["items"]
                            if h["evidence_id"] in context["anchor_evidence_ids"]
                        ]
                        assert len(anchors) == len(context["anchor_evidence_ids"]) > 0
                        if context["kind"] == "parent":
                            anchor = anchors[0]
                            endpoint = anchor["reference_url"]
                            if endpoint not in citations:
                                response = client.get(endpoint)
                                response.raise_for_status()
                                citations[endpoint] = response.json()
                            parent = citations[endpoint]["parent"]
                            assert UUID(parent["parent_id"]) == UUID(context["source"]["parent_id"])
                            start, end = context["start"], context["end"]
                            assert parent["start"] <= start < end <= parent["end"]
                            assert (
                                context["text"]
                                == parent["text"][start - parent["start"] : end - parent["start"]]
                            )
                            assert (
                                len(context["text"])
                                <= result["advanced_configuration"]["parent_max_chars"]
                            )
                            for hit in anchors:
                                assert (
                                    hit["source"]["chunk_set_id"]
                                    == context["source"]["chunk_set_id"]
                                )
                                assert all(
                                    start <= s["start"] < s["end"] <= end
                                    for s in hit["source"]["spans"]
                                )
                            counts["parent_windows_checked"] += 1
                        else:
                            assert len(anchors) == 1 and context["text"] == anchors[0]["text"]
                        counts["contexts_checked"] += 1
                    if "contexts" in result:
                        assert (
                            sum(len(c["text"]) for c in result["contexts"])
                            == result["context_chars"]
                        )
                        assert (
                            result["context_chars"]
                            <= result["advanced_configuration"]["context_budget_chars"]
                        )
        finally:
            client.post("/api/auth/logout").raise_for_status()
    report = {
        "transport": "real_http_original_citations",
        **counts,
        "unique_original_citations": len(citations),
        "model_called": False,
        "ranked_source_and_text_match_frozen": True,
        "parent_window_matches_original": True,
        "support_verified": False,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
