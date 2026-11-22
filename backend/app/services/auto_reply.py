"""auto-reply engine

Checks the newest inbound text message against the owner's enabled rules and
sends one quote-reply when a rule matches. Guard rails keep it from replying to
itself (mirrors re-ingest outgoing traffic), from flooding a conversation, and
from running when the platform account is not connected.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import AutoReplyRule, Direction, FileRecord, Message, User
from ..platform.registry import AdapterRegistry
from .events import EventBus
from .files import read_stored_bytes
from .send import send_file as send_file_message
from .send import send_text

logger = logging.getLogger(__name__)

ECHO_WINDOW = timedelta(minutes=10)
PLATFORM_LABELS = {"feishu": "飞书", "dingtalk": "钉钉", "wecom": "企业微信"}


def render_template(text: str, *, rule: AutoReplyRule, message: Message) -> str:
    """Substitute the small variable set allowed in reply templates."""
    sender = (message.sender_name or "").strip() or "你"
    return (
        text.replace("{{sender_name}}", sender)
        .replace("{{platform}}", PLATFORM_LABELS.get(message.platform, message.platform))
        .replace("{{rule_name}}", rule.name)
    )


def split_keywords(raw: str) -> list[str]:
    parts = raw.replace("，", ",").replace("、", ",").replace("\n", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def rule_matches(rule: AutoReplyRule, text: str) -> bool:
    if rule.match_type == "any":
        return True
    haystack = text.casefold()
    return any(keyword.casefold() in haystack for keyword in split_keywords(rule.keywords))


async def _recent_outbound_with_text(
    session: AsyncSession,
    message: Message,
    text: str,
) -> Message | None:
    """Detect our own message being re-ingested by a mirror/echo."""
    return await session.scalar(
        select(Message)
        .where(
            Message.owner_id == message.owner_id,
            Message.platform == message.platform,
            Message.platform_chat_id == message.platform_chat_id,
            Message.direction == Direction.OUTBOUND,
            Message.text == text,
            Message.created_at >= utcnow() - ECHO_WINDOW,
        )
        .limit(1)
    )


async def _within_cooldown(
    session: AsyncSession, rule: AutoReplyRule, message: Message
) -> bool:
    if rule.cooldown_seconds <= 0:
        return False
    cutoff = utcnow() - timedelta(seconds=rule.cooldown_seconds)
    recent = await session.scalar(
        select(Message.id)
        .where(
            Message.owner_id == message.owner_id,
            Message.platform == message.platform,
            Message.platform_chat_id == message.platform_chat_id,
            Message.auto_reply_rule_id == rule.id,
            Message.created_at >= cutoff,
        )
        .limit(1)
    )
    return recent is not None


async def _send_attachment(
    session: AsyncSession,
    *,
    settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    owner: User,
    message: Message,
    rule: AutoReplyRule,
    text: str,
) -> dict[str, Any] | None:
    """Send the rule's file with the rendered text as caption."""
    if not rule.attachment_file_id:
        return None
    record = await session.get(FileRecord, rule.attachment_file_id)
    if record is None or record.owner_id != owner.id:
        return None
    data = read_stored_bytes(settings, record)
    if not data:
        return None
    return await send_file_message(
        session,
        settings=settings,
        adapters=adapters,
        bus=bus,
        owner=owner,
        platform=message.platform,
        filename=record.filename,
        mime_type=record.mime_type,
        data=data,
        text=text,
        to_handle=message.platform_chat_id,
        client_msg_id=f"auto:{rule.id}:{message.id}",
        chat_id=message.platform_chat_id,
        chat_type=message.chat_type,
    )


async def maybe_auto_reply(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry | None,
    bus: EventBus,
    owner_id: str,
    message: Message,
) -> dict[str, Any] | None:
    if not settings.auto_reply_enabled or adapters is None:
        return None
    if message.direction != Direction.INBOUND:
        return None
    if message.content_type not in {"text", ""}:
        return None
    text = (message.text or "").strip()
    if not text:
        return None
    owner = await session.get(User, owner_id)
    if owner is None:
        return None
    # Never answer ourselves: mirrors turn our own outgoing messages into
    # inbound rows when the platform reports them back.
    if message.sender_user_id and message.sender_user_id == owner.id:
        return None
    if await _recent_outbound_with_text(session, message, text) is not None:
        return None

    rules = list(
        (
            await session.scalars(
                select(AutoReplyRule)
                .where(
                    AutoReplyRule.owner_id == owner_id,
                    AutoReplyRule.enabled.is_(True),
                )
                .order_by(AutoReplyRule.priority.asc(), AutoReplyRule.created_at.asc())
            )
        ).all()
    )
    for rule in rules:
        if rule.platform and rule.platform != message.platform:
            continue
        if not rule_matches(rule, text):
            continue
        if await _within_cooldown(session, rule, message):
            continue
        reply_text = render_template(rule.reply_text, rule=rule, message=message)
        try:
            if rule.attachment_file_id:
                payload = await _send_attachment(
                    session,
                    settings=settings,
                    adapters=adapters,
                    bus=bus,
                    owner=owner,
                    message=message,
                    rule=rule,
                    text=reply_text,
                )
                if payload is None:
                    # Attachment missing/unreadable: fall back to the text reply.
                    payload = await send_text(
                        session,
                        settings=settings,
                        adapters=adapters,
                        bus=bus,
                        owner=owner,
                        platform=message.platform,
                        text=reply_text,
                        reply_to_message_id=message.id,
                        client_msg_id=f"auto:{rule.id}:{message.id}",
                    )
            else:
                payload = await send_text(
                    session,
                    settings=settings,
                    adapters=adapters,
                    bus=bus,
                    owner=owner,
                    platform=message.platform,
                    text=reply_text,
                    reply_to_message_id=message.id,
                    client_msg_id=f"auto:{rule.id}:{message.id}",
                )
        except Exception:  # noqa: BLE001 - an auto reply must never break ingest
            logger.warning("auto reply failed for rule %s", rule.id, exc_info=True)
            return None
        if payload.get("delivery_status") != "sent":
            logger.info("auto reply not delivered for rule %s: %s", rule.id, payload.get("error"))
            return None
        row = await session.get(Message, payload["id"])
        if row is not None:
            row.auto_reply_rule_id = rule.id
        rule.hit_count += 1
        rule.last_hit_at = utcnow()
        await session.commit()
        await bus.publish(
            "auto_reply.sent",
            {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "message_id": payload["id"],
                "platform": message.platform,
                "chat_id": message.platform_chat_id,
            },
        )
        logger.info("auto reply sent rule=%s platform=%s", rule.id, message.platform)
        return {"rule_id": rule.id, "message_id": payload["id"]}
    return None
