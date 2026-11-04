import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, draws, evaluations, finance, groups, matches, notifications
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import register_error_handlers
from app.services.groups import seed_default_skills
from app.services.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        seed_default_skills(db)
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(stop)) if settings.scheduler_enabled else None
    try:
        yield
    finally:
        stop.set()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "API do Pelada+: organização de peladas, confirmação e lista de espera, perfis esportivos com "
            "Tier, avaliações pós-jogo, controle financeiro e sorteio inteligente de times."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    for module in (auth, groups, matches, evaluations, finance, draws, notifications, admin):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/health", tags=["Infra"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
