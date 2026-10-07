"""向量接口复用原会话与事务，不接收客户端组织或答案。"""

from uuid import UUID

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse

from supportops.api.errors import ServiceErrorResponse
from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.retrieval import advanced, service
from supportops.retrieval.contracts import AdvancedSearch, BuildIndex


def register_retrieval_routes(app: FastAPI):
    @app.exception_handler(advanced.AdvancedFailure)
    async def advanced_failure(request, exc):
        # 只公开已核对的阶段用量；失败请求不能返回候选或服务商正文。
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "stage": exc.stage,
                "usage": exc.usage,
            },
        )

    failures = {
        **FAILURES,
        404: {"model": ServiceErrorResponse},
        409: {"model": ServiceErrorResponse},
    }
    path = "/api/retrieval/indexes"

    @app.post(path, tags=["retrieval"], responses=failures)
    def build(payload: BuildIndex, principal: Identity, session: Transaction):
        return service.build_index(session, principal, payload)

    @app.get(path, tags=["retrieval"], responses=failures)
    def indexes(
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_indexes(session, principal, offset, limit)

    @app.get(path + "/{index_id}", tags=["retrieval"], responses=failures)
    def read(index_id: UUID, principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.view(service.get_index(session, principal, index_id))

    @app.get(path + "/{index_id}/entries", tags=["retrieval"], responses=failures)
    def entries(
        index_id: UUID,
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.entries_page(session, principal, index_id, offset, limit)

    @app.post(path + "/{index_id}/search", tags=["retrieval"], responses=failures)
    def search(
        index_id: UUID,
        payload: AdvancedSearch,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return advanced.search(session, principal, index_id, payload)
