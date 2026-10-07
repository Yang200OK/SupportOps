"""客户端只选择固定来源和缩小预算，任务权限由应用生成。"""

from uuid import UUID

from pydantic import Field

from supportops.lab.contracts import Strict
from supportops.rag.contracts import Text


class BoardRequest(Strict):
    request_id: UUID
    index_id: UUID
    lab_run_id: UUID
    max_tool_calls: int = Field(default=6, strict=True, ge=2, le=6)
    max_model_calls: int = Field(default=8, strict=True, ge=3, le=8)
    time_budget_ms: int = Field(default=240000, strict=True, ge=1000, le=240000)
    context_chars: int = Field(default=16000, strict=True, ge=300, le=16000)


class CancelRequest(Strict):
    revision: int = Field(strict=True, ge=1)
    reason: Text
