"""身份派生、输入快照与事件在同一事务中保存。"""

import hashlib
import json
from time import perf_counter_ns
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.db.models import Run
from supportops.runs.contracts import RunList, RunView
from supportops.tickets.contracts import TicketDraft
from supportops.tickets.intake import validate_intake
from supportops.tickets.service import read_ticket

WORKFLOW_VERSION = "intake-check.v1"


def view(record: Run) -> RunView:
    return RunView(
        run_id=record.id,
        **{
            field: getattr(record, field)
            for field in (
                "ticket_id",
                "organization_id",
                "requester_id",
                "kind",
                "status",
                "workflow_version",
                "started_at",
                "finished_at",
                "duration_ms",
                "input_snapshot",
                "input_sha256",
                "output",
                "events",
            )
        },
    )


def create_run(session: Session, principal: Principal, ticket_id: UUID) -> RunView:
    timer = perf_counter_ns()
    ticket = read_ticket(session, principal, ticket_id)
    started = session.scalar(select(func.clock_timestamp()))
    draft = TicketDraft.model_validate(ticket.model_dump(include=set(TicketDraft.model_fields)))
    result = validate_intake(draft)
    snapshot = {
        "ticket": ticket.model_dump(mode="json"),
        "workflow_version": WORKFLOW_VERSION,
        "parameters": {},
        "knowledge_snapshot": None,
        "model": None,
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    duration = max(0, (perf_counter_ns() - timer) // 1_000_000)
    status = "blocked" if result.missing_fields else "succeeded"
    record = Run(
        ticket_id=ticket_id,
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        kind="intake_check",
        status=status,
        workflow_version=WORKFLOW_VERSION,
        started_at=started,
        finished_at=session.scalar(select(func.clock_timestamp())),
        duration_ms=duration,
        input_snapshot=snapshot,
        input_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        output={
            "intake_status": result.intake_status,
            "missing_fields": result.missing_fields,
            "message": "请补充产品版本后再进行调查。"
            if result.missing_fields
            else "输入检查通过，可进入后续调查。",
        },
        events=[
            {"sequence": 1, "event": "run_created", "elapsed_ms": 0},
            {"sequence": 2, "event": "input_checked", "elapsed_ms": duration},
            {"sequence": 3, "event": "run_finished", "elapsed_ms": duration, "status": status},
        ],
    )
    # flush 后才能返回数据库真实产生的运行身份；外层请求事务负责提交。
    session.add(record)
    session.flush()
    return view(record)


def read_run(session: Session, principal: Principal, run_id: UUID) -> RunView:
    record = session.scalar(
        select(Run).where(Run.id == run_id, Run.organization_id == principal.organization_id)
    )
    if record is None:
        raise ServiceError(404, "RUN_NOT_FOUND", "运行记录不存在或不属于当前组织。")
    return view(record)


def list_runs(
    session: Session, principal: Principal, offset: int, limit: int, ticket_id: UUID | None
) -> RunList:
    filters = [Run.organization_id == principal.organization_id]
    if ticket_id is not None:
        read_ticket(session, principal, ticket_id)
        filters.append(Run.ticket_id == ticket_id)
    total = session.scalar(select(func.count()).select_from(Run).where(*filters))
    records = session.scalars(
        select(Run)
        .where(*filters)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return RunList(
        items=[view(record) for record in records], total=total, offset=offset, limit=limit
    )
