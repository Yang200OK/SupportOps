"""回答只从服务端的当前组织检索证据，错误不回显模型正文。"""

from uuid import UUID

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.rag.contracts import AnswerRequest
from supportops.rag.guided import GuidedFailure, guided_answer
from supportops.rag.guided_contracts import GuidedRequest
from supportops.rag.screenshots import ScreenshotFailure, ScreenshotRequest, extract
from supportops.rag.service import AnswerFailure, answer


def register_rag_routes(app: FastAPI):
    @app.exception_handler(ScreenshotFailure)
    async def screenshot_failure(request, exc):
        return JSONResponse(
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "usage": exc.usage,
            },
        )

    @app.post("/api/rag/screenshots/extract", tags=["rag"], responses=FAILURES)
    def extract_screenshot(payload: ScreenshotRequest, principal: Identity, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return extract(payload)

    @app.exception_handler(GuidedFailure)
    async def guided_failure(request, exc):
        return JSONResponse(
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "stage": exc.stage,
                "usage": exc.usage,
                "trace": exc.trace,
            },
        )

    @app.post("/api/retrieval/indexes/{index_id}/guided-answer", tags=["rag"], responses=FAILURES)
    def guided_generate(
        index_id: UUID,
        payload: GuidedRequest,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return guided_answer(session, principal, index_id, payload)

    @app.exception_handler(AnswerFailure)
    async def answer_failure(request, exc):
        return JSONResponse(
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "stage": exc.stage,
                "usage": exc.usage,
            },
        )

    @app.post("/api/retrieval/indexes/{index_id}/answer", tags=["rag"], responses=FAILURES)
    def generate(
        index_id: UUID,
        payload: AnswerRequest,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return answer(session, principal, index_id, payload)
