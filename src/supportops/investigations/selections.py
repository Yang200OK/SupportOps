"""模型选择固定原文片段，应用复制原句后仍进行原有引用与语义核对。"""

from typing import Annotated, Literal, Union

from pydantic import Field, StringConstraints, create_model, model_validator

from supportops.investigations.hypothesis_contracts import (
    Hypothesis,
    InvestigationDraft,
    InvestigationPlan,
)
from supportops.investigations.hypothesis_validation import CURRENT, model_hypotheses
from supportops.lab.contracts import Strict
from supportops.rag.contracts import Text


class Selection(Strict):
    evidence_id: str
    quote_index: int = Field(strict=True, ge=0, le=63)


class SuggestedHypothesis(Hypothesis):
    support_citations: list[Selection] = Field(max_length=3)
    refute_citations: list[Selection] = Field(max_length=3)


class SuggestedPlan(InvestigationPlan):
    hypotheses: list[SuggestedHypothesis] = Field(min_length=1, max_length=3)


class SuggestedClaim(Strict):
    claim_id: Annotated[str, StringConstraints(strict=True, pattern=r"^C[1-6]$")]
    kind: Literal["fact", "check"]
    text: Text
    citations: list[Selection] = Field(min_length=1, max_length=3)


class SuggestedDraft(Strict):
    claims: list[SuggestedClaim] = Field(max_length=6)
    missing_information: list[Text] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct(self):
        if len({c.claim_id for c in self.claims}) != len(self.claims):
            raise ValueError("结论 ID 不能重复。")
        return self


def quote_catalog(evidence):
    catalog = {}
    for e in evidence:
        fragments = []
        for line in e["text"].splitlines():
            fragment = line.strip()
            if fragment and fragment not in ("{", "}", "[", "]"):
                # 只选择原文中的明确窗口，不补写、改格式或拼接不存在的原句。
                fragment = fragment[:1000]
                if fragment not in fragments:
                    fragments.append(fragment)
            if len(fragments) == 64:
                break
        catalog[e["evidence_id"]] = fragments
    return catalog


def selection_schema(ids, name):
    allowed = tuple(ids) or ("__no_evidence_available__",)
    return create_model(name, __base__=Selection, evidence_id=(Literal[allowed], ...))


def plan_schema(evidence, existing=()):
    selector = selection_schema(
        [e["evidence_id"] for e in evidence if e["source"].get("source_type") in CURRENT],
        "CurrentEvidenceSelection",
    )
    hypothesis = create_model(
        "SuggestedCurrentHypothesis",
        __base__=SuggestedHypothesis,
        support_citations=(list[selector], Field(max_length=3)),
        refute_citations=(list[selector], Field(max_length=3)),
    )
    if existing:
        # 已建立的原因候选由应用冻结，后续模型只能更新信号与判断。
        variants = tuple(
            create_model(
                "Fixed" + h["hypothesis_id"],
                __base__=hypothesis,
                hypothesis_id=(Literal[h["hypothesis_id"]], ...),
                cause=(Literal[h["cause"]], ...),
            )
            for h in existing
        )
        spare = tuple(
            f"H{i}" for i in range(1, 4) if f"H{i}" not in {h["hypothesis_id"] for h in existing}
        )
        if spare:
            variants += (
                create_model(
                    "NewCandidate", __base__=hypothesis, hypothesis_id=(Literal[spare], ...)
                ),
            )
        hypothesis = Union[variants] if len(variants) > 1 else variants[0]
    return create_model(
        "CurrentInvestigationPlan",
        __base__=SuggestedPlan,
        hypotheses=(list[hypothesis], Field(min_length=1, max_length=3)),
    )


def draft_schema(evidence):
    selector = selection_schema([e["evidence_id"] for e in evidence], "KnownEvidenceSelection")
    claim = create_model(
        "SelectedClaim",
        __base__=SuggestedClaim,
        citations=(list[selector], Field(min_length=1, max_length=3)),
    )
    return create_model(
        "SelectedDraft", __base__=SuggestedDraft, claims=(list[claim], Field(max_length=6))
    )


def citations(selections, catalog, code):
    refs = []
    for selection in selections:
        try:
            quote = catalog[selection["evidence_id"]][selection["quote_index"]]
        except (KeyError, IndexError):
            raise ValueError(code) from None
        refs.append(
            {
                "context_id": selection["evidence_id"],
                "evidence_id": selection["evidence_id"],
                "quote": quote,
            }
        )
    return refs


def bind_plan(choice, catalog, completed_steps=()):
    data = choice.model_dump()
    # 已执行内容由应用原样保留，模型只提交尚未执行的检查建议。
    data["steps"] = [*completed_steps, *data["steps"]]
    for h in data["hypotheses"]:
        for field in ("support_citations", "refute_citations"):
            h[field] = citations(h[field], catalog, "HYPOTHESIS_CITATION_INVALID")
    return InvestigationPlan.model_validate(data)


def bind_draft(choice, catalog):
    data = choice.model_dump()
    for claim in data["claims"]:
        claim["citations"] = citations(claim["citations"], catalog, "ANSWER_EVIDENCE_INVALID")
    return InvestigationDraft.model_validate(data)


def suggested_hypotheses(items, catalog):
    projected = model_hypotheses(items)
    for h in projected:
        for field in ("support_citations", "refute_citations"):
            h[field] = [
                {
                    "evidence_id": c["evidence_id"],
                    "quote_index": catalog[c["evidence_id"]].index(c["quote"]),
                }
                for c in h[field]
            ]
    return projected
