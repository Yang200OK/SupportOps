"""请求不允许上传方法正文、执行代码或扩大权限。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from supportops.lab.contracts import Strict
from supportops.memory.contracts import Reason
from supportops.skills.catalog import Digest


class DraftRequest(Strict):
    request_id: UUID
    candidate_id: UUID
    candidate_revision: int = Field(strict=True, ge=1)


class ReviewRequest(Strict):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)
    decision: Literal["approve", "reject"]
    report_id: UUID
    payload_sha256: Digest
    reason: Reason


class TransitionRequest(Strict):
    revision: int = Field(strict=True, ge=1)
    reason: Reason
