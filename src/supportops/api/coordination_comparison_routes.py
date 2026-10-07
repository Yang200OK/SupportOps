"""成对比较只读入口，没有新的模型派发权限。"""

from fastapi import FastAPI, Response

from supportops.api.persistence_routes import Identity, Transaction
from supportops.coordination.comparison_reports import load


def register_coordination_comparison_routes(app: FastAPI):
    @app.get("/api/coordination-comparison", tags=["coordination"])
    def read(principal: Identity, session: Transaction, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return load(session, principal)
