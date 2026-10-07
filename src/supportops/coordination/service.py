"""范围验证与持久化调度基础，当前不启动模型或工具。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, text

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.coordination.contracts import BoardRequest
from supportops.coordination.core import ROLES, build_plan, ready_tasks, validate_dag
from supportops.coordination.models import Board, BoardEvent
from supportops.investigations.live_service import get_registration
from supportops.retrieval.service import get_index
from supportops.tickets.service import read_ticket


def error(code, message):
    return ServiceError(409, code, message)


def event(session, record, name, principal, reason):
    data = {
        "event": name,
        "actor_id": str(principal.user_id),
        "reason": reason,
        "status": record.status,
        "revision": record.revision,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    session.add(
        BoardEvent(
            board_id=record.id,
            organization_id=record.organization_id,
            revision=record.revision,
            data=data,
            sha256=digest(data),
        )
    )
    session.flush()


def verify(record):
    if (
        digest(record.input_snapshot) != record.input_sha256
        or digest(record.plan) != record.plan_sha256
    ):
        raise error("COORDINATION_INTEGRITY_FAILED", "任务板快照摘要不一致。")
    try:
        validate_dag(record.plan["tasks"])
        payload = BoardRequest.model_validate(record.input_snapshot["request"])
        expected = build_plan(
            record.input_snapshot["ticket"],
            record.input_snapshot["lab_registration"]["mode"],
            payload,
        )
        if record.plan != expected:
            raise ValueError("任务包超出应用固定计划。")
    except (ValueError, KeyError, TypeError):
        raise error(
            "COORDINATION_INTEGRITY_FAILED", "任务图、角色或配额不符合固定调度契约。"
        ) from None


def get_board(session, principal, identity, lock=False):
    statement = select(Board).where(
        Board.id == identity, Board.organization_id == principal.organization_id
    )
    if lock:
        statement = statement.with_for_update()
    record = session.scalar(statement)
    if record is None:
        raise ServiceError(404, "COORDINATION_NOT_FOUND", "任务板不存在或不属于当前组织。")
    verify(record)
    return record


def view(session, record):
    verify(record)
    rows = session.scalars(
        select(BoardEvent)
        .where(
            BoardEvent.board_id == record.id, BoardEvent.organization_id == record.organization_id
        )
        .order_by(BoardEvent.revision)
    ).all()
    if [r.revision for r in rows] != list(range(1, record.revision + 1)) or any(
        digest(r.data) != r.sha256 for r in rows
    ):
        raise error("COORDINATION_INTEGRITY_FAILED", "任务板事件摘要或修订不一致。")
    ready = ready_tasks(record.plan["tasks"], {})
    return {
        "board_id": str(record.id),
        "ticket_id": str(record.ticket_id),
        "status": record.status,
        "revision": record.revision,
        "created_at": record.created_at,
        "workflow_version": record.plan["workflow_version"],
        "input_sha256": record.input_sha256,
        "plan_sha256": record.plan_sha256,
        "budget": record.plan["budget"],
        "execution_started": False,
        "usage": {"model_calls": 0, "tool_calls": 0},
        "limitation": "仅任务计划；子 Agent 执行、结果归并和模型效果尚未验证。",
        "tasks": [
            {
                "task_id": t["task_id"],
                "role": t["role"],
                "depends_on": t["depends_on"],
                "objective": t["package"]["objective"],
                "tools": t["package"]["tools"],
                "budget": t["package"]["budget"],
                "package_sha256": digest(t["package"]),
                "status": "cancelled"
                if record.status == "cancelled"
                else "ready"
                if t["task_id"] in ready
                else "blocked",
            }
            for t in record.plan["tasks"]
        ],
        "events": [{"data": r.data, "sha256": r.sha256} for r in rows],
    }


def create(session, principal, ticket_id, payload):
    request = payload.model_dump(mode="json")
    # 同一组织 / 操作者的请求键先串行化，避免并发重复创建与事件。
    key = f"coordination:{principal.organization_id}:{principal.user_id}:{payload.request_id}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key})
    existing = session.scalar(
        select(Board).where(
            Board.organization_id == principal.organization_id,
            Board.requester_id == principal.user_id,
            Board.request_id == payload.request_id,
        )
    )
    if existing is not None:
        if existing.ticket_id != ticket_id or existing.input_snapshot["request"] != request:
            raise error("COORDINATION_REQUEST_CONFLICT", "请求 ID 已绑定不同任务板输入。")
        return view(session, existing)
    ticket = read_ticket(session, principal, ticket_id)
    if ticket.product_version is None:
        raise ServiceError(422, "COORDINATION_VERSION_REQUIRED", "请先补充工单版本。")
    index = get_index(session, principal, payload.index_id)
    registered, binding = get_registration(session, principal, payload.lab_run_id)
    if ticket.product_version != binding.product_version:
        raise ServiceError(422, "INVESTIGATION_VERSION_MISMATCH", "实验与工单版本不符。")
    snapshot = {
        "request": request,
        "ticket": ticket.model_dump(mode="json"),
        "knowledge": {"index_id": str(index.id), "corpus_sha256": index.corpus_sha256},
        "lab_registration": binding.model_dump(mode="json"),
        "registration_sha256": registered.sha256,
    }
    plan = build_plan(snapshot["ticket"], binding.mode, payload)
    row = Board(
        id=uuid4(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        request_id=payload.request_id,
        ticket_id=ticket_id,
        input_snapshot=snapshot,
        input_sha256=digest(snapshot),
        plan=plan,
        plan_sha256=digest(plan),
        status="planned",
        revision=1,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    event(session, row, "board_planned", principal, "创建计划，未启动执行。")
    return view(session, row)


def listing(session, principal, ticket_id, offset, limit):
    read_ticket(session, principal, ticket_id)
    filters = (Board.organization_id == principal.organization_id, Board.ticket_id == ticket_id)
    rows = session.scalars(
        select(Board)
        .where(*filters)
        .order_by(Board.created_at.desc(), Board.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [view(session, r) for r in rows],
        "offset": offset,
        "limit": limit,
        "total": session.scalar(select(func.count()).select_from(Board).where(*filters)),
    }


def cancel(session, principal, identity, payload):
    record = get_board(session, principal, identity, lock=True)
    if record.revision != payload.revision or record.status != "planned":
        raise error("COORDINATION_REVISION_CONFLICT", "任务板状态或修订已变化，请重新读取。")
    record.status = "cancelled"
    record.revision += 1
    event(session, record, "board_cancelled", principal, payload.reason)
    return view(session, record)


def package(session, principal, identity, task_id):
    record = get_board(session, principal, identity)
    for task in record.plan["tasks"]:
        if task["task_id"] == task_id:
            return {
                "board_id": str(record.id),
                "package": task["package"],
                "sha256": digest(task["package"]),
                "execution_allowed": False,
            }
    raise ServiceError(404, "COORDINATION_TASK_NOT_FOUND", "任务不存在。")


def execution_package(session, principal, identity, task_id, role):
    """后续执行器边界：角色与新鲜来源重新核对，不能用展示包取得执行权限。"""
    record = get_board(session, principal, identity)
    if ROLES.get(task_id) != role:
        raise ServiceError(403, "COORDINATION_ROLE_DENIED", "角色不能读取该任务执行包。")
    if record.status != "planned" or task_id not in ready_tasks(record.plan["tasks"], {}):
        raise error("COORDINATION_TASK_NOT_READY", "任务尚未就绪或计划已取消。")
    ticket = read_ticket(session, principal, record.ticket_id)
    index = get_index(session, principal, record.input_snapshot["request"]["index_id"])
    registered, binding = get_registration(
        session, principal, record.input_snapshot["request"]["lab_run_id"]
    )
    if (
        digest(ticket.model_dump(mode="json")) != digest(record.input_snapshot["ticket"])
        or index.corpus_sha256 != record.input_snapshot["knowledge"]["corpus_sha256"]
        or registered.sha256 != record.input_snapshot["registration_sha256"]
    ):
        raise error("COORDINATION_SCOPE_CHANGED", "任务来源已变化。")
    body = package(session, principal, identity, task_id)
    # 凭据、地址和标签从未进入私有任务包，目标登记只留给服务器执行层。
    return body, binding
