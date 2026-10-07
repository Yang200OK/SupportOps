"""原句、当前来源和状态转换由代码核对，语义仍需独立模型评审。"""

from supportops.investigations.hypothesis_contracts import Hypothesis
from supportops.rag.contracts import AnswerDraft, Citation, Claim
from supportops.rag.validation import bind_citations
from supportops.retrieval.contexts import text_hash

CURRENT = {"current_lab_observations", "current_lab_state", "current_startup_diagnostic"}


def model_hypotheses(items):
    # 只清理模型输入投影；模型输出仍严格拒绝应用拥有的核对字段。
    projected = []
    for item in items:
        row = {k: item[k] for k in Hypothesis.model_fields}
        for field in ("support_citations", "refute_citations"):
            row[field] = [{k: c[k] for k in Citation.model_fields} for c in row[field]]
        projected.append(row)
    return projected


def contexts_for(evidence):
    return [
        {
            "context_id": e["evidence_id"],
            "anchor_evidence_ids": [e["evidence_id"]],
            "text": e["text"],
            "text_sha256": text_hash(e["text"]),
            "text_verified": e["text_verified"],
            "kind": e["kind"],
            "source": e["source"],
        }
        for e in evidence
    ]


def validate_hypotheses(items, evidence):
    contexts = contexts_for(evidence)
    by_id = {e["evidence_id"]: e for e in evidence}
    rows = []
    for item in items:
        row = item.model_dump()
        for field in ("support_citations", "refute_citations"):
            refs = getattr(item, field)
            if not refs:
                continue
            try:
                draft = AnswerDraft(
                    claims=[
                        Claim(claim_id="C1", kind="hypothesis", text=item.reason, citations=refs)
                    ],
                    missing_information=["尚未人工复核。"],
                )
                bound = bind_citations(draft, contexts)[0]["citations"]
            except ValueError:
                raise ValueError("HYPOTHESIS_CITATION_INVALID") from None
            if not any(
                by_id[c["evidence_id"]]["source"].get("source_type") in CURRENT for c in bound
            ):
                raise ValueError("HYPOTHESIS_CURRENT_EVIDENCE_REQUIRED")
            row[field] = bound
        row["semantic_reviewed"] = False
        row["human_reviewed"] = False
        rows.append(row)
    return rows
