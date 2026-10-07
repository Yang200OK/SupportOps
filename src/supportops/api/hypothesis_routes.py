"""独立假设调查和只读实验列表；没有普通用户注册或控制动作接口。"""

from uuid import UUID

from fastapi import FastAPI, Query, Request, Response

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.investigations import live_service
from supportops.investigations.hypothesis_contracts import HypothesisRequest


def register_hypothesis_routes(app: FastAPI):
    @app.post(
        "/api/tickets/{ticket_id}/hypothesis-investigations",
        tags=["hypothesis-investigations"],
        responses=FAILURES,
        status_code=201,
    )
    def create(
        ticket_id: UUID,
        payload: HypothesisRequest,
        principal: Identity,
        session: Transaction,
        request: Request,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return live_service.create(
            session,
            principal,
            ticket_id,
            payload,
            request.app.state.settings.database_url.get_secret_value(),
        )

    @app.get(
        "/api/tickets/{ticket_id}/hypothesis-investigations",
        tags=["hypothesis-investigations"],
        responses=FAILURES,
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
        return live_service.listing(session, principal, ticket_id, offset, limit)

    @app.get("/api/investigation-lab-runs", tags=["hypothesis-investigations"], responses=FAILURES)
    def registrations(
        principal: Identity,
        session: Transaction,
        response: Response,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(30, ge=1, le=30),
    ):
        response.headers["Cache-Control"] = "no-store"
        return live_service.registrations(session, principal, offset, limit)

    @app.get(
        "/api/investigations/{identity}/evidence/{evidence_id}",
        tags=["hypothesis-investigations"],
        responses=FAILURES,
    )
    def evidence(
        identity: UUID,
        evidence_id: str,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return live_service.evidence_reference(session, principal, identity, evidence_id)
