"""调查历史单独持久化，旧输入检查表与协议保留。"""

import anyio
from sqlalchemy import func, select

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.experiments.service import read_experiment
from supportops.investigations import runner
from supportops.investigations.contracts import Scope, tool_manifest
from supportops.investigations.models import Investigation
from supportops.retrieval.service import get_index
from supportops.tickets.service import read_ticket

WORKFLOW = "readonly-baseline.v1"


def view(record):
    if (
        digest(record.input_snapshot) != record.input_sha256
        or digest(record.output) != record.output_sha256
        or record.output["status"] != record.status
    ):
        raise ServiceError(409, "INVESTIGATION_INTEGRITY_FAILED", "调查记录摘要不一致。")
    return {
        "investigation_id": str(record.id),
        "ticket_id": str(record.ticket_id),
        "workflow_version": record.workflow_version,
        "created_at": record.created_at,
        "input_snapshot": record.input_snapshot,
        "input_sha256": record.input_sha256,
        "output_sha256": record.output_sha256,
        "persisted": True,
        **record.output,
    }


def create(session, principal, ticket_id, payload, database_url):
    ticket = read_ticket(session, principal, ticket_id)
    index = get_index(session, principal, payload.index_id)
    if payload.experiment_id is not None:
        experiment = read_experiment(session, principal, payload.experiment_id)
        if ticket.product_version and experiment.product_version != ticket.product_version:
            raise ServiceError(422, "INVESTIGATION_VERSION_MISMATCH", "实验版本与工单不一致。")
    snapshot = {
        "ticket": ticket.model_dump(mode="json"),
        "request": payload.model_dump(mode="json"),
        "knowledge": {"index_id": str(index.id), "corpus_sha256": index.corpus_sha256},
        "tools": tool_manifest(),
        "workflow_version": WORKFLOW,
    }
    if ticket.product_version is None:
        output = {
            "status": "needs_clarification",
            "stop_reason": "version_required",
            "report": None,
            "events": [],
            "tool_results": [],
            "evidence": [],
            "contexts": [],
            "mcp": None,
            "questions": ["请补充工单产品版本。"],
            "duration_ms": 0,
            "tool_calls": 0,
            "model_calls": 0,
            "usage": {
                "calls": [],
                "known_model_calls": 0,
                "unknown_model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_cny": None,
            },
            "current_incident_verified": False,
            "human_reviewed": False,
            "limitation": runner.LIMITATION,
        }
    else:
        scope = Scope(
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            session_id=principal.session_id,
            ticket_id=ticket_id,
            index_id=index.id,
            product_version=ticket.product_version,
            experiment_id=payload.experiment_id,
            corpus_sha256=index.corpus_sha256,
            ticket_sha256=digest(ticket.model_dump(mode="json")),
        )
        output = anyio.run(runner.run, scope, payload, database_url)
    record = Investigation(
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        ticket_id=ticket_id,
        workflow_version=WORKFLOW,
        status=output["status"],
        input_snapshot=snapshot,
        input_sha256=digest(snapshot),
        output=output,
        output_sha256=digest(output),
    )
    session.add(record)
    session.flush()
    return view(record)


def read(session, principal, identity):
    record = session.scalar(
        select(Investigation).where(
            Investigation.id == identity, Investigation.organization_id == principal.organization_id
        )
    )
    if record is None:
        raise ServiceError(404, "INVESTIGATION_NOT_FOUND", "调查记录不存在或不属于当前组织。")
    return view(record)


def listing(session, principal, ticket_id, offset, limit):
    read_ticket(session, principal, ticket_id)
    filters = (
        Investigation.organization_id == principal.organization_id,
        Investigation.ticket_id == ticket_id,
        Investigation.workflow_version == WORKFLOW,
    )
    records = session.scalars(
        select(Investigation)
        .where(*filters)
        .order_by(Investigation.created_at.desc(), Investigation.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [view(r) for r in records],
        "total": session.scalar(select(func.count()).select_from(Investigation).where(*filters)),
        "offset": offset,
        "limit": limit,
    }
