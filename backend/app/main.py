"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.operational import router as operational_router
from app.api.po import router as po_router
from app.api.scheduling import router as scheduling_router
from app.api.table_preferences import router as table_preferences_router
from app.api.workflow import router as workflow_router
from app.auth.routes import router as auth_router
from app.config import get_settings
from app.db import close_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Validate config at startup (fail fast). Does NOT bootstrap indexes/admin.
    get_settings()
    yield
    close_client()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Eternal Production V4",
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(table_preferences_router)
    application.include_router(po_router)
    application.include_router(operational_router)
    application.include_router(workflow_router)
    application.include_router(scheduling_router)
    return application


app = create_app()
