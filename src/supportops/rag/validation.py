"""确定性核对原文字面，独立保存非确定性的模型语义判断。"""

from supportops.retrieval.contexts import text_hash


def bind_citations(draft, contexts):
    by_id = {c["context_id"]: c for c in contexts}
    if len(by_id) != len(contexts):
        raise ValueError("上下文身份重复。")
    claims = []
    for claim in draft.claims:
        row = claim.model_dump()
        for citation in row["citations"]:
            context = by_id.get(citation["context_id"])
            if (
                context is None
                or not context["text_verified"]
                or citation["evidence_id"] not in context["anchor_evidence_ids"]
            ):
                raise ValueError("引用不属于本次已核对上下文。")
            start = context["text"].find(citation["quote"])
            if start < 0:
                raise ValueError("引文不存在于原文。")
            citation.update(
                start=start,
                end=start + len(citation["quote"]),
                context_text_sha256=text_hash(context["text"]),
                literal_verified=True,
            )
        claims.append(row)
    return claims


def bind_reviews(claims, review):
    by_id = {r.claim_id: r for r in review.items}
    if set(by_id) != {c["claim_id"] for c in claims}:
        raise ValueError("语义核对没有准确覆盖本次结论。")
    return [
        {
            **c,
            "support": {
                **by_id[c["claim_id"]].model_dump(exclude={"claim_id"}),
                "method": "independent_model_review",
                "human_reviewed": False,
            },
        }
        for c in claims
    ]
