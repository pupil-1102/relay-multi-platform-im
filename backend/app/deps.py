from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import User
from .platform.registry import AdapterRegistry
from .services.auth import require_user
from .services.events import EventBus


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_adapters(request: Request) -> AdapterRegistry:
    return request.app.state.adapters


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
AdaptersDep = Annotated[AdapterRegistry, Depends(get_adapters)]
BusDep = Annotated[EventBus, Depends(get_bus)]
CurrentUser = Annotated[User, Depends(require_user)]
