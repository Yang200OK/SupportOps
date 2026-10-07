"""用公开文档定位生成引文差异，保留失败，不放宽原句校验。"""

import argparse
import json
from pathlib import Path

import httpx

from supportops.models.provider import ModelFailure, Provider
from supportops.rag.contracts import AnswerDraft
from supportops.rag.model import AnswerModelSettings, chat_json
from supportops.rag.service import GENERATE
from supportops.rag.validation import bind_citations
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--answer-task")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("报告应为项目内的新路径。")
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
    request = json.loads(
        (ROOT / "docs/verification/phase-4-round-2/http-attempt-1.json").read_text(encoding="utf-8")
    )["records"][3]["request"]
    if args.answer_task:
        dataset = json.loads(
            (ROOT / "data/evaluations/answers-v1/dataset.json").read_text(encoding="utf-8")
        )
        request = next(
            task["request"]
            for task in dataset["tasks"]
            if task["task_id"] == args.answer_task and task["split"] == "dev"
        )
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=120, trust_env=False) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        response.raise_for_status()
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}
        try:
            response = client.post(
                f"/api/retrieval/indexes/{frozen['index']['index_id']}/search",
                headers=auth,
                json=request,
            )
            response.raise_for_status()
            retrieved = response.json()
        finally:
            client.post("/api/auth/logout", headers=auth)
    data = {
        "query": request["query"],
        "product_version": request["product_version"],
        "contexts": [
            {
                "context_id": c["context_id"],
                "kind": c["kind"],
                "source_kind": "document",
                "source": c["source"],
                "anchor_evidence_ids": c["anchor_evidence_ids"],
                "text": c["text"],
            }
            for c in retrieved["contexts"]
        ],
    }
    report = {
        "retrieval_usage": retrieved["usage"],
        "contexts": data["contexts"],
        "usage": None,
        "unknown_usage_calls": 0,
    }
    try:
        with Provider(AnswerModelSettings()) as provider:
            draft, report["usage"] = chat_json(provider, GENERATE, data, AnswerDraft, 3500)
        report["draft"] = draft.model_dump()
        by_id = {c["context_id"]: c for c in retrieved["contexts"]}
        report["citation_diagnostics"] = [
            {
                "claim_id": claim.claim_id,
                **citation.model_dump(),
                "context_exists": citation.context_id in by_id,
                "anchor_exists": citation.evidence_id
                in by_id.get(citation.context_id, {}).get("anchor_evidence_ids", []),
                "exact_quote_exists": citation.quote
                in by_id.get(citation.context_id, {}).get("text", ""),
            }
            for claim in draft.claims
            for citation in claim.citations
        ]
        try:
            bind_citations(draft, retrieved["contexts"])
            report["binding_status"] = "passed"
        except ValueError as exc:
            report["binding_status"] = "failed"
            report["binding_reason"] = str(exc)
    except ModelFailure as exc:
        report["error_code"] = exc.code
        report["usage"] = getattr(exc, "usage", None)
        report["unknown_usage_calls"] = int(report["usage"] is None)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"contexts", "draft"}}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
