"""资料接口复用身份和事务，原文下载同样要求组织权限。"""

import base64
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from supportops.api.errors import ServiceError, ServiceErrorResponse
from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.documents import service
from supportops.documents.contracts import (
    MAX_REQUEST_BYTES,
    DocumentList,
    DocumentView,
    ImportRequest,
    Version,
)


async def import_payload(request: Request) -> ImportRequest:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise ServiceError(413, "IMPORT_REQUEST_TOO_LARGE", "资料导入请求超过 3 MiB。")
        data.extend(chunk)
    try:
        return ImportRequest.model_validate_json(bytes(data))
    except ValidationError as failure:
        errors = [{**e, "loc": ("body", *e["loc"])} for e in failure.errors()]
        raise RequestValidationError(errors) from None


def register_document_routes(app: FastAPI):
    @app.post(
        "/api/documents/import",
        response_model=DocumentView,
        tags=["documents"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": ImportRequest.model_json_schema()}},
            }
        },
        responses={
            **FAILURES,
            409: {"model": ServiceErrorResponse},
            413: {"model": ServiceErrorResponse},
        },
    )
    def import_document(
        principal: Identity,
        session: Transaction,
        payload: Annotated[ImportRequest, Depends(import_payload)],
    ):
        # 同步数据库与解析在工作线程执行，避免等待数据库锁时阻塞事件循环。
        return service.import_document(session, principal, payload)

    @app.get("/api/documents", response_model=DocumentList, tags=["documents"], responses=FAILURES)
    def documents(
        principal: Identity,
        session: Transaction,
        product_version: Version | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_documents(session, principal, product_version, offset, limit)

    @app.get(
        "/api/documents/{document_id}/revisions",
        response_model=DocumentList,
        tags=["documents"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def revisions(
        document_id: UUID,
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_revisions(session, principal, document_id, offset, limit)

    @app.get(
        "/api/documents/{document_id}/revisions/{revision_id}",
        response_model=DocumentView,
        tags=["documents"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def revision(document_id: UUID, revision_id: UUID, principal: Identity, session: Transaction):
        return service.view(*service.get_revision(session, principal, document_id, revision_id))

    @app.get(
        "/api/documents/{document_id}/revisions/{revision_id}/raw",
        tags=["documents"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def raw(
        document_id: UUID,
        revision_id: UUID,
        principal: Identity,
        session: Transaction,
        response: Response,
    ):
        _, revision = service.get_revision(session, principal, document_id, revision_id)
        response.headers["Cache-Control"] = "no-store"
        return {"content_base64": base64.b64encode(revision.raw_bytes).decode("ascii")}

    @app.get(
        "/api/documents/{document_id}/revisions/{revision_id}/original",
        tags=["documents"],
        responses={**FAILURES, 404: {"model": ServiceErrorResponse}},
    )
    def original(document_id: UUID, revision_id: UUID, principal: Identity, session: Transaction):
        document, revision = service.get_revision(session, principal, document_id, revision_id)
        return Response(
            content=revision.raw_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(document.filename, safe=""),
                "X-Content-SHA256": revision.content_sha256,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )
