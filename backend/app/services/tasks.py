from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import (
    ActionStatus,
    Direction,
    LinkState,
    Message,
    Platform,
    SyncState,
    Task,
    TaskAction,
    TaskLink,
    TaskOrigin,
    TaskStatus,
    User,
)
from ..platform.base import AccountContext, CapabilityError, MessageRef, PlatformError, TaskDraft
from ..platform.registry import AdapterRegistry
from .accounts import context_for_user, get_account
from .events import EventBus
from .payloads import link_payload, message_brief, task_payload
from .resilience import platform_gate
from .sync import consume_token, is_echo, prepare_write, to_platform_status

_locks: dict[str, asyncio.Lock] = {}


def task_lock(task_id: str) -> asyncio.Lock:
    lock = _locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[task_id] = lock
    return lock


@dataclass(slots=True)
class ActionInput:
    client_action_id: str
    action_type: str = "action"
    reply_text: str | None = None
    target_status: str | None = None


async def get_task(session: AsyncSession, task_id: str, owner_id: str | None = None) -> Task | None:
    stmt = select(Task).where(Task.id == task_id)
    if owner_id:
        stmt = stmt.where(Task.owner_id == owner_id)
    return await session.scalar(stmt)


async def build_task_payload(session: AsyncSession, task: Task) -> dict[str, Any]:
    source = None
    if task.source_message_id:
        source = await session.get(Message, task.source_message_id)
    assignee_name = None
    if task.assignee_id:
        assignee = await session.get(User, task.assignee_id)
        assignee_name = assignee.display_name if assignee else None
    return task_payload(task, source_message=source, assignee_name=assignee_name)


async def list_task_payloads(session: AsyncSession, owner_id: str, *, status: str | None = None,
                              platform: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Task).where(Task.owner_id == owner_id)
    if status and status != "all":
        if status == "active":
            stmt = stmt.where(Task.status.in_([TaskStatus.OPEN, TaskStatus.IN_PROGRESS]))
        else:
            stmt = stmt.where(Task.status == status)
    if platform:
        stmt = stmt.where(
            Task.id.in_(select(TaskLink.task_id).where(TaskLink.platform == platform))
        )
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Task.title.ilike(like)) | (Task.description.ilike(like)))
    stmt = stmt.order_by(Task.updated_at.desc())
    tasks = (await session.scalars(stmt)).all()
    return [await build_task_payload(session, task) for task in tasks]


async def create_task_from_message(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    user: User,
    message: Message,
    title: str | None = None,
    description: str | None = None,
    assignee_id: str | None = None,
    due_at: datetime | None = None,
    sync_platform: bool = True,
) -> tuple[Task, bool]:
    existing = await session.scalar(
        select(Task).where(Task.owner_id == user.id, Task.source_message_id == message.id)
    )
    if existing:
        return existing, False

    clean_title = (title or message.text or message.files[0].filename if message.files else title or message.text or "未命名待办").strip()
    if not clean_title:
        clean_title = "未命名待办"
    clean_title = clean_title[:500]
    desc = description or (
        f"来源：{message.platform} · {message.sender_name or '未知发送人'}\n"
        f"时间：{message.created_at.isoformat()}\n\n{message.text}"
    )
    task = Task(
        owner_id=user.id,
        title=clean_title,
        description=desc,
        status=TaskStatus.OPEN,
        source_platform=message.platform,
        source_message_id=message.id,
        origin=TaskOrigin.MESSAGE,
        assignee_id=assignee_id or user.id,
        due_at=due_at,
    )
    session.add(task)
    await session.flush()
    message.task_id = task.id
    await session.flush()

    if sync_platform:
        adapter = adapters.get(message.platform)
        if not adapter.supports_task_creation:
            task.sync_state = SyncState.LOCAL
            task.sync_error = "该平台不支持服务端创建待办；请在平台客户端完成创建后回传 ID。"
        else:
            account: AccountContext | None = None
            try:
                account = await context_for_user(session, user.id, message.platform, settings)
            except PlatformError as exc:
                task.sync_state = SyncState.FAILED
                task.sync_error = exc.message
            if account is not None:
                assignee_ctx: AccountContext | None = account
                if task.assignee_id and task.assignee_id != user.id:
                    assignee_account = await get_account(session, task.assignee_id, message.platform)
                    if assignee_account is None:
                        task.sync_state = SyncState.LOCAL
                        task.sync_error = "负责人未绑定该平台账号，已创建本地待办。"
                        assignee_ctx = None
                    else:
                        from .tokens import ensure_fresh_account

                        assignee_ctx = await ensure_fresh_account(session, assignee_account, settings)

                if assignee_ctx is not None:
                    draft = TaskDraft(
                        unified_id=task.id,
                        title=task.title,
                        description=task.description,
                        due_at=task.due_at,
                        assignee_platform_id=assignee_ctx.platform_user_id,
                        owner_platform_id=account.platform_user_id,
                    )
                    try:
                        ref = await platform_gate.once(
                            message.platform, lambda: adapter.create_task(account, draft, assignee_ctx)
                        )
                        session.add(
                            TaskLink(
                                task_id=task.id,
                                platform=message.platform,
                                platform_task_id=ref.platform_task_id,
                                platform_status="open",
                                last_synced_at=utcnow(),
                                meta_json=json.dumps(ref.meta, ensure_ascii=False, default=str),
                            )
                        )
                        task.sync_state = SyncState.SYNCED
                        task.sync_error = None
                    except CapabilityError as exc:
                        task.sync_state = SyncState.LOCAL
                        task.sync_error = exc.message
                    except PlatformError as exc:
                        task.sync_state = SyncState.FAILED
                        task.sync_error = exc.message

    await session.commit()
    await session.refresh(task)
    await session.refresh(task, ["links"])
    payload = await build_task_payload(session, task)
    await bus.publish("task.created", payload)
    return task, True


async def _create_outbound_reply(
    session: AsyncSession,
    *,
    task: Task,
    source: Message,
    text: str,
    platform_msg_id: str,
) -> Message:
    outgoing = Message(
        owner_id=task.owner_id,
        platform=source.platform,
        direction=Direction.OUTBOUND,
        platform_msg_id=platform_msg_id,
        platform_chat_id=source.platform_chat_id,
        chat_type=source.chat_type,
        sender_name="我",
        receiver_platform_id=source.sender_platform_id,
        content_type="text",
        text=text,
        reply_to_message_id=source.id,
        reply_to_platform_msg_id=source.platform_msg_id,
        task_id=task.id,
        delivery_status="sent",
        created_at=utcnow(),
    )
    session.add(outgoing)
    await session.flush()
    return outgoing


async def perform_task_action(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    user: User,
    task: Task,
    payload: ActionInput,
) -> tuple[TaskAction, Task]:
    async with task_lock(task.id):
        existing = await session.scalar(
            select(TaskAction).where(TaskAction.client_action_id == payload.client_action_id)
        )
        if existing:
            fresh = await get_task(session, task.id, user.id)
            return existing, fresh or task

        action = TaskAction(
            task_id=task.id,
            client_action_id=payload.client_action_id,
            action_type=payload.action_type,
            request_json=json.dumps(
                {
                    "reply_text": payload.reply_text,
                    "target_status": payload.target_status,
                },
                ensure_ascii=False,
            ),
            steps_json="{}",
            status=ActionStatus.PENDING,
        )
        session.add(action)
        await session.flush()

        steps: dict[str, Any] = {}
        reply_ok = True
        if payload.reply_text is not None:
            text = payload.reply_text.strip()
            if not text:
                steps["reply"] = {"status": "skipped", "message": "回复内容为空"}
            elif not task.source_message_id:
                steps["reply"] = {
                    "status": "failed",
                    "message": "该任务没有源消息，无法引用回复；请直接修改状态或从消息创建任务。",
                }
                reply_ok = False
            else:
                source = await session.get(Message, task.source_message_id)
                if source is None or not source.platform_msg_id:
                    steps["reply"] = {"status": "failed", "message": "源消息不可用或缺少平台消息 ID。"}
                    reply_ok = False
                else:
                    try:
                        account = await context_for_user(session, user.id, source.platform, settings)
                        adapter = adapters.get(source.platform)
                        ref = MessageRef(
                            id=source.id,
                            platform=source.platform,
                            platform_msg_id=source.platform_msg_id,
                            chat_id=source.platform_chat_id,
                            chat_type=source.chat_type,
                            sender_platform_id=source.sender_platform_id,
                            sender_name=source.sender_name,
                        )
                        result = await platform_gate.once(
                            source.platform, lambda: adapter.reply_text(account, ref, text)
                        )
                        outgoing = await _create_outbound_reply(
                            session,
                            task=task,
                            source=source,
                            text=text,
                            platform_msg_id=result.platform_msg_id,
                        )
                        steps["reply"] = {
                            "status": "ok",
                            "message_id": outgoing.id,
                            "platform_msg_id": result.platform_msg_id,
                        }
                    except PlatformError as exc:
                        steps["reply"] = {"status": "failed", "message": exc.message}
                        reply_ok = False

        status_ok = True
        if payload.target_status:
            target = payload.target_status
            if target not in {TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.DONE}:
                raise ValueError("无效的任务状态")
            if not reply_ok:
                steps["status"] = {"status": "skipped", "message": "回复失败，未执行状态变更。"}
            elif target == task.status and not any(link.link_state == LinkState.ACTIVE for link in task.links):
                steps["status"] = {"status": "skipped", "message": "状态未变化"}
            else:
                link_results: list[dict[str, Any]] = []
                failure_messages: list[str] = []
                for link in task.links:
                    if link.link_state != LinkState.ACTIVE:
                        continue
                    token = prepare_write(link, target)
                    try:
                        account = await context_for_user(session, user.id, link.platform, settings)
                        adapter = adapters.get(link.platform)
                        await platform_gate.idempotent(
                            link.platform,
                            lambda: adapter.update_task_status(account, link, target, token),
                        )
                        link.platform_status = to_platform_status(link.platform, target)
                        link.last_synced_at = utcnow()
                        link_results.append({"platform": link.platform, "status": "ok"})
                    except PlatformError as exc:
                        failure_messages.append(f"{link.platform}: {exc.message}")
                        link_results.append(
                            {"platform": link.platform, "status": "failed", "message": exc.message}
                        )
                        consume_token(link)
                task.status = target
                task.version += 1
                task.closed_at = utcnow() if target == TaskStatus.DONE else None
                if task.links and not failure_messages:
                    task.sync_state = SyncState.SYNCED
                    task.sync_error = None
                elif task.links:
                    task.sync_state = SyncState.PARTIAL if any(
                        item["status"] == "ok" for item in link_results
                    ) else SyncState.FAILED
                    task.sync_error = "；".join(failure_messages)[:2000]
                else:
                    task.sync_state = SyncState.LOCAL
                status_ok = not failure_messages
                steps["status"] = {
                    "status": "ok" if status_ok else "partial",
                    "target": target,
                    "links": link_results,
                }

        if not reply_ok:
            action.status = ActionStatus.FAILED
        elif status_ok:
            action.status = ActionStatus.SUCCEEDED
        else:
            action.status = ActionStatus.PARTIAL
        action.steps_json = json.dumps(steps, ensure_ascii=False, default=str)
        await session.commit()
        await session.refresh(action)
        fresh = await get_task(session, task.id, user.id)
        assert fresh is not None
        task_payload_data = await build_task_payload(session, fresh)
        await bus.publish("task.updated", task_payload_data)
        await bus.publish(
            "task.action",
            {
                "task_id": fresh.id,
                "action_id": action.id,
                "status": action.status,
                "steps": steps,
                "task": task_payload_data,
            },
        )
        return action, fresh


async def sync_task_from_platform(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    link: TaskLink,
    new_status: str,
    event_time: datetime | None = None,
) -> dict[str, Any]:
    task = await get_task(session, link.task_id)
    if task is None:
        return {"status": "orphan"}
    async with task_lock(task.id):
        if is_echo(link, new_status, event_time):
            link.platform_status = to_platform_status(link.platform, new_status)
            link.last_synced_at = utcnow()
            link.last_event_at = event_time or utcnow()
            consume_token(link)
            await session.commit()
            return {"status": "echo", "task_id": task.id}

        link.platform_status = to_platform_status(link.platform, new_status)
        link.last_synced_at = utcnow()
        link.last_event_at = event_time or utcnow()
        if task.status == new_status:
            await session.commit()
            return {"status": "unchanged", "task_id": task.id}

        task.status = new_status
        task.version += 1
        task.closed_at = utcnow() if new_status == TaskStatus.DONE else None
        failures: list[str] = []
        propagated = 0
        for other in task.links:
            if other.id == link.id or other.link_state != LinkState.ACTIVE:
                continue
            token = prepare_write(other, new_status)
            try:
                account = await context_for_user(session, task.owner_id, other.platform, settings)
                adapter = adapters.get(other.platform)
                await platform_gate.idempotent(
                    other.platform,
                    lambda: adapter.update_task_status(account, other, new_status, token),
                )
                other.platform_status = to_platform_status(other.platform, new_status)
                other.last_synced_at = utcnow()
                propagated += 1
            except PlatformError as exc:
                failures.append(f"{other.platform}: {exc.message}")
                consume_token(other)
        task.sync_state = SyncState.SYNCED if not failures else SyncState.PARTIAL
        task.sync_error = "；".join(failures)[:2000] if failures else None
        await session.commit()
        fresh = await get_task(session, task.id)
        assert fresh is not None
        data = await build_task_payload(session, fresh)
        await bus.publish("task.updated", data)
        return {"status": "propagated", "task_id": task.id, "propagated": propagated, "failures": failures}
