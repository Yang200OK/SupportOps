"""应用入口只装配当前轮已实现的接口。"""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from supportops.api.action_routes import register_action_routes
from supportops.api.chunk_routes import register_chunk_routes
from supportops.api.coordination_comparison_routes import register_coordination_comparison_routes
from supportops.api.coordination_execution_routes import register_coordination_execution_routes
from supportops.api.coordination_routes import register_coordination_routes
from supportops.api.document_routes import register_document_routes
from supportops.api.errors import ErrorResponse, register_error_handlers
from supportops.api.experiment_routes import register_experiment_routes
from supportops.api.hypothesis_routes import register_hypothesis_routes
from supportops.api.investigation_routes import register_investigation_routes
from supportops.api.memory_routes import register_memory_routes
from supportops.api.persistence_routes import register_persistence_routes
from supportops.api.publication_routes import register_publication_routes
from supportops.api.rag_routes import register_rag_routes
from supportops.api.retrieval_routes import register_retrieval_routes
from supportops.api.skill_routes import register_skill_routes
from supportops.api.workbench_routes import register_workbench_routes
from supportops.db.runtime import Database
from supportops.settings import Settings
from supportops.tickets.contracts import IntakeResult, TicketDraft
from supportops.tickets.intake import validate_intake


class Liveness(BaseModel):
    service: Literal["supportops"] = "supportops"
    status: Literal["alive"] = "alive"
    version: Literal["0.1.0"] = "0.1.0"


def create_app() -> FastAPI:
    settings = Settings()
    database = Database(settings.database_url.get_secret_value()) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            if database is not None:
                database.engine.dispose()

    application = FastAPI(title="SupportOps", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database
    register_error_handlers(application)

    @application.get("/health/live", response_model=Liveness, tags=["health"])
    def liveness() -> Liveness:
        # 此接口仅证明进程存活，数据库状态由独立 ready 接口核对。
        return Liveness()

    @application.post(
        "/api/tickets/validate",
        response_model=IntakeResult,
        responses={422: {"model": ErrorResponse}},
        tags=["tickets"],
        summary="校验工单输入并返回未持久化草稿",
    )
    def validate_ticket(payload: TicketDraft) -> IntakeResult:
        return validate_intake(payload)

    register_persistence_routes(application)
    register_workbench_routes(application)
    register_document_routes(application)
    register_chunk_routes(application)
    register_experiment_routes(application)
    register_retrieval_routes(application)
    register_rag_routes(application)
    register_investigation_routes(application)
    register_hypothesis_routes(application)
    register_action_routes(application)
    register_skill_routes(application)
    register_memory_routes(application)
    register_publication_routes(application)
    register_coordination_routes(application)
    register_coordination_execution_routes(application)
    register_coordination_comparison_routes(application)
    return application


app = create_app()
