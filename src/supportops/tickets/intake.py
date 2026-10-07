"""纯函数完成入库前检查，不访问数据库、模型或实验环境。"""

from supportops.tickets.contracts import IntakeResult, TicketDraft


def validate_intake(draft: TicketDraft) -> IntakeResult:
    # 未知版本是待澄清信息，不从描述、历史工单或默认版本推断。
    if draft.product_version is None:
        return IntakeResult(
            draft=draft,
            intake_status="needs_clarification",
            missing_fields=["product_version"],
        )
    return IntakeResult(draft=draft, intake_status="ready_for_intake", missing_fields=[])
