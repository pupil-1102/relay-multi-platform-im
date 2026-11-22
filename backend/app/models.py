from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow


def new_id() -> str:
    return uuid.uuid4().hex


class Platform(StrEnum):
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    WECOM = "wecom"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class SyncState(StrEnum):
    LOCAL = "local"
    SYNCED = "synced"
    PENDING = "pending"
    PARTIAL = "partial"
    FAILED = "failed"


class LinkState(StrEnum):
    ACTIVE = "active"
    UNLINKED = "unlinked"


class TaskOrigin(StrEnum):
    MESSAGE = "message"
    PLATFORM = "platform_task"
    MANUAL = "manual"


class ActionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class FileStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), default="未命名用户")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Null for OAuth-only identities; set when the user registers or adds a password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    accounts: Mapped[list["PlatformAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_account_user_platform"),
        UniqueConstraint("platform", "platform_user_id", name="uq_account_platform_handle"),
        Index("ix_account_platform_union", "platform", "union_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    platform_user_id: Mapped[str] = mapped_column(String(160))
    union_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="connected")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("owner_id", "platform", "platform_msg_id", name="uq_message_owner_platform_msg"),
        UniqueConstraint("owner_id", "client_msg_id", name="uq_message_owner_client_msg"),
        Index("ix_message_owner_created", "owner_id", "created_at"),
        Index("ix_message_platform_created", "platform", "created_at"),
        Index("ix_message_chat", "owner_id", "platform", "platform_chat_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10), default=Direction.INBOUND)
    platform_msg_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    platform_chat_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(20), default="direct")
    sender_platform_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(120), default="")
    sender_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    receiver_platform_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_type: Mapped[str] = mapped_column(String(30), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to_platform_msg_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    auto_reply_rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    client_msg_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    delivery_status: Mapped[str] = mapped_column(String(20), default="received")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    files: Mapped[list["FileRecord"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", lazy="selectin"
    )


class Task(Base):
    __tablename__ = "unified_tasks"
    __table_args__ = (
        UniqueConstraint("owner_id", "source_message_id", name="uq_task_owner_source_message"),
        Index("ix_task_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.OPEN, index=True)
    source_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(20), default=TaskOrigin.MESSAGE)
    assignee_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    version: Mapped[int] = mapped_column(Integer, default=1)
    sync_state: Mapped[str] = mapped_column(String(20), default=SyncState.LOCAL)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    links: Mapped[list["TaskLink"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )


class TaskLink(Base):
    __tablename__ = "platform_task_links"
    __table_args__ = (
        UniqueConstraint("platform", "platform_task_id", name="uq_link_platform_task"),
        Index("ix_link_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("unified_tasks.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    platform_task_id: Mapped[str] = mapped_column(String(200))
    platform_status: Mapped[str] = mapped_column(String(40), default="open")
    sync_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    expected_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    link_state: Mapped[str] = mapped_column(String(20), default=LinkState.ACTIVE)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    task: Mapped[Task] = relationship(back_populates="links")


class FileRecord(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_file_owner_created", "owner_id", "created_at"),
        Index("ix_file_message", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    platform_file_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), default="未命名文件")
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    storage_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    download_status: Mapped[str] = mapped_column(String(20), default=FileStatus.PENDING)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    aeskey: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    message: Mapped[Message | None] = relationship(back_populates="files")


class FileView(Base):
    __tablename__ = "file_views"
    __table_args__ = (UniqueConstraint("file_id", "user_id", name="uq_file_view"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    first_opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    open_count: Mapped[int] = mapped_column(Integer, default=0)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("platform", "event_id", name="uq_event_platform_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80), default="")
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), default="processed")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TaskAction(Base):
    __tablename__ = "task_actions"
    __table_args__ = (UniqueConstraint("client_action_id", name="uq_action_client_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("unified_tasks.id", ondelete="CASCADE"), index=True)
    client_action_id: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(40), default="action")
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    steps_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default=ActionStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    redirect_uri: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ConversationRead(Base):
    __tablename__ = "conversation_reads"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "platform", "platform_chat_id", name="uq_conversation_read"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20))
    platform_chat_id: Mapped[str] = mapped_column(String(300))
    last_read_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PlatformConfig(Base):
    """Per-platform credentials saved from the UI (encrypted at rest).

    A row overrides the matching ``Settings`` values loaded from the
    environment, so a running deployment can be reconfigured without a restart.
    """

    __tablename__ = "platform_configs"

    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class AutoReplyRule(Base):
    """Keyword/fallback rule that answers inbound text automatically."""

    __tablename__ = "auto_reply_rules"
    __table_args__ = (Index("ix_auto_reply_owner_enabled", "owner_id", "enabled"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # NULL means "all platforms".
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    match_type: Mapped[str] = mapped_column(String(20), default="keyword")
    keywords: Mapped[str] = mapped_column(Text, default="")
    reply_text: Mapped[str] = mapped_column(Text)
    # Optional file sent with the reply (uploaded through /api/auto-reply/assets).
    attachment_file_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=30)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


