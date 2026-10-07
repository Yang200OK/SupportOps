"""行锁与追加快照保障并发扣账；外部调用不持有事务。"""

import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from supportops.actions.service import authenticate
from supportops.actions.service import transaction as transaction
from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.coordination import service as boards
from supportops.coordination.core import ROLES
from supportops.coordination.execution_contracts import RoleScope
from supportops.coordination.execution_core import TERMINAL, initial_state, task_package
from supportops.coordination.execution_models import Execution, ExecutionEvent
from supportops.coordination.models import Board
from supportops.rag.model import AnswerModelSettings


def error(code):
    return ServiceError(409, code, "协作执行停止，请查看执行记录中的阶段与错误代码。")


@contextmanager
def receipt_transaction(database, principal):
    """已派发调用的内部回执写入；会话撤销不抹掉已发生的用量，不授予新调用。"""
    with Session(database.engine) as session, session.begin():
        database.check_ready(session)
        session.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(principal.organization_id)},
        )
        yield session


def model_binding(settings):
    # 配置快照排除凭据，模型及接口显式绑定，不允许恢复时替换。
    return {
        "main_model": settings.main_model,
        "base_url": settings.base_url,
        "timeout_seconds": settings.timeout_seconds,
    }


def save(session, row, state, name):
    previous = row.state_sha256
    row.state = deepcopy(state)
    row.state_sha256 = digest(state)
    row.revision += 1
    session.flush()
    data = {
        "event": name,
        "at": datetime.now(timezone.utc).isoformat(),
        "revision": row.revision,
        "previous_state_sha256": previous,
        "state": deepcopy(state),
        "state_sha256": row.state_sha256,
    }
    session.add(
        ExecutionEvent(
            execution_id=row.id,
            revision=row.revision,
            organization_id=row.organization_id,
            data=data,
            sha256=digest(data),
        )
    )
    session.flush()


def get(session, principal, identity, lock=False):
    statement = select(Execution).where(
        Execution.id == identity, Execution.organization_id == principal.organization_id
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise ServiceError(404, "COORDINATION_EXECUTION_NOT_FOUND", "协作执行不存在。")
    if digest(row.binding) != row.binding_sha256 or digest(row.state) != row.state_sha256:
        raise error("EXECUTION_INTEGRITY_FAILED")
    events = session.scalars(
        select(ExecutionEvent)
        .where(
            ExecutionEvent.execution_id == row.id,
            ExecutionEvent.organization_id == row.organization_id,
        )
        .order_by(ExecutionEvent.revision)
    ).all()
    previous = None
    for n, event in enumerate(events, 1):
        if (
            event.revision != n
            or digest(event.data) != event.sha256
            or digest(event.data["state"]) != event.data["state_sha256"]
            or event.data["previous_state_sha256"] != previous
        ):
            raise error("EXECUTION_INTEGRITY_FAILED")
        previous = event.data["state_sha256"]
    if len(events) != row.revision or previous != row.state_sha256:
        raise error("EXECUTION_INTEGRITY_FAILED")
    board = boards.get_board(session, principal, row.board_id)
    if (
        row.binding["board_sha256"] != board.plan_sha256
        or row.binding["input_sha256"] != board.input_sha256
    ):
        raise error("EXECUTION_INTEGRITY_FAILED")
    return row


def view(session, row):
    board = session.scalar(
        select(Board).where(Board.id == row.board_id, Board.organization_id == row.organization_id)
    )
    boards.verify(board)
    events = session.scalars(
        select(ExecutionEvent)
        .where(
            ExecutionEvent.execution_id == row.id,
            ExecutionEvent.organization_id == row.organization_id,
        )
        .order_by(ExecutionEvent.revision)
    ).all()
    operations = row.state["operations"]
    known = [o["usage"] for o in operations if o["kind"] == "model" and o["usage"] is not None]
    return {
        "execution_id": str(row.id),
        "board_id": str(row.board_id),
        "revision": row.revision,
        "created_at": row.created_at,
        "workflow_version": "bounded-coordination-execution.v1",
        "binding_sha256": row.binding_sha256,
        "state_sha256": row.state_sha256,
        "budget": board.plan["budget"],
        **deepcopy(row.state),
        "model_accounting": {
            "known_calls": len(known),
            "unknown_calls": sum(
                o["kind"] == "model" and o["usage"] is None and o["status"] != "started"
                for o in operations
            ),
            "pending_calls": sum(
                o["kind"] == "model" and o["status"] == "started" for o in operations
            ),
            "input_tokens": sum(u["input_tokens"] for u in known),
            "output_tokens": sum(u["output_tokens"] for u in known),
            "cost_cny": None,
        },
        "events": [
            {
                "revision": e.revision,
                "event": e.data["event"],
                "at": e.data["at"],
                "sha256": e.sha256,
            }
            for e in events
        ],
        "limitation": (
            "本地有界执行；模型语义尚未人工复核，冲突保持未决，未验证单 / 多 Agent 收益。"
        ),
    }


def create(session, principal, board_id, payload):
    key = (
        f"coordination-execution:{principal.organization_id}:"
        f"{principal.user_id}:{payload.request_id}"
    )
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key})
    existing = session.scalar(
        select(Execution).where(
            Execution.organization_id == principal.organization_id,
            Execution.requester_id == principal.user_id,
            Execution.request_id == payload.request_id,
        )
    )
    if existing:
        if existing.board_id != board_id:
            raise error("EXECUTION_REQUEST_CONFLICT")
        return view(session, get(session, principal, existing.id))
    board = boards.get_board(session, principal, board_id, lock=True)
    boards.execution_package(session, principal, board.id, "documents", ROLES["documents"])
    settings = AnswerModelSettings()
    if settings.api_key is None:
        raise error("MODEL_NOT_CONFIGURED")
    state = initial_state(board.plan)
    binding = {
        "session_id": str(principal.session_id),
        "board_sha256": board.plan_sha256,
        "input_sha256": board.input_sha256,
        "model": model_binding(settings),
    }
    row = Execution(
        id=uuid4(),
        organization_id=principal.organization_id,
        requester_id=principal.user_id,
        request_id=payload.request_id,
        board_id=board_id,
        binding=binding,
        binding_sha256=digest(binding),
        state=state,
        state_sha256=digest(state),
        revision=1,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    data = {
        "event": "execution_created",
        "at": row.created_at.isoformat(),
        "revision": 1,
        "previous_state_sha256": None,
        "state": state,
        "state_sha256": row.state_sha256,
    }
    session.add(
        ExecutionEvent(
            execution_id=row.id,
            revision=1,
            organization_id=row.organization_id,
            data=data,
            sha256=digest(data),
        )
    )
    session.flush()
    return view(session, row)


def owner(row, principal):
    if row.requester_id != principal.user_id or row.binding["session_id"] != str(
        principal.session_id
    ):
        raise ServiceError(403, "EXECUTION_OWNER_REQUIRED", "仅绑定操作者与会话可推进或取消。")


def fresh(session, principal, row, task, lease, *, check_model=True):
    authenticate(session, principal)
    owner(row, principal)
    if row.state["status"] in TERMINAL or row.state["lease"] != str(lease):
        raise error("EXECUTION_NOT_RUNNING")
    if time.time() >= row.state["lease_until"] or time.time() >= row.state["deadline"]:
        raise error("TIME_BUDGET_EXHAUSTED")
    board = boards.get_board(session, principal, row.board_id)
    # 归并不调用工具，但仍必须检查工单与实验登记当前有效。
    boards.execution_package(session, principal, row.board_id, "documents", ROLES["documents"])
    if check_model and model_binding(AnswerModelSettings()) != row.binding["model"]:
        raise error("EXECUTION_MODEL_CHANGED")
    return board, task_package(board.plan, task)


def scope(row, board, task, lease, key):
    snapshot = board.input_snapshot
    return RoleScope(
        organization_id=row.organization_id,
        user_id=row.requester_id,
        session_id=row.binding["session_id"],
        ticket_id=board.ticket_id,
        index_id=snapshot["knowledge"]["index_id"],
        product_version=snapshot["ticket"]["product_version"],
        experiment_id=None,
        corpus_sha256=snapshot["knowledge"]["corpus_sha256"],
        ticket_sha256=digest(snapshot["ticket"]),
        lab_run_id=snapshot["request"]["lab_run_id"],
        registration_sha256=snapshot["registration_sha256"],
        investigation_id=row.id,
        execution_id=row.id,
        task_id=task,
        lease=lease,
        operation_key=key,
    )


def cancel(session, principal, identity):
    row = get(session, principal, identity, lock=True)
    owner(row, principal)
    if row.state["status"] in TERMINAL:
        return view(session, row)
    state = deepcopy(row.state)
    state["status"] = "cancelled"
    for task in state["tasks"].values():
        if task["status"] in {"pending", "running"}:
            task["status"] = "cancelled"
    save(session, row, state, "execution_cancelled")
    return view(session, row)


def listing(session, principal, board_id):
    boards.get_board(session, principal, board_id)
    rows = session.scalars(
        select(Execution)
        .where(
            Execution.organization_id == principal.organization_id, Execution.board_id == board_id
        )
        .order_by(Execution.created_at.desc())
        .limit(20)
    ).all()
    return {"items": [view(session, get(session, principal, r.id)) for r in rows]}


def evidence(session, principal, identity, evidence_id):
    from supportops.coordination.execution_core import collect_evidence
    from supportops.investigations.live_sources import verify_snapshot

    row = get(session, principal, identity)
    all_evidence = collect_evidence([t["evidence"] for t in row.state["tasks"].values()])
    found = next((e for e in all_evidence if e["evidence_id"] == evidence_id), None)
    if found is None:
        raise ServiceError(404, "EXECUTION_EVIDENCE_NOT_FOUND", "执行证据不存在。")
    for task in row.state["tasks"].values():
        for saved in task["results"]:
            value = saved["value"]
            if digest(value) != saved["sha256"]:
                raise error("EXECUTION_INTEGRITY_FAILED")
            if any(e["evidence_id"] == evidence_id for e in value["evidence"]):
                if "snapshot" in value:
                    raw = verify_snapshot(value)
                    return {
                        "evidence": found,
                        "snapshot": value["snapshot"],
                        "raw_source_data": raw[found["source"]["ordinal"]],
                    }
                return {"evidence": found, "tool_result_sha256": saved["sha256"]}
    raise error("EXECUTION_INTEGRITY_FAILED")
