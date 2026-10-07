"""显式创建 / 推进就绪波；读取历史不调用模型。"""

from uuid import UUID

from fastapi import FastAPI, Request, Response

from supportops.api.persistence_routes import Identity, Transaction
from supportops.coordination import execution_service as service
from supportops.coordination.execution_contracts import ExecutionRequest
from supportops.coordination.executor import advance


def register_coordination_execution_routes(app: FastAPI):
    @app.post(
        "/api/coordination-boards/{identity}/executions", status_code=201, tags=["coordination"]
    )
    def create(
        identity: UUID,
        payload: ExecutionRequest,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.create(session, principal, identity, payload)

    @app.get("/api/coordination-boards/{identity}/executions", tags=["coordination"])
    def listing(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.listing(session, principal, identity)

    @app.get("/api/coordination-executions/{identity}", tags=["coordination"])
    def read(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(session, service.get(session, principal, identity))

    @app.post("/api/coordination-executions/{identity}/advance", tags=["coordination"])
    async def proceed(identity: UUID, principal: Identity, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await advance(request.app.state.database, principal, identity)

    @app.post("/api/coordination-executions/{identity}/cancel", tags=["coordination"])
    def cancel(identity: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.cancel(session, principal, identity)

    @app.get(
        "/api/coordination-executions/{identity}/evidence/{evidence_id}", tags=["coordination"]
    )
    def evidence(
        identity: UUID,
        evidence_id: str,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.evidence(session, principal, identity, evidence_id)
