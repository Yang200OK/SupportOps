"""认证和持久化工单接口共享一次请求事务。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from sqlalchemy.orm import Session

from supportops.api.errors import ErrorResponse, ServiceErrorResponse
from supportops.auth import service as auth
from supportops.auth.contracts import LoginRequest, LoginResult, PrincipalView
from supportops.db.runtime import MIGRATION, request_session
from supportops.tickets import service as tickets
from supportops.tickets.contracts import TicketDraft, TicketList, TicketView

Transaction = Annotated[Session, Depends(request_session, scope="function")]
Identity = Annotated[auth.Principal, Depends(auth.current_principal)]
FAILURES = {
    401: {"model": ServiceErrorResponse},
    503: {"model": ServiceErrorResponse},
    422: {"model": ErrorResponse},
}


def register_persistence_routes(app: FastAPI) -> None:
    @app.get("/health/ready", tags=["health"], responses={503: {"model": ServiceErrorResponse}})
    def readiness(session: Transaction):
        return {"service": "supportops", "database": "ready", "migration": MIGRATION}

    @app.post("/api/auth/login", response_model=LoginResult, tags=["auth"], responses=FAILURES)
    def login(payload: LoginRequest, request: Request, session: Transaction) -> LoginResult:
        return auth.login(session, payload, request.app.state.settings.session_ttl_seconds)

    @app.get("/api/auth/me", response_model=PrincipalView, tags=["auth"], responses=FAILURES)
    def me(principal: Identity) -> PrincipalView:
        return principal.view()

    @app.post("/api/auth/logout", status_code=204, tags=["auth"], responses=FAILURES)
    def logout(principal: Identity, session: Transaction) -> Response:
        auth.logout(session, principal)
        return Response(status_code=204)

    @app.post(
        "/api/tickets",
        status_code=201,
        response_model=TicketView,
        tags=["tickets"],
        responses=FAILURES,
        summary="创建当前组织的持久化工单",
    )
    def create_ticket(
        payload: TicketDraft, principal: Identity, session: Transaction
    ) -> TicketView:
        return tickets.create_ticket(session, principal, payload)

    @app.get("/api/tickets", response_model=TicketList, tags=["tickets"], responses=FAILURES)
    def list_tickets(
        principal: Identity,
        session: Transaction,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> TicketList:
        return tickets.list_tickets(session, principal, offset, limit)

    @app.get(
        "/api/tickets/{ticket_id}",
        response_model=TicketView,
        tags=["tickets"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def read_ticket(ticket_id: UUID, principal: Identity, session: Transaction) -> TicketView:
        return tickets.read_ticket(session, principal, ticket_id)
