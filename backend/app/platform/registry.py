from __future__ import annotations

from .base import PlatformAdapter


class AdapterRegistry:
    def __init__(self, adapters: dict[str, PlatformAdapter], *, demo_mode: bool) -> None:
        self._adapters = adapters
        self.demo_mode = demo_mode

    def get(self, platform: str) -> PlatformAdapter:
        adapter = self._adapters.get(platform)
        if adapter is None:
            raise KeyError(f"Unsupported platform: {platform}")
        return adapter

    @property
    def platforms(self) -> list[str]:
        return list(self._adapters)

    async def start(self) -> None:
        for adapter in self._adapters.values():
            await adapter.start()

    async def stop(self) -> None:
        for adapter in self._adapters.values():
            await adapter.stop()
