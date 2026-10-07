"""本轮运行不产生根因、模型使用量或批准动作。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["intake_check"] = "intake_check"


class RunView(BaseModel):
    run_id: UUID
    ticket_id: UUID
    organization_id: UUID
    requester_id: UUID
    kind: Literal["intake_check"]
    status: Literal["succeeded", "blocked"]
    workflow_version: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    input_snapshot: dict
    input_sha256: str
    output: dict
    events: list[dict]
    model: None = None
    usage: None = None
    cost_cny: None = None


class RunList(BaseModel):
    items: list[RunView]
    total: int
    offset: int
    limit: int
