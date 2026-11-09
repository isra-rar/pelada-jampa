import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.deps import block_writes_in_suspended_group
from app.api.routes import (
    admin,
    auth,
    discover,
    draws,
    evaluations,
    finance,
    groups,
    matches,
    notifications,
    pros,
    push,
    stats,
    venues,
)
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import register_error_handlers
from app.services.groups import seed_default_skills
from app.services.media import media_root
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
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],  # nome do arquivo nas exportações (CSV)
    )
    register_error_handlers(app)
    group_scoped = {groups, matches, evaluations, finance, draws, stats, venues, pros}  # grupo suspenso: só leitura
    for module in (
        auth, groups, matches, evaluations, finance, draws, stats, venues, discover, pros, notifications, push,
        admin,
    ):
        guard = [Depends(block_writes_in_suspended_group)] if module in group_scoped else []
        app.include_router(module.router, prefix=API_PREFIX, dependencies=guard)
    app.include_router(stats.public_router, prefix=API_PREFIX)
    app.include_router(discover.public_router, prefix=API_PREFIX)
    app.include_router(pros.public_router, prefix=API_PREFIX)
    # fotos enviadas pelos usuários (o tipo é validado pelo conteúdo no upload)
    app.mount("/media", StaticFiles(directory=media_root()), name="media")

    @app.get("/health", tags=["Infra"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
