"""模型只有工具建议权，身份与范围字段不进入建议参数。"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops.lab.contracts import Strict, Version
from supportops.rag.contracts import AnswerDraft, Claim, Text
from supportops.retrieval.contracts import QueryText


class InvestigationRequest(Strict):
    index_id: UUID
    experiment_id: UUID | None = None
    max_tool_calls: int = Field(default=3, strict=True, ge=1, le=3)
    max_model_calls: int = Field(default=5, strict=True, ge=1, le=5)
    time_budget_ms: int = Field(default=180000, strict=True, ge=1000, le=180000)


class NoArguments(Strict):
    pass


class SearchArguments(Strict):
    query: QueryText


ARGUMENTS = {
    "get_ticket": NoArguments,
    "search_knowledge": SearchArguments,
    "read_observations": NoArguments,
}


class Decision(Strict):
    action: Literal["search_knowledge", "read_observations", "finish", "clarify"]
    arguments: dict
    reason: Text

    @model_validator(mode="after")
    def bounded_arguments(self):
        schema = ARGUMENTS.get(self.action, NoArguments)
        schema.model_validate(self.arguments)
        return self


class BaselineDraft(AnswerDraft):
    claims: list[Claim] = Field(max_length=6)

    @model_validator(mode="after")
    def no_dynamic_hypotheses(self):
        if any(c.kind == "hypothesis" for c in self.claims):
            raise ValueError("本轮只整理事实和待检查项。")
        return self


class Scope(Strict):
    organization_id: UUID
    user_id: UUID
    session_id: UUID
    ticket_id: UUID
    index_id: UUID
    product_version: Version
    experiment_id: UUID | None
    corpus_sha256: str
    ticket_sha256: str


def tool_manifest():
    descriptions = {
        "get_ticket": "读取本次绑定工单；用户陈述不是现场观测。",
        "search_knowledge": "在固定同版本文档和案例中以 BM25 检索，最多三个片段。",
        "read_observations": "读取已绑定历史实验的异常阶段；没有实验时返回空证据。",
    }
    return [
        {
            "name": name,
            "description": descriptions[name],
            "inputSchema": schema.model_json_schema(),
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }
        for name, schema in ARGUMENTS.items()
    ]
