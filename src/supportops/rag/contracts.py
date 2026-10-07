"""模型仅生成有引用的结论；身份、预算与支持状态由应用核对。"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from supportops.lab.contracts import Strict
from supportops.retrieval.contracts import AdvancedSearch

Text = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)
]
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]


class AnswerRequest(AdvancedSearch):
    top_k: int = Field(default=5, strict=True, ge=1, le=8)
    context_budget_chars: int = Field(default=12000, strict=True, ge=128, le=12000)


class Citation(Strict):
    context_id: Identifier
    evidence_id: Identifier
    quote: Text


class Claim(Strict):
    claim_id: Annotated[
        str, StringConstraints(strict=True, pattern=r"^C[1-9][0-9]?$", max_length=3)
    ]
    kind: Literal["fact", "hypothesis", "check"]
    text: Text
    citations: list[Citation] = Field(min_length=1, max_length=3)


class AnswerDraft(Strict):
    claims: list[Claim] = Field(max_length=12)
    missing_information: list[Text] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct(self):
        ids = [c.claim_id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("结论 ID 不能重复。")
        for claim in self.claims:
            refs = [(c.context_id, c.evidence_id, c.quote) for c in claim.citations]
            if len(refs) != len(set(refs)):
                raise ValueError("同一结论不能重复引用。")
        return self


class Review(Strict):
    claim_id: Identifier
    verdict: Literal["supported", "unsupported", "insufficient"]
    reason: Text


class SupportReview(Strict):
    items: list[Review] = Field(max_length=12)

    @model_validator(mode="after")
    def distinct(self):
        ids = [r.claim_id for r in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("核对项不能重复。")
        return self
