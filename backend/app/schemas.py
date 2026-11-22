from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SendRequest(BaseModel):
    platform: Literal["feishu", "dingtalk", "wecom"]
    to_user_id: str | None = None
    to_handle: str | None = None
    text: str = Field(min_length=1, max_length=8000)
    client_msg_id: str | None = None
    reply_to_message_id: str | None = None


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    current_password: str | None = Field(default=None, max_length=128)


class PlatformConfigRequest(BaseModel):
    enabled: bool = False
    values: dict[str, str | bool | None] = Field(default_factory=dict)


class AutoReplyRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["feishu", "dingtalk", "wecom"] | None = None
    match_type: Literal["keyword", "any"] = "keyword"
    keywords: str = Field(default="", max_length=1000)
    reply_text: str = Field(min_length=1, max_length=2000)
    attachment_file_id: str | None = Field(default=None, max_length=32)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=9999)
    cooldown_seconds: int = Field(default=30, ge=0, le=86400)


class CreateTaskRequest(BaseModel):
    source_message_id: str
    title: str | None = None
    description: str | None = None
    assignee_id: str | None = None
    due_at: datetime | None = None
    sync_platform: bool = True


class TaskActionRequest(BaseModel):
    client_action_id: str = Field(min_length=4, max_length=80)
    action_type: str = "action"
    reply_text: str | None = Field(default=None, max_length=8000)
    target_status: Literal["open", "in_progress", "done"] | None = None


class WeComLinkRequest(BaseModel):
    todo_id: str = Field(min_length=2, max_length=200)


class SimulateMessageRequest(BaseModel):
    platform: Literal["feishu", "dingtalk", "wecom"] = "feishu"
    text: str = Field(min_length=1)
    sender_name: str = "模拟用户"
    sender_handle: str = "ou_sim"
    chat_id: str = "sim_chat"
    chat_type: Literal["direct", "group"] = "direct"


class SimulateTaskEventRequest(BaseModel):
    platform: Literal["feishu", "dingtalk", "wecom"] = "feishu"
    platform_task_id: str
    status: Literal["open", "in_progress", "done"]
    title: str = ""
    event_id: str | None = None


class SimulatePlatformTaskRequest(BaseModel):
    platform: Literal["feishu", "dingtalk", "wecom"] = "feishu"
    platform_task_id: str
    status: Literal["open", "in_progress", "done"]
