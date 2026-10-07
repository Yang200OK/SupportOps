"""模型建议与应用拥有的实验参数分开。"""

from typing import Literal
from uuid import UUID

from pydantic import Field, create_model, model_validator

from supportops.lab.contracts import Strict, Version

Action = Literal["release_pool", "clear_cache", "set_timeout", "restart_product"]


class ActionChoice(Strict):
    action: Action
    timeout_ms: int | None = Field(ge=500, le=2000)
    reason: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def matching_arguments(self):
        if (self.action == "set_timeout") != (self.timeout_ms is not None):
            raise ValueError("只有修改超时动作允许且必须指定毫秒参数。")
        return self


class ProposalRequest(Strict):
    request_id: UUID


def choice_schema(evidence_ids):
    return create_model(
        "BoundActionChoice",
        __base__=ActionChoice,
        evidence_ids=(list[Literal[tuple(evidence_ids)]], Field(min_length=1, max_length=3)),
    )


class Decision(Strict):
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]


class LabCommand(Strict):
    action_id: UUID
    operation: Action | Literal["retest"]
    instance_id: UUID
    receiver_instance_id: UUID
    state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    product_version: Version
    timeout_ms: int | None = Field(default=None, ge=500, le=2000)
    run_id: UUID
    request_id: UUID

    @model_validator(mode="after")
    def matching_arguments(self):
        if (self.operation == "set_timeout") != (self.timeout_ms is not None):
            raise ValueError("动作参数与操作不匹配。")
        return self
