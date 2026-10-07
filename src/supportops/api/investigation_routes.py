"""独立调查入口，不改变原运行接口。"""

from uuid import UUID

from fastapi import FastAPI, Query, Request, Response

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.investigations import service
from supportops.investigations.contracts import InvestigationRequest


def register_investigation_routes(app: FastAPI):
    @app.post(
        "/api/tickets/{ticket_id}/investigations",
        tags=["investigations"],
        responses=FAILURES,
        status_code=201,
    )
    def create(
        ticket_id: UUID,
        payload: InvestigationRequest,
        principal: Identity,
        session: Transaction,
        request: Request,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.create(
            session,
            principal,
            ticket_id,
            payload,
            request.app.state.settings.database_url.get_secret_value(),
        )

    @app.get("/api/investigations/{identity}", tags=["investigations"], responses=FAILURES)
    def read(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.read(session, principal, identity)

    @app.get("/api/tickets/{ticket_id}/investigations", tags=["investigations"], responses=FAILURES)
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
