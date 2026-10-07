"""协作任务板只规划和核对，不启动子 Agent 或动作。"""

from uuid import UUID

from fastapi import FastAPI, Query, Response

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.coordination import service
from supportops.coordination.contracts import BoardRequest, CancelRequest


def register_coordination_routes(app: FastAPI):
    @app.post(
        "/api/tickets/{ticket_id}/coordination-boards",
        status_code=201,
        tags=["coordination"],
        responses=FAILURES,
    )
    def create(
        ticket_id: UUID,
        payload: BoardRequest,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.create(session, principal, ticket_id, payload)

    @app.get(
        "/api/tickets/{ticket_id}/coordination-boards", tags=["coordination"], responses=FAILURES
    )
    def listing(
        ticket_id: UUID,
        principal: Identity,
        session: Transaction,
        response: Response,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(10, ge=1, le=30),
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.listing(session, principal, ticket_id, offset, limit)

    @app.get("/api/coordination-boards/{identity}", tags=["coordination"], responses=FAILURES)
    def read(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(session, service.get_board(session, principal, identity))

    @app.get(
        "/api/coordination-boards/{identity}/tasks/{task_id}/package",
        tags=["coordination"],
        responses=FAILURES,
    )
    def package(
        identity: UUID, task_id: str, principal: Identity, session: Transaction, response: Response
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.package(session, principal, identity, task_id)

    @app.post(
        "/api/coordination-boards/{identity}/cancel", tags=["coordination"], responses=FAILURES
    )
    def cancel(
        identity: UUID,
        payload: CancelRequest,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.cancel(session, principal, identity, payload)
