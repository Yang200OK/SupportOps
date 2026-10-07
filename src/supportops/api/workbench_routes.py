"""工作台新增接口复用已验收的身份与事务，不授予新权限。"""

from uuid import UUID

from fastapi import FastAPI, Query, Response

from supportops.api.errors import ServiceErrorResponse
from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.evaluations.answers import Report
from supportops.evaluations.contracts import EvaluationDataset
from supportops.runs import service
from supportops.runs.contracts import RunList, RunRequest, RunView


def register_workbench_routes(app: FastAPI) -> None:
    @app.post("/api/evaluations/answers/validate-report", tags=["evaluations"], responses=FAILURES)
    def validate_answer_report(payload: Report, principal: Identity, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {
            "summary": payload.summary(),
            "report": payload.model_dump(mode="json"),
            "execution_authenticated": False,
            "limitation": "仅校验本地报告格式与分母，不认证执行来源；规则覆盖不是语义准确率。",
        }

    @app.post(
        "/api/tickets/{ticket_id}/runs",
        response_model=RunView,
        status_code=201,
        tags=["runs"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
        summary="记录一次确定性输入检查",
    )
    def create_run(ticket_id: UUID, payload: RunRequest, principal: Identity, session: Transaction):
        return service.create_run(session, principal, ticket_id)

    @app.get("/api/runs", response_model=RunList, tags=["runs"], responses=FAILURES)
    def list_runs(
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        ticket_id: UUID | None = None,
    ):
        return service.list_runs(session, principal, offset, limit, ticket_id)

    @app.get(
        "/api/runs/{run_id}",
        response_model=RunView,
        tags=["runs"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def read_run(run_id: UUID, principal: Identity, session: Transaction):
        return service.read_run(session, principal, run_id)

    @app.get("/api/evaluations/schema", tags=["evaluations"], responses=FAILURES)
    def evaluation_schema(principal: Identity):
        return EvaluationDataset.model_json_schema()

    @app.post("/api/evaluations/validate", tags=["evaluations"], responses=FAILURES)
    def validate_dataset(payload: EvaluationDataset, principal: Identity):
        return {
            "schema_version": payload.schema_version,
            "dataset_id": payload.dataset_id,
            "sha256": payload.digest(),
            "total": len(payload.tasks),
            "dev": sum(item.split == "dev" for item in payload.tasks),
            "holdout": sum(item.split == "holdout" for item in payload.tasks),
            "executed": False,
        }
