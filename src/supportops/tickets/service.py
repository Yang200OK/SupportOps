"""工单写入复用既有输入检查，所有读取都有服务器组织范围。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.db.models import Ticket
from supportops.tickets.contracts import TicketDraft, TicketList, TicketView
from supportops.tickets.intake import validate_intake


def ticket_view(row: Ticket) -> TicketView:
    values = {name: getattr(row, name) for name in TicketDraft.model_fields}
    return TicketView(
        **values,
        ticket_id=row.id,
        organization_id=row.organization_id,
        requester_id=row.requester_id,
        created_at=row.created_at,
        intake_status=row.intake_status,
        missing_fields=["product_version"] if row.product_version is None else [],
    )


def create_ticket(session: Session, principal: Principal, draft: TicketDraft) -> TicketView:
    checked = validate_intake(draft)
    record = Ticket(
        **draft.model_dump(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        intake_status=checked.intake_status,
    )
    session.add(record)
    session.flush()
    return ticket_view(record)


def read_ticket(session: Session, principal: Principal, ticket_id: UUID) -> TicketView:
    record = session.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.organization_id == principal.organization_id,
        )
    )
    if record is None:
        raise ServiceError(404, "TICKET_NOT_FOUND", "工单不存在或不属于当前组织。")
    return ticket_view(record)


def list_tickets(session: Session, principal: Principal, offset: int, limit: int) -> TicketList:
    scope = Ticket.organization_id == principal.organization_id
    total = session.scalar(select(func.count()).select_from(Ticket).where(scope))
    records = session.scalars(
        select(Ticket)
        .where(scope)
        .order_by(
            Ticket.created_at.desc(),
            Ticket.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return TicketList(
        items=[ticket_view(record) for record in records], total=total, offset=offset, limit=limit
    )
