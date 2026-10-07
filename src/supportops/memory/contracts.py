"""请求只指定已有来源和治理决策，不接受模型权限或路径。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from supportops.lab.contracts import Strict

Reason = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=400)
]


class RetrospectiveRequest(Strict):
    request_id: UUID
    action_id: UUID | None = None
    expires_in_days: int = Field(default=7, strict=True, ge=1, le=30)


class Decision(Strict):
    revision: int = Field(strict=True, ge=1)
    decision: Literal["invalidate", "revoke"]
    reason: Reason


class ConflictRequest(Strict):
    other_id: UUID
    revision: int = Field(strict=True, ge=1)
    other_revision: int = Field(strict=True, ge=1)
    reason: Reason
