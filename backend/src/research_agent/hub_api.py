"""HTTP interface for teammate B. MCP shares the same Hub and persisted policy."""

from __future__ import annotations

import hmac
import sqlite3
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from .hub import Hub, HubError
from .hub_mcp import create_mcp
from .hub_models import (
    DeleteResponse,
    ErrorResponse,
    IngestRequest,
    IngestResponse,
    MCPFetchResult,
    PolicyState,
    PolicyUpdate,
    RetrievalStatus,
    SearchRequest,
    SearchResponse,
    SourceDefinition,
    SourcesResponse,
    StatusResponse,
    SyncFilesResponse,
    SyncStatus,
)
from .hub_runtime import HubCredentials


def create_app(
    hub: Hub, read_token: str, admin_token: str, allowed_origins: list[str] | None = None
) -> FastAPI:
    HubCredentials(read_token=read_token, admin_token=admin_token)
    mcp = create_mcp(hub)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Internal Research Data API",
        version="1.2.0",
        lifespan=lifespan,
        description="Dashboard API. Shared workspace; read/admin Bearer tokens. No end-user ACLs.",
        responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
    )
    security = HTTPBearer(auto_error=False)
    credential_dependency = Depends(security)

    def error_response(code, status, fields=None, headers=None):
        return JSONResponse(
            {"error": {"code": code, "message": code, "fields": fields or []}},
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                **(headers or {}),
            },
        )

    def matches(value, expected):
        return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))

    def authorized(credentials: HTTPAuthorizationCredentials | None = credential_dependency):
        value = credentials.credentials if credentials else ""
        if not (matches(value, read_token) or matches(value, admin_token)):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})

    def administrator(credentials: HTTPAuthorizationCredentials | None = credential_dependency):
        value = credentials.credentials if credentials else ""
        if not matches(value, admin_token):
            raise HTTPException(403, "admin_token_required")

    @app.middleware("http")
    async def mcp_auth_and_headers(request: Request, call_next):
        if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            header = request.headers.get("authorization", "")
            token = header[7:] if header.lower().startswith("bearer ") else ""
            if not (matches(token, read_token) or matches(token, admin_token)):
                return error_response(
                    "unauthorized",
                    401,
                    headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
                )
        # Bound even chunked request bodies before parsing JSON or forwarding to MCP.
        if request.method in {"POST", "PUT", "PATCH"}:
            size = 0
            parts = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 12_000_000:
                    return error_response("request_too_large", 413)
                parts.append(chunk)
            request._body = b"".join(parts)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HubError)
    async def hub_error(request, exc):
        code = str(exc)
        status = (
            409 if code == "policy_version_conflict" else 404 if code.endswith("not_found") else 422
        )
        return error_response(code, status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(str(exc.detail), exc.status_code, headers=exc.headers)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return error_response("storage_unavailable", 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(
            "validation_error", 422, [".".join(map(str, error["loc"])) for error in exc.errors()]
        )

    read = [Depends(authorized)]
    admin = [Depends(administrator)]

    @app.get("/healthz")
    def health():
        return {"status": "ok", "api_version": "1.2.0"}

    @app.get("/api/sync", dependencies=read, response_model=SyncStatus)
    def synchronization_status():
        from .hub_sync import sync_status

        return sync_status(hub)

    @app.get("/api/sync/files", dependencies=read, response_model=SyncFilesResponse)
    def synchronization_files(
        state: Literal["ok", "settling", "retrying", "failed", "missing"] | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        from .hub_sync import sync_files

        return sync_files(hub, state=state, limit=limit, offset=offset)

    @app.get("/api/retrieval", dependencies=read, response_model=RetrievalStatus)
    def retrieval_status():
        from .hub_semantic import index_status

        return index_status(hub)

    @app.get("/api/status", dependencies=read, response_model=StatusResponse)
    def status():
        return hub.stats()

    @app.get("/api/sources", dependencies=read, response_model=SourcesResponse)
    def sources():
        return {"sources": hub.sources()}

    @app.post("/api/sources", dependencies=admin, response_model=SourceDefinition)
    def source(body: SourceDefinition):
        return hub.upsert_source(body)

    @app.post("/api/ingest/documents", dependencies=admin, response_model=IngestResponse)
    def ingest(body: IngestRequest):
        return hub.ingest(body)

    @app.post("/api/search", dependencies=read, response_model=SearchResponse)
    def search(body: SearchRequest):
        return hub.search(body)

    @app.get("/api/documents/{document_id}", dependencies=read, response_model=MCPFetchResult)
    def fetch(document_id: str):
        return hub.fetch(document_id)

    @app.delete("/api/documents/{document_id}", dependencies=admin, response_model=DeleteResponse)
    def delete(document_id: str):
        return hub.delete_document(document_id)

    @app.get("/api/policy", dependencies=read, response_model=PolicyState)
    def policy():
        return hub.policy()

    @app.put("/api/policy", dependencies=admin, response_model=PolicyState)
    def update_policy(body: PolicyUpdate):
        return hub.update_policy(body)

    # Mount last so /api and /docs retain their routes. MCP remains exactly /mcp.
    app.mount("/", mcp_app)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    return app
