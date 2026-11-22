"""Move historical data between two Relay accounts.

Used when a platform identity is unbound from one account and re-bound to
another: the messages/tasks/files already ingested stay attached to the old
account, so the new owner sees an empty workspace. This module reassigns that
history, with a dry-run report and conflict handling.

It never touches ``platform_accounts`` — binding is handled by OAuth/unbind.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConversationRead, FileRecord, FileView, Message, PlatformAccount, Task, User


class MigrationError(Exception):
    pass


async def move_platform_binding(
    session: AsyncSession,
    *,
    platform: str,
    handle: str,
    target_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Move one platform identity from its current account to another.

    Unlike OAuth this is an operator action: it needs host access, prevents an
    accidental identity swap, and refuses when the target already holds an
    identity for that platform (unbind it first).
    """
    if platform not in {"feishu", "dingtalk", "wecom"}:
        raise MigrationError(f"不支持的平台：{platform}")
    account = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.platform_user_id == handle,
        )
    )
    if account is None:
        raise MigrationError(f"平台身份不存在：{platform}/{handle}")
    target = await session.get(User, target_id)
    if target is None:
        raise MigrationError("目标账号不存在")
    source = await session.get(User, account.user_id)

    report: dict[str, Any] = {
        "platform": platform,
        "handle": handle,
        "from": (
            {"id": source.id, "name": source.display_name, "email": source.email}
            if source is not None
            else None
        ),
        "to": {"id": target.id, "name": target.display_name, "email": target.email},
        "applied": apply,
        "status": "moved",
    }
    if account.user_id == target_id:
        report["status"] = "already_bound"
        return report

    existing = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.user_id == target_id,
            PlatformAccount.platform == platform,
        )
    )
    if existing is not None:
        raise MigrationError(
            f"目标账号已绑定 {platform} 身份（{existing.platform_user_id}），"
            "请先在该账号里解绑后再迁移"
        )

    if apply:
        account.user_id = target_id
        await session.commit()
    return report


def _message_key(message: Message) -> tuple[str, str] | None:
    if not message.platform_msg_id:
        return None
    return (message.platform, message.platform_msg_id)


async def migrate_owner_data(
    session: AsyncSession,
    *,
    source_id: str,
    target_id: str,
    platform: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    if source_id == target_id:
        raise MigrationError("源账号与目标账号相同")
    source = await session.get(User, source_id)
    target = await session.get(User, target_id)
    if source is None or target is None:
        raise MigrationError("源账号或目标账号不存在")

    report: dict[str, Any] = {
        "source": {"id": source.id, "name": source.display_name, "email": source.email},
        "target": {"id": target.id, "name": target.display_name, "email": target.email},
        "platform": platform,
        "applied": apply,
        "messages_moved": 0,
        "messages_skipped": 0,
        "message_conflicts": [],
        "tasks_moved": 0,
        "tasks_skipped": 0,
        "task_conflicts": [],
        "files_moved": 0,
        "file_views_moved": 0,
        "file_views_merged": 0,
        "reads_moved": 0,
        "reads_merged": 0,
    }

    message_stmt = select(Message).where(Message.owner_id == source_id)
    if platform:
        message_stmt = message_stmt.where(Message.platform == platform)
    messages = list((await session.scalars(message_stmt)).all())

    target_keys: set[tuple[str, str]] = set()
    target_msg_stmt = select(Message.platform, Message.platform_msg_id).where(
        Message.owner_id == target_id, Message.platform_msg_id.is_not(None)
    )
    if platform:
        target_msg_stmt = target_msg_stmt.where(Message.platform == platform)
    for row_platform, row_msg_id in (await session.execute(target_msg_stmt)).all():
        if row_msg_id:
            target_keys.add((row_platform, row_msg_id))

    movable: list[Message] = []
    skipped_message_ids: set[str] = set()
    for message in messages:
        key = _message_key(message)
        if key is not None and key in target_keys:
            report["messages_skipped"] += 1
            skipped_message_ids.add(message.id)
            if len(report["message_conflicts"]) < 20:
                report["message_conflicts"].append(key[1])
            continue
        movable.append(message)
    report["messages_moved"] = len(movable)

    # ----- tasks -----
    task_stmt = select(Task).where(Task.owner_id == source_id)
    if platform:
        task_stmt = task_stmt.where(Task.source_platform == platform)
    tasks = list((await session.scalars(task_stmt)).all())
    target_task_sources = {
        row
        for row in (
            await session.scalars(
                select(Task.source_message_id).where(
                    Task.owner_id == target_id, Task.source_message_id.is_not(None)
                )
            )
        ).all()
    }
    movable_tasks: list[Task] = []
    for task in tasks:
        if task.source_message_id and task.source_message_id in target_task_sources:
            report["tasks_skipped"] += 1
            if len(report["task_conflicts"]) < 20:
                report["task_conflicts"].append(task.title)
            continue
        movable_tasks.append(task)
    report["tasks_moved"] = len(movable_tasks)

    # ----- files (messages that move, plus rule assets with no message) -----
    movable_ids = {message.id for message in movable}
    file_stmt = select(FileRecord).where(FileRecord.owner_id == source_id)
    files = list((await session.scalars(file_stmt)).all())
    movable_files: list[FileRecord] = []
    for record in files:
        if record.message_id is None:
            # Auto-reply assets belong to the account, not to a conversation.
            movable_files.append(record)
        elif record.message_id in movable_ids:
            movable_files.append(record)
    report["files_moved"] = len(movable_files)

    # ----- conversation read state -----
    read_stmt = select(ConversationRead).where(ConversationRead.owner_id == source_id)
    if platform:
        read_stmt = read_stmt.where(ConversationRead.platform == platform)
    reads = list((await session.scalars(read_stmt)).all())
    target_reads = {
        (row.platform, row.platform_chat_id): row
        for row in (
            await session.scalars(
                select(ConversationRead).where(ConversationRead.owner_id == target_id)
            )
        ).all()
    }
    move_reads: list[ConversationRead] = []
    merge_reads: list[tuple[ConversationRead, ConversationRead]] = []
    for row in reads:
        existing = target_reads.get((row.platform, row.platform_chat_id))
        if existing is None:
            move_reads.append(row)
        else:
            merge_reads.append((row, existing))
    report["reads_moved"] = len(move_reads)
    report["reads_merged"] = len(merge_reads)

    # ----- file views (only for files that move) -----
    movable_file_ids = {record.id for record in movable_files}
    views: list[FileView] = []
    if movable_file_ids:
        views = list(
            (
                await session.scalars(
                    select(FileView).where(
                        FileView.user_id == source_id,
                        FileView.file_id.in_(movable_file_ids),
                    )
                )
            ).all()
        )
    target_views = {
        row.file_id: row
        for row in (
            await session.scalars(select(FileView).where(FileView.user_id == target_id))
        ).all()
    }
    merge_views: list[tuple[FileView, FileView]] = []
    move_views: list[FileView] = []
    for view in views:
        existing = target_views.get(view.file_id)
        if existing is None:
            move_views.append(view)
        else:
            merge_views.append((view, existing))
    report["file_views_moved"] = len(move_views)
    report["file_views_merged"] = len(merge_views)

    if not apply:
        return report

    for message in movable:
        message.owner_id = target_id
    for record in movable_files:
        record.owner_id = target_id
    for task in movable_tasks:
        task.owner_id = target_id
        if task.assignee_id == source_id:
            task.assignee_id = target_id
    for row in move_reads:
        row.owner_id = target_id
    for row, existing in merge_reads:
        if row.last_read_at > existing.last_read_at:
            existing.last_read_at = row.last_read_at
        await session.delete(row)
    for view in move_views:
        view.user_id = target_id
    for view, existing in merge_views:
        existing.open_count = max(existing.open_count, view.open_count)
        existing.first_opened_at = min(existing.first_opened_at, view.first_opened_at)
        existing.last_opened_at = max(existing.last_opened_at, view.last_opened_at)
        await session.delete(view)

    await session.commit()
    return report
