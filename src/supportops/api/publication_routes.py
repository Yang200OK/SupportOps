"""认证事务内审批组织级方法，发布不执行实验动作。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Query, Response

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.skills import pair_reports
from supportops.skills import publication_service as service
from supportops.skills.publication_contracts import DraftRequest, ReviewRequest, TransitionRequest


def register_publication_routes(app: FastAPI):
    @app.get("/api/memory-pairs", tags=["skill-publications"], responses=FAILURES)
    def pairs(
        principal: Identity,
        session: Transaction,
        response: Response,
        cohort: Literal["current", "max", "legacy"] = "current",
    ):
        response.headers["Cache-Control"] = "no-store"
        return pair_reports.load(session, principal, cohort)

    @app.post("/api/skill-drafts", status_code=201, tags=["skill-publications"], responses=FAILURES)
    def create(payload: DraftRequest, principal: Identity, session: Transaction):
        return service.create(session, principal, payload)

    @app.get("/api/skill-drafts", tags=["skill-publications"], responses=FAILURES)
    def listing(
        principal: Identity,
        session: Transaction,
        response: Response,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        limit: Annotated[int, Query(ge=1, le=30)] = 10,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.listing(session, principal, offset, limit)

    @app.get("/api/skill-drafts/{identity}", tags=["skill-publications"], responses=FAILURES)
    def detail(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(session, principal, service.get(session, principal, identity))

    @app.post(
        "/api/skill-drafts/{identity}/regressions", tags=["skill-publications"], responses=FAILURES
    )
    def regress(identity: UUID, principal: Identity, session: Transaction):
        return service.regress(session, principal, identity)

    @app.post(
        "/api/skill-drafts/{identity}/decisions", tags=["skill-publications"], responses=FAILURES
    )
    def decide(identity: UUID, payload: ReviewRequest, principal: Identity, session: Transaction):
        return service.decide(session, principal, identity, payload)

    @app.post(
        "/api/skill-drafts/{identity}/publish", tags=["skill-publications"], responses=FAILURES
    )
    def publish(
        identity: UUID, payload: TransitionRequest, principal: Identity, session: Transaction
    ):
        return service.transition(session, principal, identity, payload, "publish")

    @app.post(
        "/api/skill-drafts/{identity}/revoke", tags=["skill-publications"], responses=FAILURES
    )
    def revoke(
        identity: UUID, payload: TransitionRequest, principal: Identity, session: Transaction
    ):
        return service.transition(session, principal, identity, payload, "revoke")
