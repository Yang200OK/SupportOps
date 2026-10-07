"""人工批准与显式恢复；事件订阅本身没有副作用。"""

import json
import time
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import StreamingResponse

from supportops.actions import service
from supportops.actions.contracts import Decision, ProposalRequest
from supportops.api.persistence_routes import Identity, Transaction


def register_action_routes(app: FastAPI):
    @app.post("/api/investigations/{identity}/action-proposals", status_code=201, tags=["actions"])
    def propose(
        identity: UUID,
        payload: ProposalRequest,
        principal: Identity,
        request: Request,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.propose(request.app.state.database, principal, identity, payload)

    @app.get("/api/tickets/{ticket_id}/actions", tags=["actions"])
    def listing(ticket_id: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.listing(session, principal, ticket_id)

    @app.get("/api/actions/{identity}", tags=["actions"])
    def read(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(session, service.get(session, principal, identity))

    @app.post("/api/actions/{identity}/decision", tags=["actions"])
    def decide(identity: UUID, payload: Decision, principal: Identity, session: Transaction):
        return service.decide(session, principal, identity, payload)

    @app.post("/api/actions/{identity}/cancel", tags=["actions"])
    def cancel(identity: UUID, principal: Identity, session: Transaction):
        return service.cancel(session, principal, identity)

    @app.post("/api/actions/{identity}/advance", tags=["actions"])
    def advance(identity: UUID, principal: Identity, request: Request):
        return service.advance(request.app.state.database, principal, identity)

    @app.get("/api/actions/{identity}/events", tags=["actions"])
    def stream(
        identity: UUID,
        principal: Identity,
        request: Request,
        last_event_id: str = Header("0", alias="Last-Event-ID"),
    ):
        if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 9:
            raise service.fail("ACTION_EVENT_CURSOR_INVALID", "事件游标须为非负整数。", 422)
        cursor = int(last_event_id)
        database = request.app.state.database
        # 发响应头前核对范围，跨组织正常返回 404，而不是打开空事件流。
        with service.transaction(database, principal) as session:
            service.events(session, principal, identity, cursor)

        def generate():
            after = cursor
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with service.transaction(database, principal) as session:
                        values, status = service.events(session, principal, identity, after)
                except service.ServiceError as error:
                    yield "event: stream_error\ndata: " + json.dumps({"code": error.code}) + "\n\n"
                    return
                for value in values:
                    after = value["sequence"]
                    yield (
                        f"id: {after}\nevent: action_event\ndata: "
                        + json.dumps(value, ensure_ascii=False)
                        + "\n\n"
                    )
                if status in service.TERMINAL:
                    return
                yield ": heartbeat\n\n"
                time.sleep(0.5)
            yield 'event: stream_paused\ndata: {"reason":"window_complete"}\n\n'

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
