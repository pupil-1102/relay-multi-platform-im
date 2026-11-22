from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import select

from ..deps import CurrentUser, SessionDep, SettingsDep
from ..models import AutoReplyRule, FileRecord, FileStatus, Message, User
from ..schemas import AutoReplyRuleRequest
from ..services.auto_reply import split_keywords
from ..services.files import save_bytes

router = APIRouter(prefix="/api/auto-reply", tags=["auto-reply"])


def _attachment_payload(record: FileRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "filename": record.filename,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
    }


def _payload(rule: AutoReplyRule, attachment: FileRecord | None = None) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "platform": rule.platform,
        "match_type": rule.match_type,
        "keywords": rule.keywords,
        "keyword_list": split_keywords(rule.keywords),
        "reply_text": rule.reply_text,
        "attachment": _attachment_payload(attachment),
        "enabled": rule.enabled,
        "priority": rule.priority,
        "cooldown_seconds": rule.cooldown_seconds,
        "hit_count": rule.hit_count,
        "last_hit_at": rule.last_hit_at.isoformat() if rule.last_hit_at else None,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


async def _rule_payload(session, rule: AutoReplyRule) -> dict[str, Any]:
    attachment = None
    if rule.attachment_file_id:
        attachment = await session.get(FileRecord, rule.attachment_file_id)
    return _payload(rule, attachment)


async def _validate_attachment(session, user: User, file_id: str | None) -> None:
    if not file_id:
        return
    record = await session.get(FileRecord, file_id)
    if record is None or record.owner_id != user.id or record.platform != "asset":
        raise HTTPException(status_code=400, detail="附件不存在或不属于当前账号")


def _validate(body: AutoReplyRuleRequest) -> None:
    if body.match_type == "keyword" and not split_keywords(body.keywords):
        raise HTTPException(status_code=400, detail="关键词规则至少需要填写一个关键词")
    if not body.reply_text.strip():
        raise HTTPException(status_code=400, detail="回复内容不能为空")


async def _get_rule(session, user_id: str, rule_id: str) -> AutoReplyRule:
    rule = await session.get(AutoReplyRule, rule_id)
    if rule is None or rule.owner_id != user_id:
        raise HTTPException(status_code=404, detail="自动回复规则不存在")
    return rule


@router.post("/assets", status_code=201)
async def upload_asset(
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=400, detail=f"文件超过 {settings.max_file_mb}MB 上限")
    filename = file.filename or "自动回复附件"
    mime = file.content_type or "application/octet-stream"
    key, digest, size = save_bytes(settings, data, filename)
    record = FileRecord(
        owner_id=user.id,
        platform="asset",
        filename=filename,
        mime_type=mime,
        size_bytes=size,
        sha256=digest,
        storage_key=key,
        download_status=FileStatus.READY,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return _attachment_payload(record) or {}


@router.get("/rules")
async def list_rules(
    session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> dict[str, Any]:
    rules = list(
        (
            await session.scalars(
                select(AutoReplyRule)
                .where(AutoReplyRule.owner_id == user.id)
                .order_by(AutoReplyRule.priority.asc(), AutoReplyRule.created_at.asc())
            )
        ).all()
    )
    return {
        "items": [await _rule_payload(session, rule) for rule in rules],
        "enabled": settings.auto_reply_enabled,
    }


@router.post("/rules", status_code=201)
async def create_rule(
    body: AutoReplyRuleRequest, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    _validate(body)
    await _validate_attachment(session, user, body.attachment_file_id)
    rule = AutoReplyRule(
        owner_id=user.id,
        name=body.name.strip(),
        platform=body.platform,
        match_type=body.match_type,
        keywords=body.keywords.strip(),
        reply_text=body.reply_text.strip(),
        attachment_file_id=body.attachment_file_id,
        enabled=body.enabled,
        priority=body.priority,
        cooldown_seconds=body.cooldown_seconds,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return await _rule_payload(session, rule)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: AutoReplyRuleRequest,
    session: SessionDep,
    user: CurrentUser,
) -> dict[str, Any]:
    _validate(body)
    await _validate_attachment(session, user, body.attachment_file_id)
    rule = await _get_rule(session, user.id, rule_id)
    rule.name = body.name.strip()
    rule.platform = body.platform
    rule.match_type = body.match_type
    rule.keywords = body.keywords.strip()
    rule.reply_text = body.reply_text.strip()
    rule.attachment_file_id = body.attachment_file_id
    rule.enabled = body.enabled
    rule.priority = body.priority
    rule.cooldown_seconds = body.cooldown_seconds
    await session.commit()
    await session.refresh(rule)
    return await _rule_payload(session, rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    rule = await _get_rule(session, user.id, rule_id)
    await session.delete(rule)
    await session.commit()
    return {"ok": True}


@router.get("/log")
async def auto_reply_log(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    rules = {
        rule.id: rule
        for rule in (
            await session.scalars(
                select(AutoReplyRule).where(AutoReplyRule.owner_id == user.id)
            )
        ).all()
    }
    rows = list(
        (
            await session.scalars(
                select(Message)
                .where(
                    Message.owner_id == user.id,
                    Message.auto_reply_rule_id.is_not(None),
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    items = []
    for row in rows:
        rule = rules.get(row.auto_reply_rule_id or "")
        items.append(
            {
                "message_id": row.id,
                "rule_id": row.auto_reply_rule_id,
                "rule_name": rule.name if rule else "已删除规则",
                "platform": row.platform,
                "chat_id": row.platform_chat_id,
                "text": row.text,
                "delivery_status": row.delivery_status,
                "created_at": row.created_at.isoformat(),
            }
        )
    return {"items": items}
