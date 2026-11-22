from __future__ import annotations

from ..config import Settings
from .base import PlatformAdapter
from .mock import new_mock_adapters
from .registry import AdapterRegistry


def build_registry(settings: Settings) -> AdapterRegistry:
    if settings.demo_mode:
        adapters: dict[str, PlatformAdapter] = dict(new_mock_adapters(settings.mock_latency_ms))
        return AdapterRegistry(adapters, demo_mode=True)

    from .dingtalk import DingTalkAdapter
    from .feishu import FeishuAdapter
    from .wecom import WeComAdapter

    adapters = {
        "feishu": FeishuAdapter(settings),
        "dingtalk": DingTalkAdapter(settings),
        "wecom": WeComAdapter(settings),
    }
    return AdapterRegistry(adapters, demo_mode=False)
