"""复盘和治理在认证事务中执行，来源参数只接受已有对象 ID。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Query, Response

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.memory import service
from supportops.memory.contracts import ConflictRequest, Decision, RetrospectiveRequest


def register_memory_routes(app: FastAPI):
    @app.post(
        "/api/investigations/{identity}/retrospectives",
        status_code=201,
        tags=["memory"],
        responses=FAILURES,
    )
    def create(
        identity: UUID, payload: RetrospectiveRequest, principal: Identity, session: Transaction
    ):
        return service.create(session, principal, identity, payload)

    @app.get("/api/retrospectives", tags=["memory"], responses=FAILURES)
    def listing(
        principal: Identity,
        session: Transaction,
        response: Response,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        limit: Annotated[int, Query(ge=1, le=30)] = 10,
        ticket_id: UUID | None = None,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.listing(session, principal, offset, limit, ticket_id)

    @app.get("/api/retrospectives/{identity}", tags=["memory"], responses=FAILURES)
    def detail(
        identity: UUID,
        principal: Identity,
        session: Transaction,
        response: Response,
        include_source: bool = False,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.view(
            session,
            principal,
            service.get_retrospective(session, principal, identity),
            include_source,
        )

    @app.post("/api/experiences/{identity}/decisions", tags=["memory"], responses=FAILURES)
    def decision(identity: UUID, payload: Decision, principal: Identity, session: Transaction):
        return service.decide(session, principal, identity, payload)

    @app.post("/api/experiences/{identity}/conflicts", tags=["memory"], responses=FAILURES)
    def conflict(
        identity: UUID, payload: ConflictRequest, principal: Identity, session: Transaction
    ):
        return service.conflict(session, principal, identity, payload)

    @app.get("/api/tickets/{identity}/memory-recall", tags=["memory"], responses=FAILURES)
    def recall(
        identity: UUID,
        mode: Literal["startup", "online"],
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.recall(session, principal, identity, mode)
