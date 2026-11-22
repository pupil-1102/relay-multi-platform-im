from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB = DATA_DIR / "app.db"


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden by an env var."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Relay · 多平台消息台"
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000
    secret_key: str = "dev-only-change-me"
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_DB.as_posix()}"
    data_dir: Path = DATA_DIR
    file_dir: Path = DATA_DIR / "files"
    frontend_dist: Path = BASE_DIR / "frontend" / "dist"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    session_cookie: str = "relay_session"
    session_ttl_hours: int = 24
    encryption_key: str = ""
    cookie_secure: bool = False

    # Demo / mock mode. The application is fully usable in this mode.
    demo_mode: bool = True
    dev_login: bool = True
    seed_demo: bool = True
    allow_registration: bool = True
    auto_reply_enabled: bool = True
    mock_latency_ms: int = 0
    enable_real_channels: bool = False
    # Start user-token mirrors/pollers for every connected account instead of
    # only the first operator. Bot/app channels stay single-owner.
    multi_user_channels: bool = False
    single_operator: bool = True

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_redirect_uri: str = "http://127.0.0.1:8000/api/auth/feishu/callback"
    feishu_oauth_scope: str = (
        "offline_access task:task:read task:task:write im:message contact:user.base:readonly"
    )
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_user_mirror_enabled: bool = False
    feishu_user_mirror_interval: int = 8
    feishu_enabled: bool = False

    # DingTalk
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_redirect_uri: str = "http://127.0.0.1:8000/api/auth/dingtalk/callback"
    dingtalk_robot_code: str = ""
    dingtalk_corp_id: str = ""
    dingtalk_enabled: bool = False
    dingtalk_dws_enabled: bool = False
    dingtalk_dws_command: str = "dws"
    dingtalk_dws_config_dir: str = ""
    dingtalk_dws_disable_keychain: bool = False
    dingtalk_dws_profile: str = "default"
    dingtalk_dws_role_types: str = "creator,executor,participant"
    dingtalk_dws_im_mirror_enabled: bool = False
    dingtalk_dws_im_interval: int = 8
    dingtalk_user_reply_enabled: bool = False

    # WeCom
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""
    wecom_contact_secret: str = ""
    wecom_aibot_id: str = ""
    wecom_aibot_secret: str = ""
    wecom_redirect_uri: str = "http://127.0.0.1:8000/api/auth/wecom/callback"
    wecom_enabled: bool = False
    # Unverified/personal test tenants cannot enable the app callback, contacts
    # or archive APIs. In that mode only the aibot WebSocket is available.
    wecom_aibot_only: bool = False

    # File handling
    max_file_mb: int = 50
    file_retention_days: int = 0  # 0 = keep forever

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_hours * 3600

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
