"""模型只能建议有界检索或提问，不能修改可信过滤条件。"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from supportops.lab.contracts import Strict, Version
from supportops.rag.contracts import Citation, Text
from supportops.retrieval.contracts import AdvancedSearch, QueryText


class GuidedRequest(AdvancedSearch):
    product_version: Version | None = None
    top_k: int = Field(default=5, strict=True, ge=1, le=5)
    context_budget_chars: int = Field(default=12000, strict=True, ge=128, le=12000)
    clarification: (
        Annotated[
            str,
            StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000),
        ]
        | None
    ) = None
    max_searches: int = Field(default=2, strict=True, ge=1, le=2)
    max_model_calls: int = Field(default=9, strict=True, ge=3, le=9)
    time_budget_ms: int = Field(default=180000, strict=True, ge=1000, le=240000)


class Rewrite(Strict):
    action: Literal["retrieve", "clarify"]
    query: QueryText | None
    questions: list[Text] = Field(max_length=3)
    reason: Text

    @model_validator(mode="after")
    def consistent(self):
        if self.action == "retrieve" and (self.query is None or self.questions):
            raise ValueError("检索计划需要问题，不能同时提出澄清。")
        if self.action == "clarify" and (self.query is not None or not self.questions):
            raise ValueError("澄清计划需要具体问题，不能同时检索。")
        return self


class Conflict(Strict):
    topic: Text
    reason: Text
    citations: list[Citation] = Field(min_length=2, max_length=3)


class Decision(Strict):
    action: Literal["answer", "search_more", "clarify", "conflict", "stop"]
    next_query: QueryText | None
    questions: list[Text] = Field(max_length=3)
    conflicts: list[Conflict] = Field(max_length=3)
    reason: Text

    @model_validator(mode="after")
    def consistent(self):
        if (self.action == "search_more") != (self.next_query is not None):
            raise ValueError("只有追加检索可以包含下一条问题。")
        if (self.action == "clarify") != bool(self.questions):
            raise ValueError("只有澄清需要具体提问。")
        if (self.action == "conflict") != bool(self.conflicts):
            raise ValueError("只有冲突决策可以包含冲突引用。")
        return self
