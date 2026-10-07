"""范围来自会话和显式来源，标签不能进入检索请求。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from supportops.lab.contracts import Strict, Version

Kind = Literal["document", "case", "log"]
QueryText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)
]


class BuildIndex(Strict):
    chunk_set_ids: list[UUID] = Field(default_factory=list, max_length=32)
    experiment_ids: list[UUID] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def sources(self):
        if not self.chunk_set_ids and not self.experiment_ids:
            raise ValueError("必须显式选择来源。")
        for ids in (self.chunk_set_ids, self.experiment_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("来源不能重复。")
        return self


class Search(Strict):
    query: QueryText
    product_version: Version
    source_kinds: list[Kind] = Field(
        default_factory=lambda: ["document", "case", "log"], min_length=1, max_length=3
    )
    document_ids: list[UUID] = Field(default_factory=list, max_length=32)
    experiment_ids: list[UUID] = Field(default_factory=list, max_length=24)
    top_k: int = Field(default=5, strict=True, ge=1, le=20)

    @model_validator(mode="after")
    def unique_scope(self):
        for ids in (self.source_kinds, self.document_ids, self.experiment_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("查询范围不能重复。")
        return self


class HybridSearch(Search):
    mode: Literal["vector", "bm25", "rrf"] = "vector"
    candidate_limit: int = Field(default=20, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def bounded_candidates(self):
        if self.top_k > self.candidate_limit:
            raise ValueError("返回数不能超过单路候选上限。")
        return self


class AdvancedSearch(HybridSearch):
    rerank: bool = Field(default=False, strict=True)
    expand_parent: bool = Field(default=False, strict=True)
    rerank_pool: int = Field(default=20, strict=True, ge=1, le=40)
    parent_max_chars: int = Field(default=3000, strict=True, ge=128, le=12000)
    context_budget_chars: int = Field(default=12000, strict=True, ge=128, le=24000)

    @model_validator(mode="after")
    def pool_bounds(self):
        if self.top_k > self.rerank_pool:
            raise ValueError("返回数不能超过重排序池。")
        return self
