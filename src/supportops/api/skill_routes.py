"""认证后读取公共发布方法，不接受文件路径、权限或上传内容。"""

from fastapi import FastAPI, Response

from supportops.api.persistence_routes import FAILURES, Identity
from supportops.lab.contracts import Version
from supportops.skills.catalog import Catalog, Mode


def register_skill_routes(app: FastAPI):
    @app.get("/api/skills", tags=["skills"], responses=FAILURES)
    def listing(principal: Identity, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return Catalog().listing()

    @app.get("/api/skills/{identity}/versions/{release}", tags=["skills"], responses=FAILURES)
    def detail(
        identity: str,
        release: str,
        product_version: Version,
        mode: Mode,
        principal: Identity,
        response: Response,
    ):
        response.headers["Cache-Control"] = "no-store"
        return Catalog().detail(identity, release, product_version, mode)
