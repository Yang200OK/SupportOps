"""业务应用没有实验控制路由，仅保存和查看所属组织的公共观测。"""

from uuid import UUID

from fastapi import FastAPI, Query

from supportops.api.errors import ServiceErrorResponse
from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.experiments import service
from supportops.experiments.contracts import (
    ExperimentDetail,
    ExperimentPage,
    ExperimentView,
    ImportExperiment,
)


def register_experiment_routes(app: FastAPI):
    failures = {
        **FAILURES,
        404: {"model": ServiceErrorResponse},
        409: {"model": ServiceErrorResponse},
    }

    @app.post(
        "/api/experiments/import",
        response_model=ExperimentView,
        tags=["experiments"],
        responses=failures,
    )
    def submit(payload: ImportExperiment, principal: Identity, session: Transaction):
        return service.import_bundle(session, principal, payload)

    @app.get(
        "/api/experiments", response_model=ExperimentPage, tags=["experiments"], responses=failures
    )
    def listing(
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_experiments(session, principal, offset, limit)

    @app.get(
        "/api/experiments/{identity}",
        response_model=ExperimentDetail,
        response_model_exclude_none=True,
        tags=["experiments"],
        responses=failures,
    )
    def read(identity: UUID, principal: Identity, session: Transaction):
        return service.read_experiment(session, principal, identity)
