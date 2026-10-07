"""生成、读取和原文引用均复用会话组织与请求事务。"""

from uuid import UUID

from fastapi import FastAPI, Query, Response

from supportops.api.errors import ServiceErrorResponse
from supportops.api.persistence_routes import FAILURES, Identity, Transaction
from supportops.chunks import service
from supportops.chunks.contracts import ChunkConfig, ChunkPage, ChunkSetList, ChunkSetView


def register_chunk_routes(app: FastAPI):
    failures = {
        **FAILURES,
        404: {"model": ServiceErrorResponse},
        409: {"model": ServiceErrorResponse},
    }
    path = "/api/documents/{document_id}/revisions/{revision_id}/chunk-sets"

    @app.post(path, response_model=ChunkSetView, tags=["chunks"], responses=failures)
    def create(
        document_id: UUID,
        revision_id: UUID,
        payload: ChunkConfig,
        principal: Identity,
        session: Transaction,
    ):
        return service.create_chunk_set(session, principal, document_id, revision_id, payload)

    @app.get(path, response_model=ChunkSetList, tags=["chunks"], responses=failures)
    def sets(
        document_id: UUID,
        revision_id: UUID,
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_sets(session, principal, document_id, revision_id, offset, limit)

    @app.get(
        "/api/chunk-sets/{set_id}/chunks",
        response_model=ChunkPage,
        tags=["chunks"],
        responses=failures,
    )
    def chunks(
        set_id: UUID,
        principal: Identity,
        session: Transaction,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return service.list_chunks(session, principal, set_id, offset, limit)

    @app.get(
        "/api/chunk-sets/{set_id}/chunks/{chunk_id}/citation", tags=["chunks"], responses=failures
    )
    def citation(
        set_id: UUID, chunk_id: UUID, principal: Identity, session: Transaction, response: Response
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.citation(session, principal, set_id, chunk_id)
