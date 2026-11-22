from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import AdaptersDep, BusDep, CurrentUser, SessionDep, SettingsDep
from ..db import utcnow
from ..models import FileView, Message, Task
from ..models import ConversationRead
from ..platform.base import PlatformError
from ..services.accounts import context_for_user
from ..services.payloads import file_payload, message_payload, task_brief
from ..services.send import send_file as send_file_message
from ..services.send import send_text

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class MentionIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


class ConversationSendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    client_msg_id: str | None = None
    reply_to_message_id: str | None = None
    mentions: list[MentionIn] = Field(default_factory=list)


class StartConversationRequest(BaseModel):
    platform: str = "feishu"
    contact_id: str | None = None
    handle: str | None = None
    display_name: str | None = None
    text: str = Field(default="你好", min_length=1, max_length=8000)
    client_msg_id: str | None = None


def _validate_start_handle(platform: str, handle: str) -> None:
    """Reject values Feishu cannot use as receive_id before calling the API."""
    if platform != "feishu":
        return
    value = handle.strip()
    is_open_id = value.startswith("ou_") and len(value) > 8
    is_chat_id = value.startswith("oc_") and len(value) > 8
    is_email = "@" in value and "." in value.rsplit("@", 1)[-1]
    if is_open_id or is_chat_id or is_email:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "飞书外部联系人不能用手机号直接发起会话。"
            "请填写 ou_ 开头的 open_id，或对方绑定的邮箱；"
            "手机号不是飞书接收 ID。"
        ),
    )


def _looks_like_mobile(value: str) -> bool:
    digits = "".join(ch for ch in value if ch.isdigit())
    return len(digits) == 11 and digits.startswith("1")


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.rsplit("@", 1)[-1]



def _key(platform: str, chat_id: str) -> str:
    raw = f"{platform}:{chat_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_key(value: str) -> tuple[str, str]:
    # New opaque base64url IDs are safe for chat IDs containing "/", "+", "=".
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        platform, sep, chat_id = raw.partition(":")
        if sep and platform in {"feishu", "dingtalk", "wecom"} and chat_id:
            return platform, chat_id
    except (ValueError, UnicodeDecodeError):
        pass
    # Backward compatibility for old plain "platform:chat_id" URLs.
    platform, sep, chat_id = value.partition(":")
    if not sep or not platform or not chat_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return platform, chat_id


async def _conversation_messages(
    session, owner_id: str, platform: str, chat_id: str, limit: int, before: datetime | None
) -> list[Message]:
    stmt = (
        select(Message)
        .where(
            Message.owner_id == owner_id,
            Message.platform == platform,
            Message.platform_chat_id == chat_id,
        )
        .options(selectinload(Message.files))
    )
    if before:
        stmt = stmt.where(Message.created_at < before)
    stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
    rows = list((await session.scalars(stmt)).all())
    rows.reverse()
    return rows


async def _enrich(session, owner_id: str, user_id: str, messages: list[Message]) -> list[dict[str, Any]]:
    file_ids = [file.id for message in messages for file in message.files]
    views = {}
    if file_ids:
        rows = (
            await session.scalars(
                select(FileView).where(FileView.user_id == user_id, FileView.file_id.in_(file_ids))
            )
        ).all()
        views = {row.file_id: row for row in rows}

    task_ids = [message.task_id for message in messages if message.task_id]
    tasks: dict[str, Task] = {}
    if task_ids:
        rows = (await session.scalars(select(Task).where(Task.id.in_(task_ids)))).all()
        tasks = {task.id: task for task in rows}

    result = []
    for message in messages:
        files = [file_payload(file, views.get(file.id), message) for file in message.files]
        result.append(
            message_payload(
                message,
                files=files,
                task=task_brief(tasks.get(message.task_id)) if message.task_id else None,
            )
        )
    return result


def _preview(message: Message) -> str:
    if message.text.strip():
        return message.text.strip()
    if message.files:
        return f"[文件] {message.files[0].filename}"
    if message.content_type == "image":
        return "[图片]"
    return "[消息]"


async def _chat_title(
    session,
    settings,
    adapters,
    owner_id: str,
    platform: str,
    chat_id: str,
    chat_type: str,
    latest: Message,
    last_inbound: Message | None,
) -> str:
    raw_title = ""
    try:
        import json

        raw = json.loads(latest.raw_json or "{}")
        raw_title = str(raw.get("conversation_title") or "").strip()
    except ValueError:
        pass
    if raw_title:
        return raw_title
    if chat_type == "group":
        try:
            account = await context_for_user(session, owner_id, platform, settings)
            adapter = adapters.get(platform)
            resolver = getattr(adapter, "get_chat_name", None)
            if resolver is not None:
                name = await resolver(account, chat_id)
                if name:
                    return name
        except Exception:  # noqa: BLE001
            pass
        return f"群聊 {chat_id[-6:]}" if chat_id else "群聊"
    if last_inbound and last_inbound.sender_name.strip():
        return last_inbound.sender_name.strip()
    receiver = latest.receiver_platform_id
    if receiver:
        from ..models import PlatformAccount, User

        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform,
                PlatformAccount.platform_user_id == receiver,
            )
        )
        if account:
            user = await session.get(User, account.user_id)
            if user and user.display_name.strip():
                return user.display_name.strip()
    return f"私聊 {chat_id[-6:]}" if chat_id else "私聊"


async def unread_total(session, owner_id: str) -> int:
    messages = (
        await session.scalars(
            select(Message).where(
                Message.owner_id == owner_id,
                Message.platform_chat_id.is_not(None),
                Message.direction == "inbound",
            )
        )
    ).all()
    reads = (
        await session.scalars(
            select(ConversationRead).where(ConversationRead.owner_id == owner_id)
        )
    ).all()
    read_map = {(row.platform, row.platform_chat_id): row.last_read_at for row in reads}
    return sum(
        1
        for message in messages
        if message.platform_chat_id
        and (
            (read_map.get((message.platform, message.platform_chat_id)) is None)
            or message.created_at > read_map[(message.platform, message.platform_chat_id)]
        )
    )


@router.get("")
async def list_conversations(
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    platform: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    stmt = (
        select(Message)
        .where(Message.owner_id == user.id, Message.platform_chat_id.is_not(None))
        .options(selectinload(Message.files))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(2000)
    )
    if platform:
        stmt = stmt.where(Message.platform == platform)
    messages = list((await session.scalars(stmt)).all())

    reads = (
        await session.scalars(
            select(ConversationRead).where(ConversationRead.owner_id == user.id)
        )
    ).all()
    read_map = {(row.platform, row.platform_chat_id): row.last_read_at for row in reads}

    grouped: dict[tuple[str, str], list[Message]] = {}
    for message in messages:
        if message.platform_chat_id:
            grouped.setdefault((message.platform, message.platform_chat_id), []).append(message)

    items = []
    for (item_platform, chat_id), rows in grouped.items():
        latest = rows[0]
        last_inbound = next((row for row in rows if row.direction == "inbound"), None)
        title = await _chat_title(
            session,
            settings,
            adapters,
            user.id,
            item_platform,
            chat_id,
            latest.chat_type,
            latest,
            last_inbound,
        )
        read_at = read_map.get((item_platform, chat_id))
        unread = sum(
            1
            for row in rows
            if row.direction == "inbound" and (read_at is None or row.created_at > read_at)
        )
        item = {
            "id": _key(item_platform, chat_id),
            "platform": item_platform,
            "platform_chat_id": chat_id,
            "chat_type": latest.chat_type,
            "title": title,
            "last_message": _preview(latest),
            "last_sender_name": "我" if latest.direction == "outbound" else latest.sender_name,
            "last_at": latest.created_at.isoformat(),
            "message_count": len(rows),
            "unread_count": unread,
        }
        searchable = f"{title} {item['last_message']} {item['last_sender_name']}".lower()
        if q and q.lower() not in searchable:
            continue
        items.append(item)

    items.sort(key=lambda item: item["last_at"], reverse=True)
    return {"items": items[:limit]}


@router.post("/start")
async def start_conversation(
    body: StartConversationRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict[str, Any]:
    if body.platform not in {"feishu", "dingtalk", "wecom"}:
        raise HTTPException(status_code=400, detail="不支持的平台")
    from ..models import PlatformAccount, User

    handle = body.handle
    display_name = ""
    if body.contact_id:
        contact = await session.get(User, body.contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="联系人不存在")
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == contact.id,
                PlatformAccount.platform == body.platform,
            )
        )
        if account is None:
            raise HTTPException(status_code=400, detail="该联系人没有绑定目标平台账号")
        handle = account.platform_user_id
        display_name = contact.display_name
    elif handle:
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == body.platform,
                PlatformAccount.platform_user_id == handle,
            )
        )
        if account:
            contact = await session.get(User, account.user_id)
            display_name = contact.display_name if contact else account.display_name
        else:
            # Do not create a local contact until the platform send succeeds.
            # Invalid phone/email/open_id attempts must not pollute contacts.
            display_name = (body.display_name or "").strip()
    if not handle:
        raise HTTPException(status_code=400, detail="缺少联系人")

    if body.platform == "feishu" and _looks_like_mobile(handle):
        mobile = "".join(ch for ch in handle if ch.isdigit())
        try:
            account = await context_for_user(session, user.id, "feishu", settings)
            resolved = await adapters.get("feishu").resolve_open_id_by_mobile(
                account, mobile
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    "这个手机号不在当前飞书通讯录中，无法解析 open_id。"
                    "外部联系人请填写对方 open_id 或邮箱。"
                ),
            )
        own_account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == user.id,
                PlatformAccount.platform == "feishu",
            )
        )
        if own_account and own_account.platform_user_id == resolved:
            raise HTTPException(
                status_code=400,
                detail="这是你本人的飞书账号，不能给自己发起私聊。",
            )
        handle = resolved
        if not display_name:
            known = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.platform == "feishu",
                    PlatformAccount.platform_user_id == resolved,
                )
            )
            if known:
                known_user = await session.get(User, known.user_id)
                display_name = (
                    known_user.display_name if known_user else known.display_name
                ) or "飞书联系人"
            else:
                display_name = body.display_name or "飞书联系人"

    if body.platform == "feishu" and _looks_like_email(handle):
        email = handle.strip()
        try:
            account = await context_for_user(session, user.id, "feishu", settings)
            resolved = await adapters.get("feishu").resolve_open_id_by_email(
                account, email
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    "这个邮箱不在当前飞书通讯录中，无法解析 open_id。"
                    "飞书外部联系人请填写对方 open_id。"
                ),
            )
        own_account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == user.id,
                PlatformAccount.platform == "feishu",
            )
        )
        if own_account and own_account.platform_user_id == resolved:
            raise HTTPException(
                status_code=400,
                detail="这是你本人的飞书账号，不能给自己发起私聊。",
            )
        handle = resolved
        if not display_name:
            known = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.platform == "feishu",
                    PlatformAccount.platform_user_id == resolved,
                )
            )
            if known:
                known_user = await session.get(User, known.user_id)
                display_name = (
                    known_user.display_name if known_user else known.display_name
                ) or "飞书联系人"
            else:
                display_name = body.display_name or "飞书联系人"

    _validate_start_handle(body.platform, handle)

    receiver_handle = handle
    resolved_dingtalk_chat = ""
    if body.platform == "dingtalk" and display_name:
        from ..services.contacts import resolve_dingtalk_conversation

        try:
            resolved_dingtalk_chat = await resolve_dingtalk_conversation(
                settings, display_name
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"钉钉会话解析失败：dws 无法执行（{exc}）。请检查 DWS 安装与登录态。",
            ) from exc
        if resolved_dingtalk_chat:
            handle = resolved_dingtalk_chat

    if body.platform == "dingtalk" and not handle.startswith("cid"):
        # A DingTalk 1:1 chat is addressed by its cid… conversation id. Sending
        # to a bare staff userId would deliver a robot message into a different
        # conversation than the one inbound events report, so fail loudly.
        raise HTTPException(
            status_code=502,
            detail=(
                "未能解析钉钉单聊会话 ID（cid…）。请确认 dws 已登录（dws auth status），"
                "并在钉钉客户端先给对方发一条消息，让会话出现在 dws 会话列表中。"
            ),
        )

    try:
        payload = await send_text(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            owner=user,
            platform=body.platform,
            text=body.text,
            to_handle=handle,
            client_msg_id=body.client_msg_id,
            chat_type="direct",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if payload.get("delivery_status") != "sent":
        raise HTTPException(status_code=502, detail=payload.get("error") or "发起会话失败")
    chat_id = payload.get("platform_chat_id") or resolved_dingtalk_chat
    if chat_id:
        message_row = await session.get(Message, payload.get("id"))
        if message_row is not None:
            if message_row.platform_chat_id != chat_id:
                message_row.platform_chat_id = chat_id
            if (
                body.platform == "dingtalk"
                and receiver_handle
                and not receiver_handle.startswith("cid")
            ):
                message_row.receiver_platform_id = receiver_handle
            await session.commit()
        payload["platform_chat_id"] = chat_id
    if not chat_id:
        raise HTTPException(status_code=502, detail="平台未返回会话 ID，无法建立会话")
    if body.handle and not body.contact_id:
        known = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == body.platform,
                PlatformAccount.platform_user_id == handle,
            )
        )
        if known is None:
            contact = User(
                display_name=display_name or "外部联系人",
                is_placeholder=False,
            )
            session.add(contact)
            await session.flush()
            session.add(
                PlatformAccount(
                    user_id=contact.id,
                    platform=body.platform,
                    platform_user_id=handle,
                    display_name=contact.display_name,
                    status="connected",
                )
            )
            await session.commit()
            display_name = contact.display_name
    return {
        "message": payload,
        "conversation_id": _key(body.platform, chat_id),
        "title": display_name or "私聊",
    }


async def _require_chat(session, owner_id: str, conversation_id: str) -> tuple[str, str, str]:
    platform, chat_id = _parse_key(conversation_id)
    exists = await session.scalar(
        select(Message.id).where(
            Message.owner_id == owner_id,
            Message.platform == platform,
            Message.platform_chat_id == chat_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    latest = await session.scalar(
        select(Message)
        .where(
            Message.owner_id == owner_id,
            Message.platform == platform,
            Message.platform_chat_id == chat_id,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return platform, chat_id, latest.chat_type if latest else "direct"


@router.post("/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: str,
    session: SessionDep,
    user: CurrentUser,
) -> dict[str, Any]:
    platform, chat_id, _ = await _require_chat(session, user.id, conversation_id)
    row = await session.scalar(
        select(ConversationRead).where(
            ConversationRead.owner_id == user.id,
            ConversationRead.platform == platform,
            ConversationRead.platform_chat_id == chat_id,
        )
    )
    if row is None:
        session.add(
            ConversationRead(
                owner_id=user.id,
                platform=platform,
                platform_chat_id=chat_id,
                last_read_at=utcnow(),
            )
        )
    else:
        row.last_read_at = utcnow()
    await session.commit()
    return {"ok": True, "unread_count": 0}


@router.get("/{conversation_id}/members")
async def conversation_members(
    conversation_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
) -> dict[str, Any]:
    platform, chat_id, chat_type = await _require_chat(session, user.id, conversation_id)
    if chat_type != "group":
        return {"items": []}
    try:
        if platform == "dingtalk":
            from ..models import PlatformAccount, User

            rows = (
                await session.execute(
                    select(PlatformAccount, User)
                    .join(User, User.id == PlatformAccount.user_id)
                    .where(PlatformAccount.platform == "dingtalk")
                )
            ).all()
            return {
                "items": [
                    {"id": account.platform_user_id, "name": user.display_name}
                    for account, user in rows
                    if account.platform_user_id and user.display_name
                ]
            }
        account = await context_for_user(session, user.id, platform, settings)
        resolver = getattr(adapters.get(platform), "list_chat_members", None)
        if resolver is None:
            return {"items": []}
        items = await resolver(account, chat_id)
        return {"items": items}
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "error": str(exc)[:300]}


@router.get("/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: str,
    session: SessionDep,
    user: CurrentUser,
    before: str | None = None,
    limit: int = Query(default=200, ge=1, le=300),
) -> dict[str, Any]:
    platform, chat_id, _ = await _require_chat(session, user.id, conversation_id)
    parsed_before = None
    if before:
        try:
            parsed_before = datetime.fromisoformat(before.replace("Z", "+00:00"))
            if parsed_before.tzinfo is None:
                parsed_before = parsed_before.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="before 参数不合法") from exc
    messages = await _conversation_messages(session, user.id, platform, chat_id, limit, parsed_before)
    items = await _enrich(session, user.id, user.id, messages)
    next_before = items[0]["created_at"] if len(messages) == limit and items else None
    return {"items": items, "next_before": next_before}


@router.post("/{conversation_id}/send")
async def conversation_send(
    conversation_id: str,
    body: ConversationSendRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict[str, Any]:
    platform, chat_id, chat_type = await _require_chat(session, user.id, conversation_id)
    try:
        return await send_text(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            owner=user,
            platform=platform,
            text=body.text,
            to_handle=chat_id,
            client_msg_id=body.client_msg_id,
            reply_to_message_id=body.reply_to_message_id,
            chat_id=chat_id,
            chat_type=chat_type,
            mentions=[mention.model_dump() for mention in body.mentions],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.post("/{conversation_id}/file")
async def conversation_send_file(
    conversation_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
    file: UploadFile = File(...),
    text: str | None = Form(None),
    client_msg_id: str | None = Form(None),
):
    platform, chat_id, chat_type = await _require_chat(session, user.id, conversation_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    try:
        return await send_file_message(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            owner=user,
            platform=platform,
            filename=file.filename or "未命名文件",
            mime_type=file.content_type or "application/octet-stream",
            data=data,
            text=text,
            to_handle=chat_id,
            client_msg_id=client_msg_id,
            chat_id=chat_id,
            chat_type=chat_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
