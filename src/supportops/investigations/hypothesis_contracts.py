"""只读假设调查的模型建议与服务器范围分开。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from supportops.investigations.contracts import NoArguments, Scope, SearchArguments
from supportops.lab.contracts import Observation, Strict, Version
from supportops.rag.contracts import AnswerDraft, Citation, Claim, Text

HypothesisID = Annotated[str, StringConstraints(strict=True, pattern=r"^H[1-3]$")]
StepID = Annotated[str, StringConstraints(strict=True, pattern=r"^S[1-6]$")]


class HypothesisRequest(Strict):
    index_id: UUID
    lab_run_id: UUID | None = None
    use_skills: bool = Field(default=False, strict=True)
    use_memory: bool = Field(default=False, strict=True)
    use_published_skills: bool = Field(default=False, strict=True)
    max_tool_calls: int = Field(default=6, strict=True, ge=1, le=6)
    max_model_calls: int = Field(default=8, strict=True, ge=1, le=8)
    time_budget_ms: int = Field(default=240000, strict=True, ge=1000, le=240000)


LIVE_ARGUMENTS = {
    "get_ticket": NoArguments,
    "search_knowledge": SearchArguments,
    "read_runtime_state": NoArguments,
    "read_current_observations": NoArguments,
    "read_startup_diagnostic": NoArguments,
}


class Hypothesis(Strict):
    hypothesis_id: HypothesisID
    cause: Text
    basis: Text
    support_signal: Text
    refute_signal: Text
    missing_information: list[Text] = Field(max_length=4)
    status: Literal["proposed", "supported", "refuted", "unresolved"]
    reason: Text
    support_citations: list[Citation] = Field(max_length=3)
    refute_citations: list[Citation] = Field(max_length=3)

    @model_validator(mode="after")
    def status_needs_evidence(self):
        if self.status == "supported" and (not self.support_citations or self.refute_citations):
            raise ValueError("支持状态需要支持证据，冲突必须未决。")
        if self.status == "refuted" and (not self.refute_citations or self.support_citations):
            raise ValueError("反驳状态需要反驳证据，冲突必须未决。")
        if self.status == "proposed" and (self.support_citations or self.refute_citations):
            raise ValueError("已有依据后应明确支持、反驳或未决。")
        return self


class PlanStep(Strict):
    step_id: StepID
    hypothesis_ids: list[HypothesisID] = Field(min_length=1, max_length=3)
    reason: Text
    tool: Literal[
        "search_knowledge",
        "read_runtime_state",
        "read_current_observations",
        "read_startup_diagnostic",
    ]
    arguments: dict

    @model_validator(mode="after")
    def strict_tool_arguments(self):
        LIVE_ARGUMENTS[self.tool].model_validate(self.arguments)
        if len(set(self.hypothesis_ids)) != len(self.hypothesis_ids):
            raise ValueError("步骤的假设引用不能重复。")
        return self


class InvestigationPlan(Strict):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    steps: list[PlanStep] = Field(max_length=6)
    action: Literal["execute", "finish", "clarify"]
    next_step_id: StepID | None
    reason: Text

    @model_validator(mode="after")
    def identities_and_selection(self):
        ids = {h.hypothesis_id for h in self.hypotheses}
        step_ids = {s.step_id for s in self.steps}
        if len(ids) != len(self.hypotheses) or len(step_ids) != len(self.steps):
            raise ValueError("假设 / 步骤身份不能重复。")
        if any(not set(s.hypothesis_ids) <= ids for s in self.steps):
            raise ValueError("步骤引用不存在的假设。")
        if self.action == "execute" and self.next_step_id not in step_ids:
            raise ValueError("必须选择真实计划中的下一步骤。")
        if self.action != "execute" and self.next_step_id is not None:
            raise ValueError("停止 / 澄清不能执行步骤。")
        return self


class InvestigationDraft(AnswerDraft):
    claims: list[Claim] = Field(max_length=6)

    @model_validator(mode="after")
    def facts_before_bound_hypotheses(self):
        if any(
            c.kind == "hypothesis" or c.claim_id not in {f"C{i}" for i in range(1, 7)}
            for c in self.claims
        ):
            raise ValueError("原因候选由应用单独绑定，事实 ID 限定 C1..C6。")
        return self


class BootDiagnostic(Strict):
    exit_code: Literal[2]
    observation: Observation

    @model_validator(mode="after")
    def actual_startup_failure(self):
        if (
            self.observation.service != "boot"
            or self.observation.event != "config_checked"
            or self.observation.phase != "failure"
        ):
            raise ValueError("必须是明确的启动诊断。")
        return self


class LabRegistration(Strict):
    run_id: UUID
    instance_id: UUID
    receiver_instance_id: UUID | None
    product_version: Version
    request_ids: list[UUID] = Field(min_length=1, max_length=4)
    started_at: datetime
    expires_at: datetime
    mode: Literal["online", "startup"]
    boot: BootDiagnostic | None = None

    @model_validator(mode="after")
    def bounded_window_and_mode(self):
        if self.started_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("时间窗口必须包含时区。")
        if not 0 < (self.expires_at - self.started_at).total_seconds() <= 900:
            raise ValueError("登记窗口必须在 15 分钟内。")
        if len(set(self.request_ids)) != len(self.request_ids):
            raise ValueError("请求身份不能重复。")
        if self.mode == "startup":
            if self.boot is None or self.receiver_instance_id is not None:
                raise ValueError("启动诊断必须显式提供，不绑定在线接收器。")
            observation = self.boot.observation
            if (
                observation.product_version != self.product_version
                or observation.request_id not in self.request_ids
                or not self.started_at <= observation.observed_at <= self.expires_at
            ):
                raise ValueError("启动诊断不属于登记范围。")
        elif self.boot is not None or self.receiver_instance_id is None:
            raise ValueError("在线运行必须明确绑定两个服务实例。")
        return self


class LiveScope(Scope):
    lab_run_id: UUID
    registration_sha256: str
    investigation_id: UUID


def live_manifest():
    descriptions = {
        "get_ticket": "读取固定工单陈述，陈述不是实测事实。",
        "search_knowledge": "固定同版本文档 / 案例 BM25，最多三段；资料不能证明本次根因。",
        "read_runtime_state": "固定登记实例的当前配置与连接池占用、接收器状态；仅在线运行。",
        "read_current_observations": "读取本次固定运行 / 请求 / 时间窗口内的异常观测；仅在线运行。",
        "read_startup_diagnostic": (
            "读取明确登记的本次启动诊断与退出码；没有在线服务，不能代替在线状态。"
        ),
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
        for name, schema in LIVE_ARGUMENTS.items()
    ]
