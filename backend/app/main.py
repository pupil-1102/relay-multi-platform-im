from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    auth,
    auto_reply,
    contacts,
    conversations,
    dev,
    files,
    health,
    messages,
    platforms,
    send,
    tasks,
    wecom,
    ws,
)
from .config import Settings, get_settings
from .db import Database
from .platform import build_registry
from .services.events import EventBus
from .services.platform_config import load_platform_configs
from .services.realtime import RealtimeManager, find_any_owner, find_connected_owners
from .services.runners import RunnerManager
from .services.seed import seed_demo

logger = logging.getLogger(__name__)


def _warn_about_unsafe_settings(settings: Settings) -> None:
    """Surface deployment mistakes that silently turn into auth bypasses."""
    if settings.dev_login and not settings.demo_mode:
        logger.warning(
            "DEV_LOGIN=true outside demo mode: POST /api/auth/dev-login hands out a "
            "session without any credential. Set DEV_LOGIN=false before exposing Relay."
        )
    if "change-me" in settings.secret_key or len(settings.secret_key) < 24:
        logger.warning(
            "SECRET_KEY looks like a placeholder: it signs session cookies and derives the "
            "key that encrypts stored platform tokens. Use a random 32+ byte value."
        )
    if settings.cookie_secure is False and settings.host not in {"127.0.0.1", "localhost"}:
        logger.warning("COOKIE_SECURE=false while HOST is not loopback: session cookies can leak over plain HTTP.")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    _warn_about_unsafe_settings(settings)
    # Snapshot the environment-provided values so "reset to defaults" can restore
    # them after the database overrides mutate ``settings`` in place.
    env_settings = settings.model_copy(deep=True)
    db = Database(settings)
    bus = EventBus()
    adapters = build_registry(settings)
    realtime = RealtimeManager(settings, db, adapters, bus)
    runners = RunnerManager(settings, db, adapters, bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.init()
        async with db.session() as session:
            await load_platform_configs(session, settings)
        active_adapters = build_registry(settings)
        await active_adapters.start()
        if settings.seed_demo:
            async with db.session() as session:
                await seed_demo(session, settings, active_adapters)
        active_realtime = RealtimeManager(settings, db, active_adapters, bus)
        active_runners = RunnerManager(settings, db, active_adapters, bus)
        app.state.settings = settings
        app.state.env_settings = env_settings
        app.state.db = db
        app.state.bus = bus
        app.state.adapters = active_adapters
        app.state.realtime = active_realtime
        app.state.runners = active_runners
        app.state.started = True
        if settings.enable_real_channels and not settings.demo_mode:
            if settings.multi_user_channels:
                owners = await find_connected_owners(db)
                for owner_id in owners:
                    await active_realtime.start(owner_id)
                if owners:
                    await active_runners.start(owners[0])
            else:
                owner_id = await find_any_owner(db)
                await active_realtime.start(owner_id)
                if owner_id:
                    await active_runners.start(owner_id)
        try:
            yield
        finally:
            await app.state.runners.stop()
            await app.state.realtime.stop()
            await app.state.adapters.stop()
            await db.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="多平台 IM 统一消息网关与业务闭环系统",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.bus = bus
    app.state.adapters = adapters
    app.state.realtime = realtime
    app.state.runners = runners

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        auth.router,
        conversations.router,
        messages.router,
        tasks.router,
        files.router,
        contacts.router,
        send.router,
        platforms.router,
        auto_reply.router,
        health.router,
        dev.router,
        wecom.router,
        ws.router,
    ):
        app.include_router(router)

    dist = Path(settings.frontend_dist)
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path.startswith("WW_verify_") and full_path.endswith(".txt") and "/" not in full_path:
            verify_file = Path(settings.data_dir) / "wecom-verify" / full_path
            if verify_file.is_file():
                return FileResponse(verify_file, media_type="text/plain")
        index = dist / "index.html"
        if index.exists():
            return FileResponse(
                index,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return JSONResponse(
            {
                "app": settings.app_name,
                "status": "backend running",
                "docs": "/docs",
                "frontend": "frontend/dist 尚未构建，请运行 npm --prefix frontend run build",
            }
        )

    return app


app = create_app()
