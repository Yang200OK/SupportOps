"""固定首批产品范围；来源声明与真实工具观测保持区分。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

Title = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
Description = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=10000)
]
ProductVersion = Literal["1.0", "1.1", "2.0"]


class TicketDraft(BaseModel):
    """只接受用户可以陈述的输入；组织、根因与审批字段由其它流程拥有。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    title: Title
    description: Description
    product: Literal["relaydesk"]
    product_version: ProductVersion | None = None
    environment: Literal["local_lab"]
    source_type: Literal["synthetic_case", "user_report"]


class IntakeResult(BaseModel):
    """校验结果没有工单 ID；通过校验不等于完成创建或调查。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["supportops.ticket-intake.v1"] = "supportops.ticket-intake.v1"
    draft: TicketDraft
    intake_status: Literal["ready_for_intake", "needs_clarification"]
    missing_fields: list[Literal["product_version"]]
    persisted: Literal[False] = False


class TicketView(TicketDraft):
    """正式工单身份只由服务端产生。"""

    ticket_id: UUID
    organization_id: UUID
    requester_id: UUID
    created_at: datetime
    intake_status: Literal["ready_for_intake", "needs_clarification"]
    missing_fields: list[Literal["product_version"]]
    persisted: Literal[True] = True


class TicketList(BaseModel):
    items: list[TicketView]
    total: int
    offset: int
    limit: int
