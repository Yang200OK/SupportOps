"""规范化格式先于评测执行，失败样本不能从分母消失。"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from supportops.tickets.contracts import TicketDraft

Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=120)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class Expected(Contract):
    outcome: Literal["needs_clarification", "needs_evidence", "supported_answer"]
    evidence_ids: list[Identifier]


class EvaluationTask(Contract):
    task_id: Identifier
    source_group: Identifier
    split: Literal["dev", "holdout"]
    input: TicketDraft
    expected: Expected

    def model_input(self) -> dict:
        # 标注、数据分区和家族标签永远不进入模型任务输入。
        return {"task_id": self.task_id, "input": self.input.model_dump(mode="json")}


class EvaluationDataset(Contract):
    schema_version: Literal["supportops.evaluation-dataset.v1"]
    dataset_id: Identifier
    tasks: list[EvaluationTask] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_and_partitioned(self):
        identifiers = [item.task_id for item in self.tasks]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("任务 ID 必须唯一。")
        groups = {}
        for item in self.tasks:
            if item.source_group in groups and groups[item.source_group] != item.split:
                raise ValueError("相同来源家族不能跨开发集与保留集。")
            groups[item.source_group] = item.split
        return self

    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Attempt(Contract):
    task_id: Identifier
    status: Literal["completed", "failed", "not_run"]
    latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    cost_cny: float | None = Field(default=None, ge=0)
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def truthful_measurements(self):
        if self.status == "failed" and not self.error_code:
            raise ValueError("失败必须记录错误代码。")
        if self.status == "completed" and (self.error_code is not None or self.latency_ms is None):
            raise ValueError("完成必须记录耗时且没有错误代码。")
        if self.status == "not_run" and any(
            value is not None
            for value in (
                self.latency_ms,
                self.input_tokens,
                self.output_tokens,
                self.cost_cny,
                self.error_code,
            )
        ):
            raise ValueError("未执行的任务不能填写测量结果。")
        return self


class EvaluationReport(Contract):
    schema_version: Literal["supportops.evaluation-report.v1"]
    dataset_sha256: Digest
    model: Identifier
    parameters: dict
    knowledge_sha256: Digest | None
    task_ids: list[Identifier] = Field(min_length=1, max_length=500)
    attempts: list[Attempt]

    @model_validator(mode="after")
    def complete_denominator(self):
        actual = [item.task_id for item in self.attempts]
        if (
            len(set(self.task_ids)) != len(self.task_ids)
            or len(set(actual)) != len(actual)
            or set(actual) != set(self.task_ids)
        ):
            raise ValueError("必须逐项记录全部任务，包括失败和未执行。")
        return self

    def summary(self) -> dict:
        return {
            "total": len(self.task_ids),
            **{
                status: sum(item.status == status for item in self.attempts)
                for status in ("completed", "failed", "not_run")
            },
        }
