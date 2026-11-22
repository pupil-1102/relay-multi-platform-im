"""Platform credential management: DB overrides on top of environment settings.

The environment stays the deployment default; a row in ``platform_configs``
overrides it and is hot-applied to the running app. Secrets are encrypted with
the same key that protects platform tokens and are never returned by the API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import PlatformConfig
from ..security import decrypt_secret, encrypt_secret

PLATFORM_LABELS = {"feishu": "飞书", "dingtalk": "钉钉", "wecom": "企业微信"}
PLATFORM_ORDER = ("feishu", "dingtalk", "wecom")


class PlatformConfigError(Exception):
    pass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"  # text | secret | bool
    placeholder: str = ""
    hint: str = ""


PLATFORM_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "feishu": (
        FieldSpec("feishu_app_id", "App ID", placeholder="cli_xxxxxxxxxxxxxxxx"),
        FieldSpec("feishu_app_secret", "App Secret", kind="secret", placeholder="应用的 App Secret"),
        FieldSpec("feishu_redirect_uri", "OAuth 回调地址", placeholder="http://127.0.0.1:8000/api/auth/feishu/callback"),
        FieldSpec("feishu_user_mirror_enabled", "同步本人聊天", kind="bool", hint="用 user token 轮询本人会话，把私聊镜像进 Relay"),
    ),
    "dingtalk": (
        FieldSpec("dingtalk_client_id", "Client ID (AppKey)", placeholder="dingxxxxxxxxxxxxxxxx"),
        FieldSpec("dingtalk_client_secret", "Client Secret", kind="secret", placeholder="应用的 Client Secret"),
        FieldSpec("dingtalk_robot_code", "机器人 Code", placeholder="留空则使用 Client ID"),
        FieldSpec("dingtalk_redirect_uri", "OAuth 回调地址", placeholder="http://127.0.0.1:8000/api/auth/dingtalk/callback"),
        FieldSpec("dingtalk_dws_enabled", "启用 DWS 个人事件", kind="bool", hint="需要宿主机已登录 dws；用于待办事件回流"),
        FieldSpec("dingtalk_user_reply_enabled", "单聊以本人身份回复", kind="bool", hint="开启后单聊引用回复走 dws 用户态"),
    ),
    "wecom": (
        FieldSpec("wecom_corp_id", "企业 CorpID", placeholder="wwxxxxxxxxxxxxxxxx"),
        FieldSpec("wecom_agent_id", "应用 AgentId", placeholder="1000002"),
        FieldSpec("wecom_secret", "应用 Secret", kind="secret", placeholder="自建应用的 Secret"),
        FieldSpec("wecom_aibot_id", "智能机器人 Bot ID", placeholder="aibxxxxxxxx"),
        FieldSpec("wecom_aibot_secret", "智能机器人 Secret", kind="secret", placeholder="机器人 Secret"),
        FieldSpec("wecom_redirect_uri", "OAuth 回调地址", placeholder="http://127.0.0.1:8000/api/auth/wecom/callback"),
        FieldSpec("wecom_aibot_only", "仅用智能机器人", kind="bool", hint="未认证/个人测试企业只能走 aibot，不能启用回调和通讯录"),
    ),
}

REQUIRED_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "feishu": (("feishu_app_id", "App ID"), ("feishu_app_secret", "App Secret")),
    "dingtalk": (("dingtalk_client_id", "Client ID"), ("dingtalk_client_secret", "Client Secret")),
}


def spec_for(platform: str) -> tuple[FieldSpec, ...]:
    try:
        return PLATFORM_SPECS[platform]
    except KeyError as exc:  # pragma: no cover - guarded by callers/API
        raise PlatformConfigError(f"不支持的平台：{platform}") from exc


def _secret_fields(platform: str) -> set[str]:
    return {spec.key for spec in spec_for(platform) if spec.kind == "secret"}


def _row_values(row: PlatformConfig | None, settings: Settings) -> dict[str, Any]:
    if row is None or not row.config_enc:
        return {}
    raw = decrypt_secret(row.config_enc, settings)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def apply_to_settings(
    settings: Settings, platform: str, values: dict[str, Any], enabled: bool
) -> None:
    for spec in spec_for(platform):
        if spec.key not in values:
            continue
        value = values[spec.key]
        if spec.kind == "bool":
            setattr(settings, spec.key, bool(value))
        else:
            setattr(settings, spec.key, str(value or "").strip())
    setattr(settings, f"{platform}_enabled", bool(enabled))


async def load_platform_configs(session: AsyncSession, settings: Settings) -> list[str]:
    """Apply every stored override; returns the platforms that were overridden."""
    rows = (await session.scalars(select(PlatformConfig))).all()
    applied: list[str] = []
    for row in rows:
        if row.platform not in PLATFORM_SPECS:
            continue
        apply_to_settings(settings, row.platform, _row_values(row, settings), row.enabled)
        applied.append(row.platform)
    return applied


def _required_missing(
    platform: str, *, enabled: bool, get: Callable[[str], Any]
) -> list[str]:
    if not enabled:
        return []
    if platform == "wecom":
        if bool(get("wecom_aibot_only")):
            needed = (("wecom_aibot_id", "智能机器人 Bot ID"), ("wecom_aibot_secret", "智能机器人 Secret"))
        else:
            needed = (("wecom_corp_id", "企业 CorpID"), ("wecom_secret", "应用 Secret"))
    else:
        needed = REQUIRED_FIELDS.get(platform, ())
    return [label for key, label in needed if not str(get(key) or "").strip()]


def _configured(platform: str, get: Callable[[str], Any]) -> bool:
    if platform == "feishu":
        return bool(str(get("feishu_app_id") or "").strip() and str(get("feishu_app_secret") or "").strip())
    if platform == "dingtalk":
        return bool(
            str(get("dingtalk_client_id") or "").strip()
            and str(get("dingtalk_client_secret") or "").strip()
        )
    corp = str(get("wecom_corp_id") or "").strip() and str(get("wecom_secret") or "").strip()
    aibot = str(get("wecom_aibot_id") or "").strip() and str(get("wecom_aibot_secret") or "").strip()
    return bool(corp or aibot)


async def platform_config_payload(
    session: AsyncSession, settings: Settings
) -> list[dict[str, Any]]:
    rows = {row.platform: row for row in (await session.scalars(select(PlatformConfig))).all()}
    items: list[dict[str, Any]] = []
    for platform in PLATFORM_ORDER:
        row = rows.get(platform)
        fields: list[dict[str, Any]] = []
        for spec in spec_for(platform):
            current = getattr(settings, spec.key, "" if spec.kind != "bool" else False)
            if spec.kind == "bool":
                fields.append(
                    {
                        "key": spec.key,
                        "label": spec.label,
                        "kind": "bool",
                        "value": bool(current),
                        "hint": spec.hint,
                    }
                )
            elif spec.kind == "secret":
                fields.append(
                    {
                        "key": spec.key,
                        "label": spec.label,
                        "kind": "secret",
                        "value": "",
                        "secret_set": bool(str(current or "").strip()),
                        "placeholder": "已配置，留空保持不变" if current else spec.placeholder,
                        "hint": spec.hint,
                    }
                )
            else:
                fields.append(
                    {
                        "key": spec.key,
                        "label": spec.label,
                        "kind": "text",
                        "value": str(current or ""),
                        "placeholder": spec.placeholder,
                        "hint": spec.hint,
                    }
                )
        items.append(
            {
                "platform": platform,
                "label": PLATFORM_LABELS[platform],
                "enabled": bool(getattr(settings, f"{platform}_enabled", False)),
                "configured": _configured(platform, lambda key: getattr(settings, key, "")),
                "source": "database" if row is not None else "environment",
                "updated_at": row.updated_at.isoformat() if row is not None else None,
                "fields": fields,
            }
        )
    return items


async def save_platform_config(
    session: AsyncSession,
    settings: Settings,
    platform: str,
    *,
    enabled: bool,
    values: dict[str, Any],
    updated_by: str | None = None,
) -> dict[str, Any]:
    if platform not in PLATFORM_SPECS:
        raise PlatformConfigError(f"不支持的平台：{platform}")
    row = await session.get(PlatformConfig, platform)
    stored = _row_values(row, settings)
    incoming: dict[str, Any] = {}
    for spec in spec_for(platform):
        if spec.key not in values:
            continue
        raw = values[spec.key]
        if spec.kind == "secret":
            text = str(raw or "").strip()
            if not text:
                # Blank means "keep the existing secret".
                continue
            incoming[spec.key] = text
        elif spec.kind == "bool":
            incoming[spec.key] = bool(raw)
        else:
            incoming[spec.key] = str(raw or "").strip()

    merged = {**stored, **incoming}

    def effective(key: str) -> Any:
        return merged.get(key, getattr(settings, key, ""))

    missing = _required_missing(platform, enabled=enabled, get=effective)
    if missing:
        raise PlatformConfigError(f"启用{PLATFORM_LABELS[platform]}前请先填写：{'、'.join(missing)}")

    apply_to_settings(settings, platform, merged, enabled)

    if row is None:
        row = PlatformConfig(platform=platform)
        session.add(row)
    row.enabled = bool(enabled)
    row.config_enc = encrypt_secret(json.dumps(merged, ensure_ascii=False), settings) if merged else None
    row.updated_by = updated_by
    await session.commit()
    await session.refresh(row)
    return {"platform": platform, "enabled": bool(enabled), "source": "database"}


async def reset_platform_config(
    session: AsyncSession,
    settings: Settings,
    baseline: Settings,
    platform: str,
) -> None:
    """Drop the DB override and restore the environment-provided values."""
    if platform not in PLATFORM_SPECS:
        raise PlatformConfigError(f"不支持的平台：{platform}")
    row = await session.get(PlatformConfig, platform)
    if row is not None:
        await session.delete(row)
        await session.commit()
    for spec in spec_for(platform):
        setattr(settings, spec.key, getattr(baseline, spec.key))
    setattr(settings, f"{platform}_enabled", getattr(baseline, f"{platform}_enabled", False))
