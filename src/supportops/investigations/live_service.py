"""登记与假设调查复用不可变结果表；不提供普通用户目标注册接口。"""

from datetime import datetime, timezone
from uuid import uuid4

import anyio
from sqlalchemy import func, select

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.investigations import graph, service
from supportops.investigations.hypothesis_contracts import LabRegistration, LiveScope, live_manifest
from supportops.investigations.live_models import LabRun
from supportops.investigations.live_sources import verify_snapshot
from supportops.investigations.models import Investigation
from supportops.memory import service as memory
from supportops.retrieval.service import get_index
from supportops.skills import publication_service as publications
from supportops.skills.catalog import Catalog
from supportops.tickets.service import read_ticket

WORKFLOW = "hypothesis-investigation.v1"


def register_lab_run(session, organization_id, payload):
    # 应用角色没有 INSERT 权限；只有显式准备脚本的管理员事务可登记。
    payload = LabRegistration.model_validate(payload)
    if not payload.started_at <= datetime.now(timezone.utc) < payload.expires_at:
        raise ServiceError(409, "LIVE_RUN_EXPIRED", "登记时间窗口无效。")
    snapshot = payload.model_dump(mode="json")
    record = LabRun(
        organization_id=organization_id,
        product_version=payload.product_version,
        snapshot=snapshot,
        sha256=digest(snapshot),
        expires_at=payload.expires_at,
    )
    session.add(record)
    session.flush()
    return record


def get_registration(session, principal, identity, *, fresh=True):
    record = session.scalar(
        select(LabRun).where(
            LabRun.id == identity, LabRun.organization_id == principal.organization_id
        )
    )
    if record is None:
        raise ServiceError(404, "LIVE_RUN_NOT_FOUND", "本次实验不存在或不属于当前组织。")
    if digest(record.snapshot) != record.sha256:
        raise ServiceError(409, "LIVE_REGISTRATION_INVALID", "实验登记摘要不一致。")
    payload = LabRegistration.model_validate(record.snapshot)
    if payload.product_version != record.product_version or payload.expires_at != record.expires_at:
        raise ServiceError(409, "LIVE_REGISTRATION_INVALID", "实验登记字段不一致。")
    if fresh and not payload.started_at <= datetime.now(timezone.utc) < payload.expires_at:
        raise ServiceError(409, "LIVE_RUN_EXPIRED", "本次实验登记已过期。")
    return record, payload


def registrations(session, principal, offset, limit):
    filters = (LabRun.organization_id == principal.organization_id, LabRun.expires_at > func.now())
    rows = session.scalars(
        select(LabRun)
        .where(*filters)
        .order_by(LabRun.created_at.desc(), LabRun.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = []
    for record in rows:
        _, payload = get_registration(session, principal, record.id)
        items.append(
            {
                "lab_run_id": str(record.id),
                "product_version": payload.product_version,
                "mode": payload.mode,
                "run_id": str(payload.run_id),
                "expires_at": payload.expires_at,
                "sha256": record.sha256,
                "source_type": "registered_local_lab",
            }
        )
    return {
        "items": items,
        "total": session.scalar(select(func.count()).select_from(LabRun).where(*filters)),
        "offset": offset,
        "limit": limit,
    }


def create(session, principal, ticket_id, payload, database_url):
    ticket = read_ticket(session, principal, ticket_id)
    index = get_index(session, principal, payload.index_id)
    registered = None
    binding = None
    if payload.lab_run_id is not None:
        registered, binding = get_registration(session, principal, payload.lab_run_id)
        if ticket.product_version is not None and binding.product_version != ticket.product_version:
            raise ServiceError(422, "INVESTIGATION_VERSION_MISMATCH", "实验与工单版本不符。")
    identity = uuid4()
    snapshot = {
        "ticket": ticket.model_dump(mode="json"),
        "request": payload.model_dump(mode="json"),
        "knowledge": {"index_id": str(index.id), "corpus_sha256": index.corpus_sha256},
        "lab_registration": binding.model_dump(mode="json") if binding else None,
        "registration_sha256": registered.sha256 if registered else None,
        "tools": live_manifest(),
        "workflow_version": WORKFLOW,
    }
    skills = None
    memories = None
    if payload.use_skills and ticket.product_version is not None and binding is not None:
        skills = Catalog().prepare(
            ticket.product_version, binding.mode, ticket.title + "\n" + ticket.description
        )
        snapshot["skills"] = skills
    if payload.use_published_skills and ticket.product_version is not None and binding is not None:
        skills = publications.prepare(session, principal, ticket_id, binding.mode, skills)
        snapshot["skills"] = skills
    if payload.use_memory and ticket.product_version is not None and binding is not None:
        memories = memory.recall(session, principal, ticket_id, binding.mode)
        snapshot["memory"] = memories
    if ticket.product_version is None or registered is None:
        output = graph.empty_result()
        reason = "version_required" if ticket.product_version is None else "lab_run_required"
        output.update(
            status="needs_clarification",
            stop_reason=reason,
            questions=[
                "请补充工单版本。"
                if ticket.product_version is None
                else "请选择新登记的本次实验运行。"
            ],
            duration_ms=0,
            tool_calls=0,
            model_calls=0,
        )
    else:
        scope = LiveScope(
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            session_id=principal.session_id,
            ticket_id=ticket_id,
            index_id=index.id,
            product_version=ticket.product_version,
            experiment_id=None,
            corpus_sha256=index.corpus_sha256,
            ticket_sha256=digest(ticket.model_dump(mode="json")),
            lab_run_id=registered.id,
            registration_sha256=registered.sha256,
            investigation_id=identity,
        )
        if memories is not None:
            output = anyio.run(
                graph.run, scope, payload, database_url, binding.mode, skills, memories
            )
        elif skills is None:
            output = anyio.run(graph.run, scope, payload, database_url, binding.mode)
        else:
            output = anyio.run(graph.run, scope, payload, database_url, binding.mode, skills)
    record = Investigation(
        id=identity,
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
    return service.view(record)


def listing(session, principal, ticket_id, offset, limit):
    read_ticket(session, principal, ticket_id)
    filters = (
        Investigation.organization_id == principal.organization_id,
        Investigation.ticket_id == ticket_id,
        Investigation.workflow_version == WORKFLOW,
    )
    rows = session.scalars(
        select(Investigation)
        .where(*filters)
        .order_by(Investigation.created_at.desc(), Investigation.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [service.view(r) for r in rows],
        "total": session.scalar(select(func.count()).select_from(Investigation).where(*filters)),
        "offset": offset,
        "limit": limit,
    }


def evidence_reference(session, principal, identity, evidence_id):
    body = service.read(session, principal, identity)
    if body["workflow_version"] != WORKFLOW:
        raise ServiceError(404, "LIVE_EVIDENCE_NOT_FOUND", "本次证据不存在。")
    for tool in body["tool_results"]:
        value = tool["result"]
        if digest(value) != tool["result_sha256"]:
            raise ServiceError(409, "LIVE_SNAPSHOT_INVALID", "工具结果摘要不一致。")
        if "snapshot" not in value:
            continue
        data = verify_snapshot(value)
        for item in value["evidence"]:
            if item["evidence_id"] == evidence_id:
                return {
                    "evidence": item,
                    "snapshot": value["snapshot"],
                    "source_data": data[item["source"]["ordinal"]],
                    "text_verified": True,
                    "source_type": item["source"]["source_type"],
                    "human_reviewed": False,
                    "current_incident_verified": False,
                }
    raise ServiceError(404, "LIVE_EVIDENCE_NOT_FOUND", "本次证据不存在或不属于该调查。")
