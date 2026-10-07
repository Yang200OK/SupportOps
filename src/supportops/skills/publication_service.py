"""组织级草稿、绑定报告审批、显式发布和规划前资格核对。"""

import json
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.memory import service as memory
from supportops.skills.catalog import MAX_CONTEXT_CHARS, sha256
from supportops.skills.publication_core import method_body, regression
from supportops.skills.publication_models import (
    SkillDecision,
    SkillDraft,
    SkillEvent,
    SkillRegression,
)
from supportops.tickets.service import read_ticket


def fail(code, message, status=409):
    return ServiceError(status, code, message)


def lock_request(session, principal, request_id):
    key = int(digest([str(principal.user_id), str(request_id), "skill-publication"])[:15], 16)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def events(session, row):
    records = session.scalars(
        select(SkillEvent)
        .where(SkillEvent.draft_id == row.id, SkillEvent.organization_id == row.organization_id)
        .order_by(SkillEvent.revision)
    ).all()
    if (
        [r.revision for r in records] != list(range(1, row.revision + 1))
        or any(digest(r.data) != r.sha256 or r.data["revision"] != r.revision for r in records)
        or records[-1].data["status"] != row.status
    ):
        raise fail("SKILL_PUBLICATION_INTEGRITY", "发布治理事件不完整或摘要不一致。")
    return [r.data for r in records]


def get(session, principal, identity, lock=False):
    query = select(SkillDraft).where(
        SkillDraft.id == identity, SkillDraft.organization_id == principal.organization_id
    )
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise fail("SKILL_DRAFT_NOT_FOUND", "方法草稿不存在或不属于当前组织。", 404)
    payload = row.payload
    if (
        digest(payload) != row.payload_sha256
        or payload["candidate_id"] != str(row.candidate_id)
        or digest(payload["candidate_body"]) != payload["candidate_sha256"]
        or method_body(payload["candidate_body"]) != payload["method"]
    ):
        raise fail("SKILL_PUBLICATION_INTEGRITY", "方法正文、来源或摘要不一致。")
    events(session, row)
    return row


def check_source(session, principal, row):
    candidate = memory.get_candidate(session, principal, row.candidate_id)
    payload = row.payload
    if (
        candidate.revision != payload["candidate_revision"]
        or candidate.body_sha256 != payload["candidate_sha256"]
        or memory.eligible(session, candidate) != "eligible_unreviewed"
    ):
        raise fail("SKILL_SOURCE_INELIGIBLE", "方法来源已冲突、过期、失效、撤销或修订。")
    memory.verify_current_source(session, principal, candidate)
    retrospective = memory.get_retrospective(session, principal, candidate.retrospective_id)
    if retrospective.source_sha256 != payload["source_sha256"]:
        raise fail("SKILL_SOURCE_INELIGIBLE", "方法绑定的来源摘要已经变化。")
    return candidate


def append_event(session, principal, row, kind, reason, **extra):
    data = {
        "revision": row.revision,
        "status": row.status,
        "event": kind,
        "reason": reason,
        "user_id": str(principal.user_id),
        "at": memory.now().isoformat(),
        **extra,
    }
    session.add(
        SkillEvent(
            draft_id=row.id,
            organization_id=row.organization_id,
            revision=row.revision,
            data=data,
            sha256=digest(data),
        )
    )
    session.flush()


def report_view(session, principal, identity, row):
    record = session.scalar(
        select(SkillRegression).where(
            SkillRegression.id == identity,
            SkillRegression.organization_id == principal.organization_id,
            SkillRegression.draft_id == row.id,
        )
    )
    if record is None:
        raise fail("SKILL_REPORT_NOT_FOUND", "回归报告不存在或不属于当前草稿。", 404)
    if (
        digest(record.report) != record.sha256
        or record.report["payload_sha256"] != row.payload_sha256
    ):
        raise fail("SKILL_PUBLICATION_INTEGRITY", "回归报告摘要或正文绑定不一致。")
    return {"report_id": str(record.id), "report": record.report, "sha256": record.sha256}


def view(session, principal, row):
    reports = session.scalars(
        select(SkillRegression)
        .where(
            SkillRegression.draft_id == row.id,
            SkillRegression.organization_id == row.organization_id,
        )
        .order_by(SkillRegression.id)
    ).all()
    decisions = session.scalars(
        select(SkillDecision)
        .where(
            SkillDecision.draft_id == row.id, SkillDecision.organization_id == row.organization_id
        )
        .order_by(SkillDecision.id)
    ).all()
    for d in decisions:
        if digest(d.data) != d.sha256:
            raise fail("SKILL_PUBLICATION_INTEGRITY", "审批摘要不一致。")
    try:
        check_source(session, principal, row)
        eligibility = "eligible" if row.status == "published" else row.status
    except ServiceError as exc:
        eligibility = exc.code
    return {
        "draft_id": str(row.id),
        "payload": row.payload,
        "payload_sha256": row.payload_sha256,
        "status": row.status,
        "revision": row.revision,
        "created_at": row.created_at,
        "eligibility": eligibility,
        "events": events(session, row),
        "reports": [report_view(session, principal, r.id, row) for r in reports],
        "decisions": [
            {"decision_id": str(d.id), "data": d.data, "sha256": d.sha256} for d in decisions
        ],
    }


def create(session, principal, request):
    lock_request(session, principal, request.request_id)
    old = session.scalar(
        select(SkillDraft).where(
            SkillDraft.organization_id == principal.organization_id,
            SkillDraft.requester_id == principal.user_id,
            SkillDraft.request_id == request.request_id,
        )
    )
    if old:
        if (
            old.candidate_id != request.candidate_id
            or old.payload["candidate_revision"] != request.candidate_revision
        ):
            raise fail("SKILL_REQUEST_CONFLICT", "同一请求键不能改变候选或修订。")
        return view(session, principal, get(session, principal, old.id))
    c = memory.get_candidate(session, principal, request.candidate_id, lock=True)
    if (
        c.revision != request.candidate_revision
        or memory.eligible(session, c) != "eligible_unreviewed"
    ):
        raise fail("SKILL_SOURCE_INELIGIBLE", "只能使用当前有效且无冲突的候选修订。")
    memory.verify_current_source(session, principal, c)
    retrospective = memory.get_retrospective(session, principal, c.retrospective_id)
    payload = {
        "candidate_id": str(c.id),
        "candidate_revision": c.revision,
        "candidate_body": deepcopy(c.body),
        "candidate_sha256": c.body_sha256,
        "retrospective_id": str(c.retrospective_id),
        "source_sha256": retrospective.source_sha256,
        "expires_at": c.expires_at.isoformat(),
        "method": method_body(c.body),
    }
    row = SkillDraft(
        id=uuid4(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        request_id=request.request_id,
        candidate_id=c.id,
        payload=payload,
        payload_sha256=digest(payload),
        status="draft",
        revision=1,
        created_at=memory.now(),
    )
    session.add(row)
    session.flush()
    append_event(session, principal, row, "created", "确定性方法摘录，等待回归与审批")
    return view(session, principal, row)


def regress(session, principal, identity):
    row = get(session, principal, identity, lock=True)
    if row.status != "draft":
        raise fail("SKILL_REVISION_CHANGED", "只有待审批草稿可以追加回归报告。")
    report = regression(row.payload["method"], method_body(row.payload["candidate_body"]))
    try:
        check_source(session, principal, row)
        source_check = {"name": "current_source_eligibility", "passed": True}
    except ServiceError as exc:
        source_check = {"name": "current_source_eligibility", "passed": False, "error": exc.code}
    report["checks"].append(source_check)
    report["passed"] = all(c["passed"] for c in report["checks"])
    report.update(
        draft_id=str(row.id),
        payload_sha256=row.payload_sha256,
        source_sha256=row.payload["source_sha256"],
        at=memory.now().isoformat(),
        user_id=str(principal.user_id),
    )
    record = SkillRegression(
        id=uuid4(),
        draft_id=row.id,
        organization_id=row.organization_id,
        report=report,
        sha256=digest(report),
    )
    session.add(record)
    session.flush()
    return report_view(session, principal, record.id, row)


def decide(session, principal, identity, request):
    lock_request(session, principal, request.request_id)
    old = session.scalar(
        select(SkillDecision).where(
            SkillDecision.organization_id == principal.organization_id,
            SkillDecision.requester_id == principal.user_id,
            SkillDecision.request_id == request.request_id,
        )
    )
    if old:
        if old.draft_id != identity or old.data["request"] != request.model_dump(mode="json"):
            raise fail("SKILL_REQUEST_CONFLICT", "同一审批请求键不能改变内容。")
        return view(session, principal, get(session, principal, identity))
    row = get(session, principal, identity, lock=True)
    if (
        row.revision != request.revision
        or row.status != "draft"
        or row.payload_sha256 != request.payload_sha256
    ):
        raise fail("SKILL_REVISION_CHANGED", "草稿状态或审批正文摘要已改变。")
    report = report_view(session, principal, request.report_id, row)
    if request.decision == "approve":
        check_source(session, principal, row)
        if not report["report"]["passed"]:
            raise fail("SKILL_REGRESSION_FAILED", "回归未通过，不能批准发布方法。")
    row.status = "approved" if request.decision == "approve" else "rejected"
    row.revision += 1
    data = {
        "request": request.model_dump(mode="json"),
        "user_id": str(principal.user_id),
        "session_id": str(principal.session_id),
        "at": memory.now().isoformat(),
        "report_sha256": report["sha256"],
        "human_semantic_reviewed": False,
    }
    record = SkillDecision(
        id=uuid4(),
        draft_id=row.id,
        organization_id=row.organization_id,
        requester_id=principal.user_id,
        request_id=request.request_id,
        data=data,
        sha256=digest(data),
    )
    session.add(record)
    session.flush()
    append_event(
        session, principal, row, request.decision, request.reason, decision_id=str(record.id)
    )
    return view(session, principal, row)


def approved_decision(session, principal, row):
    decisions = session.scalars(
        select(SkillDecision).where(
            SkillDecision.draft_id == row.id, SkillDecision.organization_id == row.organization_id
        )
    ).all()
    if len(decisions) != 1:
        raise fail("SKILL_APPROVAL_INVALID", "方法必须绑定唯一的有效审批。")
    decision = decisions[0]
    data = decision.data
    request = data["request"]
    report = report_view(session, principal, UUID(request["report_id"]), row)
    if (
        digest(data) != decision.sha256
        or request["decision"] != "approve"
        or request["payload_sha256"] != row.payload_sha256
        or request["revision"] != 1
        or data["report_sha256"] != report["sha256"]
        or not report["report"]["passed"]
    ):
        raise fail("SKILL_APPROVAL_INVALID", "审批与正文、回归报告不一致。")


def transition(session, principal, identity, request, kind):
    row = get(session, principal, identity, lock=True)
    allowed = ("approved",) if kind == "publish" else ("approved", "published")
    if row.revision != request.revision or row.status not in allowed:
        raise fail("SKILL_REVISION_CHANGED", "方法状态已改变，请重新读取。")
    if kind == "publish":
        check_source(session, principal, row)
        approved_decision(session, principal, row)
        if not regression(row.payload["method"], method_body(row.payload["candidate_body"]))[
            "passed"
        ]:
            raise fail("SKILL_REGRESSION_FAILED", "当前方法结构未通过回归门槛。")
    row.status = "published" if kind == "publish" else "revoked"
    row.revision += 1
    session.flush()
    append_event(session, principal, row, kind, request.reason)
    return view(session, principal, row)


def listing(session, principal, offset, limit):
    filters = (SkillDraft.organization_id == principal.organization_id,)
    rows = session.scalars(
        select(SkillDraft)
        .where(*filters)
        .order_by(SkillDraft.created_at.desc(), SkillDraft.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [view(session, principal, get(session, principal, r.id)) for r in rows],
        "total": session.scalar(select(func.count()).select_from(SkillDraft).where(*filters)),
        "offset": offset,
        "limit": limit,
    }


def prepare(session, principal, ticket_id, mode, base=None):
    ticket = read_ticket(session, principal, ticket_id)
    # 元数据投影只查询范围与症状；匹配后才核对完整正文与原始来源。
    records = session.execute(
        select(SkillDraft.id, SkillDraft.payload["method"]["signals"].label("signals"))
        .where(
            SkillDraft.organization_id == principal.organization_id,
            SkillDraft.status == "published",
            SkillDraft.payload["method"]["product_version"].astext == ticket.product_version,
            SkillDraft.payload["method"]["environment"].astext == ticket.environment,
            SkillDraft.payload["method"]["mode"].astext == mode,
        )
        .order_by(SkillDraft.created_at.desc(), SkillDraft.id.desc())
        .limit(201)
    ).all()
    words = (ticket.title + "\n" + ticket.description).casefold()
    matches = [(r.id, [s for s in r.signals if s.casefold() in words]) for r in records[:200]]
    matches = sorted((r for r in matches if r[1]), key=lambda r: (-len(r[1]), str(r[0])))
    bundle = (
        deepcopy(base)
        if base is not None
        else {
            "catalog": {
                "catalog_sha256": digest([]),
                "items": [],
                "source_type": "organization_publications",
            },
            "loaded": [],
            "selection_limited": False,
            "selection_reason": "no_matching_skill",
            "max_loaded": 2,
            "role": "planning_guidance_only",
            "selection_version": "published-signal.v1",
        }
    )
    bundle["publication_selection"] = {
        "matched": len(matches),
        "scope": {
            "product_version": ticket.product_version,
            "environment": ticket.environment,
            "mode": mode,
        },
    }
    for identity, signals in matches:
        if len(bundle["loaded"]) >= 2:
            bundle["selection_limited"] = True
            break
        row = get(session, principal, identity)
        try:
            check_source(session, principal, row)
        except ServiceError as exc:
            # 不合格记录明确展示排除原因；已加载版本改变时则由 validate_bundle 停止。
            bundle.setdefault("excluded_publications", []).append(
                {"draft_id": str(row.id), "error": exc.code}
            )
            continue
        approved_decision(session, principal, row)
        item = {
            "skill_id": "experience-" + str(row.id),
            "version": "1.0.0",
            "title": row.payload["method"]["title"],
            "body": json.dumps(row.payload["method"], ensure_ascii=False),
            "body_sha256": sha256(
                json.dumps(row.payload["method"], ensure_ascii=False).encode("utf-8")
            ),
            "publication_id": str(row.id),
            "publication_revision": row.revision,
            "payload_sha256": row.payload_sha256,
            "matched_signals": signals,
            "role": "planning_guidance_only",
        }
        proposed = deepcopy(bundle)
        proposed["loaded"].append(item)
        if len(json.dumps(proposed, ensure_ascii=False, separators=(",", ":"))) > MAX_CONTEXT_CHARS:
            bundle["selection_limited"] = True
            break
        bundle = proposed
        bundle["selection_reason"] = "matched"
    bundle["selection_limited"] |= len(records) > 200
    if len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))) > MAX_CONTEXT_CHARS:
        raise fail("SKILL_CONTEXT_LIMIT", "共同 Skill 上下文超过原预算。")
    return bundle


def validate_bundle(session, principal, bundle):
    for item in bundle["loaded"]:
        if "publication_id" not in item:
            continue
        row = get(session, principal, UUID(item["publication_id"]))
        if (
            row.status != "published"
            or row.revision != item["publication_revision"]
            or row.payload_sha256 != item["payload_sha256"]
        ):
            raise fail("SKILL_PUBLICATION_CHANGED", "已加载的方法发布已改变或撤销。")
        check_source(session, principal, row)
        approved_decision(session, principal, row)
