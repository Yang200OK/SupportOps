"""来源冻结、幂等创建、追加治理和受控召回。"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select, text

from supportops.actions import service as actions
from supportops.actions.models import ActionApproval
from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.investigations import service as investigations
from supportops.investigations.models import Investigation
from supportops.memory.core import LIMITATION, summarize
from supportops.memory.models import Experience, MemoryConflict, MemoryEvent, Retrospective
from supportops.retrieval.lexical import bm25
from supportops.tickets.service import read_ticket


def now():
    return datetime.now(timezone.utc)


def fail(code, message, status=409):
    return ServiceError(status, code, message)


def source_for(session, principal, identity, action_id=None):
    investigations.read(session, principal, identity)
    row = session.scalar(
        select(Investigation).where(
            Investigation.id == identity, Investigation.organization_id == principal.organization_id
        )
    )
    result = {
        "investigation": {
            "investigation_id": str(row.id),
            "ticket_id": str(row.ticket_id),
            "workflow_version": row.workflow_version,
            "status": row.status,
            "input_snapshot": row.input_snapshot,
            "input_sha256": row.input_sha256,
            "output": row.output,
            "output_sha256": row.output_sha256,
        },
        "action": None,
    }
    if action_id is not None:
        job = actions.get(session, principal, action_id)
        if job.investigation_id != identity or job.status not in actions.TERMINAL:
            raise fail("MEMORY_ACTION_INVALID", "只能绑定同调查的终态动作。")
        action = actions.view(session, job)
        action["events"] = actions.events(session, principal, job.id, 0)[0]
        approval = session.get(ActionApproval, job.id)
        if approval:
            action["approval"].update(
                proposal_sha256=approval.proposal_sha256, session_id=str(approval.session_id)
            )
        result["action"] = action
    return jsonable_encoder(result)


def checked_summary(source):
    try:
        return summarize(source)
    except ValueError:
        raise fail("MEMORY_SOURCE_INVALID", "原始记录、事件或引文核对失败。") from None


def get_retrospective(session, principal, identity):
    row = session.scalar(
        select(Retrospective).where(
            Retrospective.id == identity, Retrospective.organization_id == principal.organization_id
        )
    )
    if row is None:
        raise fail("MEMORY_NOT_FOUND", "复盘不存在或不属于当前组织。", 404)
    if digest(row.source) != row.source_sha256 or digest(row.summary) != row.summary_sha256:
        raise fail("MEMORY_INTEGRITY_FAILED", "复盘摘要不一致。")
    summary, _ = checked_summary(row.source)
    if summary != row.summary:
        raise fail("MEMORY_INTEGRITY_FAILED", "复盘与冻结原始记录不一致。")
    return row


def audit(session, row):
    events = session.scalars(
        select(MemoryEvent)
        .where(
            MemoryEvent.candidate_id == row.id, MemoryEvent.organization_id == row.organization_id
        )
        .order_by(MemoryEvent.revision)
    ).all()
    if (
        [e.revision for e in events] != list(range(1, row.revision + 1))
        or any(digest(e.data) != e.sha256 or e.data["revision"] != e.revision for e in events)
        or events[-1].data["status"] != row.status
    ):
        raise fail("MEMORY_INTEGRITY_FAILED", "候选治理事件不完整或摘要不一致。")
    return [e.data for e in events]


def get_candidate(session, principal, identity, lock=False):
    query = select(Experience).where(
        Experience.id == identity, Experience.organization_id == principal.organization_id
    )
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise fail("MEMORY_NOT_FOUND", "候选不存在或不属于当前组织。", 404)
    if digest(row.body) != row.body_sha256 or any(
        row.body[k] != getattr(row, k) for k in ("product_version", "environment", "mode")
    ):
        raise fail("MEMORY_INTEGRITY_FAILED", "候选正文摘要或适用范围不一致。")
    audit(session, row)
    return row


def conflicts(session, row):
    records = session.scalars(
        select(MemoryConflict)
        .where(
            MemoryConflict.organization_id == row.organization_id,
            or_(MemoryConflict.left_id == row.id, MemoryConflict.right_id == row.id),
        )
        .order_by(MemoryConflict.left_id, MemoryConflict.right_id)
    ).all()
    items = []
    for conflict in records:
        if digest(conflict.data) != conflict.sha256:
            raise fail("MEMORY_INTEGRITY_FAILED", "冲突记录摘要不一致。")
        partner = session.get(
            Experience, conflict.right_id if conflict.left_id == row.id else conflict.left_id
        )
        if partner is None:
            raise fail("MEMORY_INTEGRITY_FAILED", "冲突另一方缺失。")
        items.append(
            {
                **conflict.data,
                "other_id": str(partner.id),
                "blocking": partner.status == "candidate" and partner.expires_at > now(),
            }
        )
    return items


def eligible(session, row):
    if row.status != "candidate":
        return row.status
    if row.expires_at <= now():
        return "expired"
    if any(c["blocking"] for c in conflicts(session, row)):
        return "conflicted"
    return "eligible_unreviewed"


def candidate_view(session, row):
    return {
        "candidate_id": str(row.id),
        "retrospective_id": str(row.retrospective_id),
        "body": row.body,
        "body_sha256": row.body_sha256,
        "status": row.status,
        "revision": row.revision,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "eligibility": eligible(session, row),
        "events": audit(session, row),
        "conflicts": conflicts(session, row),
    }


def view(session, principal, row, include_source=False):
    candidate = session.scalar(
        select(Experience).where(
            Experience.retrospective_id == row.id,
            Experience.organization_id == principal.organization_id,
        )
    )
    result = {
        "retrospective_id": str(row.id),
        "investigation_id": str(row.investigation_id),
        "ticket_id": str(row.ticket_id),
        "source_sha256": row.source_sha256,
        "summary": row.summary,
        "summary_sha256": row.summary_sha256,
        "created_at": row.created_at,
        "model_calls": 0,
        "candidate": candidate_view(session, get_candidate(session, principal, candidate.id))
        if candidate
        else None,
    }
    if include_source:
        result["source"] = row.source
    return result


def event(session, row, principal, kind, reason, **data):
    payload = {
        "revision": row.revision,
        "event": kind,
        "status": row.status,
        "reason": reason,
        "user_id": str(principal.user_id),
        "at": now().isoformat(),
        **data,
    }
    session.add(
        MemoryEvent(
            candidate_id=row.id,
            revision=row.revision,
            organization_id=row.organization_id,
            data=payload,
            sha256=digest(payload),
        )
    )
    session.flush()


def create(session, principal, identity, request):
    lock_key = int(digest([str(principal.user_id), str(request.request_id), "memory"])[:15], 16)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    old = session.scalar(
        select(Retrospective).where(
            Retrospective.organization_id == principal.organization_id,
            Retrospective.requester_id == principal.user_id,
            Retrospective.request_id == request.request_id,
        )
    )
    request_body = request.model_dump(mode="json")
    if old:
        if old.investigation_id != identity or old.request != request_body:
            raise fail("MEMORY_KEY_CONFLICT", "同一请求键不能改变来源或期限。")
        return view(session, principal, get_retrospective(session, principal, old.id))
    source = source_for(session, principal, identity, request.action_id)
    if len(json.dumps(source, ensure_ascii=False)) > 300000:
        raise fail("MEMORY_SOURCE_LIMIT", "原始记录超过本轮复盘上限。")
    summary, candidate_body = checked_summary(source)
    created = now()
    row = Retrospective(
        id=uuid4(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        ticket_id=UUID(source["investigation"]["ticket_id"]),
        investigation_id=identity,
        request_id=request.request_id,
        request=request_body,
        source=source,
        source_sha256=digest(source),
        summary=summary,
        summary_sha256=digest(summary),
        created_at=created,
    )
    session.add(row)
    session.flush()
    if candidate_body is not None:
        candidate = Experience(
            id=uuid4(),
            organization_id=principal.organization_id,
            retrospective_id=row.id,
            body=candidate_body,
            body_sha256=digest(candidate_body),
            status="candidate",
            revision=1,
            expires_at=created + timedelta(days=request.expires_in_days),
            created_at=created,
            **{k: candidate_body[k] for k in ("product_version", "environment", "mode")},
        )
        session.add(candidate)
        session.flush()
        event(session, candidate, principal, "created", "摘录完成；仍需人工语义复核")
    return view(session, principal, row)


def listing(session, principal, offset, limit, ticket_id=None):
    filters = [Retrospective.organization_id == principal.organization_id]
    if ticket_id is not None:
        read_ticket(session, principal, ticket_id)
        filters.append(Retrospective.ticket_id == ticket_id)
    rows = session.scalars(
        select(Retrospective)
        .where(*filters)
        .order_by(Retrospective.created_at.desc(), Retrospective.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            view(session, principal, get_retrospective(session, principal, r.id)) for r in rows
        ],
        "offset": offset,
        "limit": limit,
        "total": session.scalar(select(func.count()).select_from(Retrospective).where(*filters)),
    }


def decide(session, principal, identity, request):
    row = get_candidate(session, principal, identity, lock=True)
    if row.revision != request.revision or row.status != "candidate":
        raise fail("MEMORY_REVISION_CHANGED", "候选状态已改变，请重新读取。")
    row.status = "invalidated" if request.decision == "invalidate" else "revoked"
    row.revision += 1
    session.flush()
    event(session, row, principal, request.decision, request.reason)
    return candidate_view(session, row)


def conflict(session, principal, identity, request):
    if identity == request.other_id:
        raise fail("MEMORY_CONFLICT_INVALID", "不能与自身标注冲突。")
    rows = {
        i: get_candidate(session, principal, i, lock=True)
        for i in sorted([identity, request.other_id])
    }
    left, right = rows[identity], rows[request.other_id]
    if (
        left.revision != request.revision
        or right.revision != request.other_revision
        or any(r.status != "candidate" or r.expires_at <= now() for r in rows.values())
    ):
        raise fail("MEMORY_REVISION_CHANGED", "候选状态已改变，请重新读取。")
    if any(
        getattr(left, k) != getattr(right, k) for k in ("product_version", "environment", "mode")
    ):
        raise fail("MEMORY_CONFLICT_INVALID", "冲突标注需要同版本、环境和模式。")
    pair = sorted(rows)
    if session.get(MemoryConflict, tuple(pair)) is not None:
        raise fail("MEMORY_CONFLICT_EXISTS", "这两条候选已有冲突记录。")
    data = {
        "left_id": str(pair[0]),
        "right_id": str(pair[1]),
        "reason": request.reason,
        "at": now().isoformat(),
        "user_id": str(principal.user_id),
        "method": "human_declared_conflict",
    }
    session.add(
        MemoryConflict(
            left_id=pair[0],
            right_id=pair[1],
            organization_id=principal.organization_id,
            data=data,
            sha256=digest(data),
        )
    )
    for row in rows.values():
        row.revision += 1
    session.flush()
    for row in rows.values():
        event(
            session,
            row,
            principal,
            "conflict_declared",
            request.reason,
            other_id=str(right.id if row.id == left.id else left.id),
        )
    return {"items": [candidate_view(session, rows[i]) for i in pair]}


def verify_current_source(session, principal, row):
    retrospective = get_retrospective(session, principal, row.retrospective_id)
    _, body = checked_summary(retrospective.source)
    if body != row.body:
        raise fail("MEMORY_INTEGRITY_FAILED", "候选与冻结摘要不一致。")
    action = retrospective.source["action"]
    current = source_for(
        session,
        principal,
        retrospective.investigation_id,
        UUID(action["job_id"]) if action else None,
    )
    if digest(current) != retrospective.source_sha256:
        raise fail("MEMORY_SOURCE_CHANGED", "经验来源已变化，应失效原候选并重新复盘。")


def recall(session, principal, ticket_id, mode):
    ticket = read_ticket(session, principal, ticket_id)
    filters = (
        Experience.organization_id == principal.organization_id,
        Experience.product_version == ticket.product_version,
        Experience.environment == ticket.environment,
        Experience.mode == mode,
        Experience.status == "candidate",
        Experience.expires_at > now(),
    )
    records = session.scalars(
        select(Experience)
        .where(*filters)
        .order_by(Experience.created_at.desc(), Experience.id.desc())
        .limit(201)
    ).all()
    limited = len(records) > 200
    rows = {
        str(r.id): get_candidate(session, principal, r.id)
        for r in records[:200]
        if eligible(session, r) == "eligible_unreviewed"
    }
    ranking = bm25(
        {i: json.dumps(r.body, ensure_ascii=False) for i, r in rows.items()},
        ticket.title + "\n" + ticket.description,
    )
    loaded, chars = [], 0
    for identity, score in ranking:
        row = rows[identity]
        verify_current_source(session, principal, row)
        item = {
            "candidate_id": identity,
            "revision": row.revision,
            "body": row.body,
            "body_sha256": row.body_sha256,
            "source_sha256": get_retrospective(
                session, principal, row.retrospective_id
            ).source_sha256,
            "expires_at": row.expires_at.isoformat(),
            "score": score,
        }
        size = len(json.dumps(item, ensure_ascii=False, sort_keys=True))
        if len(loaded) >= 2 or chars + size > 6000:
            limited = True
            break
        chars += size
        loaded.append(item)
    return {
        "loaded": loaded,
        "selection_limited": limited,
        "selection_reason": "matched_unreviewed_methods" if loaded else "no_eligible_match",
        "scope": {
            "product_version": ticket.product_version,
            "environment": ticket.environment,
            "mode": mode,
        },
        "limitation": LIMITATION,
    }


def validate_bundle(session, principal, bundle):
    # 资格变化不能偷偷换用另一条方法；冻结快照保留，但当前规划显式停止。
    for item in bundle["loaded"]:
        row = get_candidate(session, principal, UUID(item["candidate_id"]))
        if (
            row.revision != item["revision"]
            or row.body_sha256 != item["body_sha256"]
            or eligible(session, row) != "eligible_unreviewed"
        ):
            raise fail("MEMORY_RECALL_CHANGED", "本次已加载经验失效、撤销、冲突或过期。")
        verify_current_source(session, principal, row)
