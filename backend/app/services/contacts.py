from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PlatformAccount, User
from ..platform.base import PlatformError
from .accounts import context_for_user
from .payloads import contact_payload


async def list_contacts(
    session: AsyncSession,
    *,
    q: str | None = None,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(User).where(User.is_placeholder.is_(False))
    if q:
        like = f"%{q}%"
        stmt = stmt.where((User.display_name.ilike(like)) | (User.email.ilike(like)))
    users = (await session.scalars(stmt.order_by(User.display_name.asc()))).all()
    result: list[dict[str, Any]] = []
    for user in users:
        accounts = list(user.accounts)
        if platform and not any(account.platform == platform for account in accounts):
            continue
        result.append(contact_payload(user, accounts))
    return result


async def list_platform_accounts(session: AsyncSession, user_id: str) -> list[PlatformAccount]:
    return list(
        (
            await session.scalars(
                select(PlatformAccount).where(PlatformAccount.user_id == user_id)
            )
        ).all()
    )


async def find_by_platform_handle(session: AsyncSession, platform: str, handle: str) -> User | None:
    account = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.platform_user_id == handle,
        )
    )
    if account is None:
        return None
    return await session.get(User, account.user_id)


async def sync_feishu_contacts(
    session: AsyncSession, *, settings, adapters, owner_id: str
) -> dict[str, int]:
    adapter = adapters.get("feishu")
    if not hasattr(adapter, "list_directory_users"):
        raise PlatformError("飞书适配器不支持通讯录同步", code="feishu_directory_unsupported")
    context = await context_for_user(session, owner_id, "feishu", settings)
    directory = await adapter.list_directory_users(context)
    synced = 0
    skipped = 0
    for item in directory:
        open_id = str(item.get("open_id") or "").strip()
        if not open_id:
            skipped += 1
            continue
        name = str(item.get("name") or item.get("en_name") or "飞书联系人").strip()
        union_id = item.get("union_id") or None
        email = (item.get("email") or "").strip() or None
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "feishu",
                PlatformAccount.platform_user_id == open_id,
            )
        )
        if account is None:
            user = None
            if email:
                user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name=name, is_placeholder=False)
                session.add(user)
                await session.flush()
            session.add(
                PlatformAccount(
                    user_id=user.id,
                    platform="feishu",
                    platform_user_id=open_id,
                    union_id=union_id,
                    display_name=name,
                    email=email,
                    status="connected",
                )
            )
        elif name:
            account.display_name = name
            if union_id:
                account.union_id = union_id
        synced += 1
    await session.commit()
    return {"synced": synced, "skipped": skipped, "total": len(directory)}


async def _run_dws_json(settings, args: list[str]) -> dict[str, Any]:
    command = settings.dingtalk_dws_command
    env = os.environ.copy()
    if settings.dingtalk_dws_config_dir:
        env["DWS_CONFIG_DIR"] = settings.dingtalk_dws_config_dir
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
    except FileNotFoundError as exc:
        raise PlatformError(
            f"找不到 dws 命令：{command}", code="dingtalk_dws_missing"
        ) from exc
    except asyncio.TimeoutError as exc:
        raise PlatformError("DWS 通讯录查询超时", code="dingtalk_dws_timeout") from exc
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "ignore").strip() or stdout.decode("utf-8", "ignore").strip()
        raise PlatformError(f"DWS 通讯录查询失败: {detail[:400]}", code="dingtalk_dws_contacts")
    try:
        return json.loads(stdout.decode("utf-8", "ignore").strip() or "{}")
    except ValueError as exc:
        raise PlatformError("DWS 通讯录返回非 JSON", code="dingtalk_dws_bad_json") from exc


async def resolve_dingtalk_conversation(settings, name: str) -> str:
    """Resolve the personal DWS conversation id for a DingTalk contact name."""
    wanted = name.strip()
    if not wanted:
        return ""
    payload = await _run_dws_json(
        settings,
        ["chat", "+conversation-list", "--page-all", "--format", "json"],
    )
    for item in payload.get("conversations") or []:
        if str(item.get("conversationName") or "").strip() == wanted:
            return str(item.get("openConversationId") or "")
    return ""


async def sync_wecom_contacts(
    session: AsyncSession, *, settings, owner_id: str, adapters
) -> dict[str, int]:
    try:
        context = await context_for_user(session, owner_id, "wecom", settings)
        directory = await adapters.get("wecom").list_directory_users(context)
    except PlatformError:
        raise
    synced = 0
    skipped = 0
    for item in directory:
        user_id = str(item.get("open_id") or "").strip()
        if not user_id:
            skipped += 1
            continue
        name = str(item.get("name") or "企微联系人").strip()
        email = (item.get("email") or "").strip() or None
        union_id = item.get("union_id") or None
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "wecom",
                PlatformAccount.platform_user_id == user_id,
            )
        )
        if account is None:
            user = None
            if email:
                user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name=name, is_placeholder=False)
                session.add(user)
                await session.flush()
            session.add(
                PlatformAccount(
                    user_id=user.id,
                    platform="wecom",
                    platform_user_id=user_id,
                    union_id=union_id,
                    display_name=name,
                    email=email,
                    status="connected",
                )
            )
        else:
            account.display_name = name
            if email:
                account.email = email
            if union_id:
                account.union_id = union_id
            user = await session.get(User, account.user_id)
            if user and not user.display_name:
                user.display_name = name
        synced += 1
    await session.commit()
    return {"synced": synced, "skipped": skipped, "total": len(directory), "mode": "api"}


async def sync_dingtalk_contacts(
    session: AsyncSession, *, settings, owner_id: str, adapters=None
) -> dict[str, int]:
    """Sync DingTalk directory, preferring the full app API when authorized."""
    directory: list[dict[str, Any]] = []
    if adapters is not None:
        try:
            context = await context_for_user(session, owner_id, "dingtalk", settings)
            directory = await adapters.get("dingtalk").list_directory_users(context)
        except PlatformError:
            directory = []
    if directory:
        synced = 0
        skipped = 0
        for item in directory:
            user_id = str(item.get("open_id") or item.get("user_id") or "").strip()
            if not user_id:
                skipped += 1
                continue
            name = str(item.get("name") or "钉钉联系人").strip()
            email = (item.get("email") or "").strip() or None
            union_id = item.get("union_id") or None
            account = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.platform == "dingtalk",
                    PlatformAccount.platform_user_id == user_id,
                )
            )
            if account is None:
                user = None
                if email:
                    user = await session.scalar(select(User).where(User.email == email))
                if user is None:
                    user = User(email=email, display_name=name, is_placeholder=False)
                    session.add(user)
                    await session.flush()
                session.add(
                    PlatformAccount(
                        user_id=user.id,
                        platform="dingtalk",
                        platform_user_id=user_id,
                        union_id=union_id,
                        display_name=name,
                        email=email,
                        status="connected",
                    )
                )
            else:
                account.display_name = name
                if email:
                    account.email = email
                if union_id:
                    account.union_id = union_id
                user = await session.get(User, account.user_id)
                if user and not user.display_name:
                    user.display_name = name
            synced += 1
        await session.commit()
        return {"synced": synced, "skipped": skipped, "total": len(directory), "mode": "api"}

    payload = await _run_dws_json(
        settings,
        ["contact", "dept", "list-members", "--ids", "1", "--format", "json"],
    )
    users = payload.get("deptUserList") or []
    synced = 0
    skipped = 0
    for item in users:
        info = item.get("userInfo") or item
        user_id = str(info.get("userid") or info.get("userId") or "").strip()
        if not user_id:
            skipped += 1
            continue
        name = str(info.get("name") or "钉钉联系人").strip()
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "dingtalk",
                PlatformAccount.platform_user_id == user_id,
            )
        )
        if account is None:
            user = User(display_name=name, is_placeholder=False)
            session.add(user)
            await session.flush()
            session.add(
                PlatformAccount(
                    user_id=user.id,
                    platform="dingtalk",
                    platform_user_id=user_id,
                    display_name=name,
                    status="connected",
                )
            )
        else:
            account.display_name = name
            user = await session.get(User, account.user_id)
            if user and not user.display_name:
                user.display_name = name
        synced += 1
    await session.commit()
    return {"synced": synced, "skipped": skipped, "total": len(users), "mode": "dws"}
