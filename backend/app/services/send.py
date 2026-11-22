from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import Direction, FileRecord, FileStatus, Message, Platform, ProcessedEvent, User
from ..platform.base import AccountContext, MessageRef, OutboundFile, PlatformError
from ..platform.registry import AdapterRegistry
from .accounts import context_for_user
from .events import EventBus
from .files import save_bytes
from .payloads import file_payload, message_payload
from .resilience import platform_gate


async def _resolve_handle(
    session: AsyncSession,
    *,
    owner: User,
    platform: str,
    to_user_id: str | None,
    to_handle: str | None,
) -> str:
    if to_handle:
        return to_handle
    if not to_user_id:
        raise ValueError("缺少收件人")
    from ..models import PlatformAccount

    account = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.user_id == to_user_id,
            PlatformAccount.platform == platform,
        )
    )
    if account is None:
        raise ValueError("该联系人没有绑定目标平台账号")
    return account.platform_user_id


async def _send_context(
    session: AsyncSession,
    owner: User,
    platform: str,
    settings: Settings,
    *,
    need_user_token: bool,
) -> AccountContext:
    try:
        return await context_for_user(session, owner.id, platform, settings)
    except PlatformError as exc:
        if need_user_token or exc.code != "account_missing":
            raise
        # Bot/tenant APIs don't need the operator's platform account. This is
        # important for groups before DingTalk/WeCom OAuth has been completed.
        return AccountContext(
            platform=platform,
            user_id=owner.id,
            platform_user_id="",
            union_id=None,
            display_name=owner.display_name,
            access_token=None,
        )


async def send_text(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    owner: User,
    platform: str,
    text: str,
    to_user_id: str | None = None,
    to_handle: str | None = None,
    client_msg_id: str | None = None,
    reply_to_message_id: str | None = None,
    chat_id: str | None = None,
    chat_type: str | None = None,
    mentions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("消息内容不能为空")
    if platform not in {Platform.FEISHU, Platform.DINGTALK, Platform.WECOM}:
        raise ValueError("不支持的平台")

    client_msg_id = client_msg_id or uuid.uuid4().hex
    wire_text = text
    if platform == "feishu" and mentions:
        from html import escape

        for mention in mentions:
            mention_id = str(mention.get("id") or "").strip()
            mention_name = str(mention.get("name") or "").strip()
            if not mention_id or not mention_name:
                continue
            marker = f"@{mention_name}"
            if marker in wire_text:
                wire_text = wire_text.replace(
                    marker,
                    f'<at user_id="{escape(mention_id, quote=True)}">{escape(mention_name)}</at>',
                    1,
                )
    existing = await session.scalar(
        select(Message).where(Message.owner_id == owner.id, Message.client_msg_id == client_msg_id)
    )
    if existing:
        return message_payload(existing)

    source: Message | None = None
    if reply_to_message_id:
        source = await session.get(Message, reply_to_message_id)
        if source is None or source.owner_id != owner.id:
            raise ValueError("被回复的消息不存在")

    if source is not None:
        handle = source.platform_chat_id or source.sender_platform_id or ""
        if not handle:
            raise ValueError("原消息缺少会话上下文，无法回复")
    else:
        handle = await _resolve_handle(
            session, owner=owner, platform=platform, to_user_id=to_user_id, to_handle=to_handle
        )
    adapter = adapters.get(platform)
    if source is not None:
        chat_id = source.platform_chat_id or chat_id
        chat_type = source.chat_type or chat_type
    elif chat_id is None and (handle.startswith("oc_") or handle.startswith("cid") or handle.startswith("$")):
        chat_id = handle
    chat_type = chat_type or ("group" if handle.startswith(("cid", "$")) else ("direct" if handle.startswith("ou_") else "group"))
    if chat_id is None and chat_type == "direct":
        # WeCom only returns a msgid and Feishu may omit chat_id, but a direct
        # chat still needs a stable conversation key. The peer handle is that
        # key (WeCom userid / Feishu open_id or email). Without it the message is
        # delivered yet never shows up in the inbox.
        chat_id = handle
    if platform == "dingtalk" and chat_type == "direct" and chat_id and not chat_id.startswith("cid"):
        raise PlatformError(
            "钉钉单聊必须先解析真实会话 ID（cid…）。请确认 dws 已登录后重试，"
            "或在钉钉客户端先给对方发一条消息，让会话出现在 dws 会话列表里。",
            code="dingtalk_direct_chat_id_missing",
        )
    need_user_token = chat_type == "direct" and hasattr(adapter, "send_text_as_user")
    account = await _send_context(
        session, owner, platform, settings, need_user_token=need_user_token
    )

    message = Message(
        owner_id=owner.id,
        platform=platform,
        direction=Direction.OUTBOUND,
        platform_chat_id=chat_id,
        chat_type=chat_type,
        receiver_platform_id=handle,
        sender_name=owner.display_name or "我",
        content_type="text",
        text=text,
        client_msg_id=client_msg_id,
        reply_to_message_id=source.id if source else None,
        reply_to_platform_msg_id=source.platform_msg_id if source else None,
        delivery_status="pending",
        created_at=utcnow(),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    try:
        if source and source.platform_msg_id:
            try:
                source_raw = json.loads(source.raw_json) if source.raw_json else {}
            except (TypeError, ValueError):
                source_raw = {}
            ref = MessageRef(
                id=source.id,
                platform=source.platform,
                platform_msg_id=source.platform_msg_id,
                chat_id=source.platform_chat_id,
                chat_type=source.chat_type,
                sender_platform_id=source.sender_platform_id,
                sender_name=source.sender_name,
                source_raw=source_raw if isinstance(source_raw, dict) else {},
            )
            result = await platform_gate.once(
                source.platform, lambda: adapter.reply_text(account, ref, wire_text)
            )
        else:
            adapter_handle = handle
            if (
                platform == "dingtalk"
                and chat_type == "direct"
                and not adapter_handle.startswith("cid")
            ):
                # DingTalk direct chats are keyed by a cid... conversation id,
                # but the bot API needs the peer's staff userId. Recover it from
                # the latest inbound message in this conversation. A cid target
                # already addresses the conversation, and the user-identity send
                # path requires the cid, so keep it untouched.
                peer = await session.scalar(
                    select(Message)
                    .where(
                        Message.owner_id == owner.id,
                        Message.platform == "dingtalk",
                        Message.platform_chat_id == chat_id,
                        Message.direction == "inbound",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if peer and peer.sender_platform_id:
                    adapter_handle = peer.sender_platform_id
            if chat_type == "direct" and hasattr(adapter, "send_text_as_user"):
                result = await platform_gate.once(
                    platform, lambda: adapter.send_text_as_user(account, adapter_handle, wire_text)
                )
            elif platform == "dingtalk" and chat_type == "group" and mentions:
                result = await platform_gate.once(
                    platform,
                    lambda: adapter.send_text(
                        account, adapter_handle, wire_text, mentions=mentions
                    ),
                )
            else:
                result = await platform_gate.once(
                    platform, lambda: adapter.send_text(account, adapter_handle, wire_text)
                )
        message.platform_msg_id = result.platform_msg_id or None
        result_chat_id = result.raw.get("chat_id") if result.raw else None
        if result_chat_id:
            message.platform_chat_id = result_chat_id
        message.delivery_status = "sent"
        message.error = None
    except PlatformError as exc:
        message.delivery_status = "failed"
        message.error = exc.message
    except Exception as exc:  # noqa: BLE001
        message.delivery_status = "failed"
        message.error = str(exc)

    await session.commit()
    await session.refresh(message)
    payload = message_payload(message)
    await bus.publish("message.created", payload)
    return payload


async def send_file(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    owner: User,
    platform: str,
    filename: str,
    mime_type: str,
    data: bytes,
    text: str | None = None,
    to_user_id: str | None = None,
    to_handle: str | None = None,
    client_msg_id: str | None = None,
    chat_id: str | None = None,
    chat_type: str | None = None,
) -> dict[str, Any]:
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > settings.max_file_bytes:
        raise ValueError(f"文件超过 {settings.max_file_mb}MB 上限")
    if platform not in {Platform.FEISHU, Platform.DINGTALK, Platform.WECOM}:
        raise ValueError("不支持的平台")

    client_msg_id = client_msg_id or uuid.uuid4().hex
    existing = await session.scalar(
        select(Message).where(Message.owner_id == owner.id, Message.client_msg_id == client_msg_id)
    )
    if existing:
        await session.refresh(existing, ["files"])
        return message_payload(existing, files=[file_payload(file, message=existing) for file in existing.files])

    handle = await _resolve_handle(
        session, owner=owner, platform=platform, to_user_id=to_user_id, to_handle=to_handle
    )
    adapter = adapters.get(platform)
    if not hasattr(adapter, "send_file"):
        raise ValueError("该平台暂不支持发送文件")
    if chat_id is None and (handle.startswith("oc_") or handle.startswith("cid") or handle.startswith("$")):
        chat_id = handle
    if chat_type is None and platform == Platform.FEISHU and chat_id:
        # An ``oc_`` id says nothing about p2p vs group, and the difference
        # decides whether Feishu needs the operator's token. Reuse whatever the
        # conversation already recorded instead of guessing "group".
        chat_type = await session.scalar(
            select(Message.chat_type)
            .where(
                Message.owner_id == owner.id,
                Message.platform == platform,
                Message.platform_chat_id == chat_id,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    chat_type = chat_type or ("group" if handle.startswith(("cid", "$")) else ("direct" if handle.startswith("ou_") else "group"))
    # A personal chat belongs to the operator, not to the bot. Feishu rejects
    # tenant-token sends there with 230002 "Bot/User can NOT be out of the
    # chat", so direct chats must upload and send as the operator. Demo mode
    # has no real platform account behind the owner, so it keeps the bot path.
    send_as_user = (
        platform == Platform.FEISHU
        and (chat_type or "direct") == "direct"
        and not settings.demo_mode
    )
    account = await _send_context(
        session, owner, platform, settings, need_user_token=send_as_user
    )
    adapter_handle = handle
    if platform == "dingtalk" and chat_type == "direct":
        peer = await session.scalar(
            select(Message)
            .where(
                Message.owner_id == owner.id,
                Message.platform == "dingtalk",
                Message.platform_chat_id == chat_id,
                Message.direction == "inbound",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        if peer and peer.sender_platform_id:
            adapter_handle = peer.sender_platform_id

    key, digest, size = save_bytes(settings, data, filename)
    message = Message(
        owner_id=owner.id,
        platform=platform,
        direction=Direction.OUTBOUND,
        platform_chat_id=chat_id,
        chat_type=chat_type or "direct",
        receiver_platform_id=handle,
        sender_name=owner.display_name or "我",
        content_type="image" if mime_type.startswith("image/") else "file",
        text=(text or "").strip(),
        client_msg_id=client_msg_id,
        delivery_status="pending",
        created_at=utcnow(),
    )
    session.add(message)
    await session.flush()
    record = FileRecord(
        owner_id=owner.id,
        message_id=message.id,
        platform=platform,
        filename=filename,
        mime_type=mime_type or "application/octet-stream",
        size_bytes=size,
        sha256=digest,
        storage_key=key,
        download_status=FileStatus.READY,
    )
    session.add(record)
    await session.commit()
    await session.refresh(message)
    await session.refresh(message, ["files"])

    try:
        try:
            result = await platform_gate.once(
                platform,
                lambda: adapter.send_file(
                    account,
                    adapter_handle,
                    OutboundFile(filename=filename, mime_type=mime_type, data=data),
                    text=(text or "").strip() or None,
                    as_user=send_as_user,
                ),
            )
        except PlatformError as exc:
            if not send_as_user or exc.code not in {
                "feishu_user_scope_missing",
                "feishu_user_token_missing",
            }:
                raise
            # Falling back keeps bot-hosted chats (the bot talks to the
            # operator) working while the operator scope is still missing.
            identity_error = exc
            try:
                result = await platform_gate.once(
                    platform,
                    lambda: adapter.send_file(
                        account,
                        adapter_handle,
                        OutboundFile(filename=filename, mime_type=mime_type, data=data),
                        text=(text or "").strip() or None,
                        as_user=False,
                    ),
                )
            except PlatformError as bot_exc:
                raise PlatformError(
                    f"{identity_error.message}（改用机器人身份发送也被拒绝：{bot_exc.message}）",
                    code="feishu_file_send_blocked",
                ) from bot_exc
        message.platform_msg_id = result.platform_msg_id or None
        message.delivery_status = "sent"
        message.error = None
        caption_id = (result.raw or {}).get("caption_message_id") if isinstance(result.raw, dict) else None
        if caption_id:
            event_id = f"sent:{caption_id}"
            known = await session.scalar(
                select(ProcessedEvent.id).where(
                    ProcessedEvent.platform == platform,
                    ProcessedEvent.event_id == event_id,
                )
            )
            if known is None:
                # Lets the user-token mirror skip the caption it will see in
                # the chat history; otherwise the bubble shows up twice.
                session.add(
                    ProcessedEvent(
                        platform=platform,
                        event_id=event_id,
                        event_type="outbound_text",
                        outcome="sent",
                    )
                )
    except PlatformError as exc:
        message.delivery_status = "failed"
        message.error = exc.message
    except Exception as exc:  # noqa: BLE001
        message.delivery_status = "failed"
        message.error = str(exc)

    await session.commit()
    await session.refresh(message)
    payload = message_payload(message, files=[file_payload(file, message=message) for file in message.files])
    await bus.publish("message.created", payload)
    return payload
