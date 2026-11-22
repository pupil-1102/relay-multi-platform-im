from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from backend.app.config import Settings
from backend.app.main import create_app


@pytest_asyncio.fixture
async def app(tmp_path: Path):
    settings = Settings(
        # Hermetic tests: never inherit the developer's .env (which points at
        # real tenants and enables real channels).
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path,
        file_dir=tmp_path / "files",
        frontend_dist=tmp_path / "dist",
        seed_demo=True,
        demo_mode=True,
        dev_login=True,
        mock_latency_ms=0,
    )
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://relay.test") as http:
        yield http


@pytest_asyncio.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    response = await client.post("/api/auth/dev-login")
    assert response.status_code == 200, response.text
    return client
