"""Hot-reload the platform adapters, runners and realtime mirrors.

Called after platform credentials change so a saved configuration takes effect
without restarting the process.
"""
from __future__ import annotations

import logging

from ..platform import build_registry
from .realtime import RealtimeManager, find_any_owner, find_connected_owners
from .runners import RunnerManager

logger = logging.getLogger(__name__)


async def reload_channels(app) -> None:  # noqa: ANN001 - FastAPI app instance
    settings = app.state.settings
    db = app.state.db
    bus = app.state.bus

    for manager_attr in ("runners", "realtime"):
        manager = getattr(app.state, manager_attr, None)
        if manager is None:
            continue
        try:
            await manager.stop()
        except Exception:  # noqa: BLE001 - a stuck channel must not block the save
            logger.warning("failed to stop %s during reload", manager_attr, exc_info=True)

    adapters = build_registry(settings)
    await adapters.start()
    app.state.adapters = adapters
    app.state.realtime = RealtimeManager(settings, db, adapters, bus)
    app.state.runners = RunnerManager(settings, db, adapters, bus)

    if settings.enable_real_channels and not settings.demo_mode:
        try:
            if settings.multi_user_channels:
                owners = await find_connected_owners(db)
                for owner_id in owners:
                    await app.state.realtime.start(owner_id)
                if owners:
                    await app.state.runners.start(owners[0])
            else:
                owner_id = await find_any_owner(db)
                await app.state.realtime.start(owner_id)
                await app.state.runners.start(owner_id)
        except Exception:  # noqa: BLE001 - report through /api/platforms/status
            logger.warning("failed to restart channels after reload", exc_info=True)
    logger.info("platform channels reloaded")
