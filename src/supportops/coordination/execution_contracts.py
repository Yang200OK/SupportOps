"""执行请求不接受角色、地址、组织或扩大配额。"""

from typing import Literal
from uuid import UUID

from pydantic import Field, create_model

from supportops.investigations.hypothesis_contracts import LiveScope
from supportops.investigations.selections import Selection, draft_schema, selection_schema
from supportops.lab.contracts import Strict
from supportops.rag.contracts import Text


class ExecutionRequest(Strict):
    request_id: UUID


class RoleScope(LiveScope):
    execution_id: UUID
    task_id: Literal["documents", "runtime"]
    lease: UUID
    operation_key: str


class ToolStep(Strict):
    tool: Literal[
        "search_knowledge",
        "read_runtime_state",
        "read_current_observations",
        "read_startup_diagnostic",
    ]
    arguments: dict


class ToolPlan(Strict):
    reason: Text
    steps: list[ToolStep] = Field(min_length=1, max_length=3)


def tool_plan_schema(package):
    step = create_model("RoleStep", __base__=ToolStep, tool=(Literal[tuple(package["tools"])], ...))
    return create_model(
        "RolePlan",
        __base__=ToolPlan,
        steps=(list[step], Field(min_length=1, max_length=package["budget"]["tool_calls"])),
    )


class Conflict(Strict):
    description: Text
    left: Selection
    right: Selection
    status: Literal["unresolved"]


def merge_schema(evidence):
    selector = selection_schema([e["evidence_id"] for e in evidence], "ConflictEvidence")
    conflict = create_model(
        "BoundConflict", __base__=Conflict, left=(selector, ...), right=(selector, ...)
    )
    return create_model(
        "CoordinationMerge",
        __base__=draft_schema(evidence),
        conflicts=(list[conflict], Field(max_length=4)),
    )
