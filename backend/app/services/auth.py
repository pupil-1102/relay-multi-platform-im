from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import FileRecord, FileView, Message, OAuthState, PlatformAccount, Task, User
from ..security import (
    MIN_PASSWORD_LENGTH,
    create_session_token,
    decode_session_token,
    encrypt_secret,
    hash_password,
    verify_password,
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLATFORM_NAMES = {"feishu": "飞书", "dingtalk": "钉钉", "wecom": "企业微信"}


def _same_person(left: User, right: User) -> bool:
    """Two accounts are the same person only when both carry the same email."""
    left_email = (left.email or "").strip().lower()
    right_email = (right.email or "").strip().lower()
    return bool(left_email and right_email and left_email == right_email)


class AuthError(Exception):
    pass


async def get_user_from_request(request: Request) -> User | None:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    user_id = decode_session_token(token, settings)
    if not user_id:
        return None
    db = request.app.state.db
    async with db.session() as session:
        return await session.get(User, user_id)


async def require_user(request: Request) -> User:
    user = await get_user_from_request(request)
    if user is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="请先登录")
    return user


def set_session_cookie(response: Response, user_id: str, settings: Settings) -> None:
    token = create_session_token(user_id, settings)
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


async def ensure_demo_user(session: AsyncSession, settings: Settings) -> User:
    user = await session.scalar(select(User).where(User.email == "demo@relay.local"))
    if user is None:
        user = User(
            email="demo@relay.local",
            display_name="林知远",
            is_placeholder=False,
        )
        session.add(user)
        await session.flush()
        for platform, handle, union, name in [
            ("feishu", "ou_demo_lin", "on_demo_lin", "林知远"),
            ("dingtalk", "demo_user_lin", "union_demo_lin", "林知远"),
            ("wecom", "lin.zhiyuan", None, "林知远"),
        ]:
            session.add(
                PlatformAccount(
                    user_id=user.id,
                    platform=platform,
                    platform_user_id=handle,
                    union_id=union,
                    display_name=name,
                    status="connected",
                )
            )
        await session.commit()
        await session.refresh(user)
    return user


async def dev_login(session: AsyncSession, settings: Settings) -> str:
    if not settings.dev_login:
        raise AuthError("演示登录已关闭")
    user = await ensure_demo_user(session, settings)
    return user.id


def _clean_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not EMAIL_PATTERN.match(value):
        raise AuthError("邮箱格式不正确")
    return value


def _clean_password(password: str) -> str:
    value = password or ""
    if len(value) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if len(value) > 128:
        raise AuthError("密码不能超过 128 位")
    if value.strip() != value:
        raise AuthError("密码首尾不能是空格")
    return value


async def register_user(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Create a local account so people can use Relay without an OAuth tenant."""
    if not settings.allow_registration:
        raise AuthError("注册已关闭，请联系管理员开通账号")
    clean_email = _clean_email(email)
    clean_password = _clean_password(password)
    existing = await session.scalar(select(User).where(User.email == clean_email))
    if existing is not None:
        raise AuthError("该邮箱已注册，请直接登录")
    name = (display_name or "").strip()[:120] or clean_email.split("@", 1)[0]
    user = User(
        email=clean_email,
        display_name=name,
        password_hash=hash_password(clean_password),
        is_placeholder=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession, *, email: str, password: str
) -> User:
    clean_email = (email or "").strip().lower()
    user = await session.scalar(select(User).where(User.email == clean_email))
    # Always run the hash check so a missing account and a wrong password take
    # comparable time (avoids a trivial account-enumeration oracle).
    stored = user.password_hash if user is not None else None
    if not verify_password(password or "", stored):
        raise AuthError("邮箱或密码不正确")
    assert user is not None
    return user


async def set_user_password(
    session: AsyncSession,
    user: User,
    *,
    password: str,
    current_password: str | None = None,
) -> User:
    if user.password_hash and not verify_password(current_password or "", user.password_hash):
        raise AuthError("当前密码不正确")
    new_hash = hash_password(_clean_password(password))
    # ``user`` usually comes from the request auth dependency, which loads it in
    # its own short-lived session; merge it into this one before writing.
    fresh = await session.merge(user)
    fresh.password_hash = new_hash
    await session.commit()
    await session.refresh(fresh)
    return fresh


async def create_oauth_state(session: AsyncSession, platform: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    session.add(OAuthState(state=state, platform=platform, redirect_uri=redirect_uri))
    await session.commit()
    return state


async def consume_oauth_state(session: AsyncSession, state: str, platform: str) -> OAuthState:
    row = await session.get(OAuthState, state)
    if row is None or row.platform != platform:
        raise AuthError("OAuth state 无效或已过期")
    if row.created_at < datetime.now(UTC) - timedelta(minutes=10):
        raise AuthError("OAuth state 已过期")
    await session.delete(row)
    await session.commit()
    return row


def authorize_url(settings: Settings, platform: str, state: str, redirect_uri: str) -> str:
    if platform == "feishu":
        query = urlencode(
            {
                "client_id": settings.feishu_app_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": settings.feishu_oauth_scope,
                "state": state,
                "prompt": "consent",
            }
        )
        return f"https://accounts.feishu.cn/open-apis/authen/v1/authorize?{query}"
    if platform == "dingtalk":
        query = urlencode(
            {
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "client_id": settings.dingtalk_client_id,
                "scope": "openid",
                "state": state,
                "prompt": "consent",
            }
        )
        return f"https://login.dingtalk.com/oauth2/auth?{query}"
    if platform == "wecom":
        query = urlencode(
            {
                "appid": settings.wecom_corp_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"
    raise AuthError("不支持的平台")


async def _message_count(session: AsyncSession, user_id: str) -> int:
    from sqlalchemy import func

    count = await session.scalar(
        select(func.count()).select_from(Message).where(Message.owner_id == user_id)
    )
    return int(count or 0)


async def _merge_users(session: AsyncSession, source_id: str, target_id: str) -> None:
    if source_id == target_id:
        return
    accounts = list(
        (
            await session.scalars(
                select(PlatformAccount).where(PlatformAccount.user_id == source_id)
            )
        ).all()
    )
    for account in accounts:
        conflict = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == target_id,
                PlatformAccount.platform == account.platform,
            )
        )
        if conflict is None:
            account.user_id = target_id
        else:
            # Keep one account per platform on the canonical user. The newer
            # login refreshes the existing credential.
            conflict.union_id = account.union_id or conflict.union_id
            conflict.display_name = account.display_name or conflict.display_name
            conflict.access_token_enc = account.access_token_enc or conflict.access_token_enc
            conflict.refresh_token_enc = account.refresh_token_enc or conflict.refresh_token_enc
            conflict.token_expires_at = account.token_expires_at or conflict.token_expires_at
            conflict.status = "connected"
            await session.delete(account)
    await session.execute(update(Message).where(Message.owner_id == source_id).values(owner_id=target_id))
    await session.execute(
        update(Message)
        .where(Message.sender_user_id == source_id)
        .values(sender_user_id=target_id)
    )
    await session.execute(update(Task).where(Task.owner_id == source_id).values(owner_id=target_id))
    await session.execute(update(Task).where(Task.assignee_id == source_id).values(assignee_id=target_id))
    await session.execute(update(FileRecord).where(FileRecord.owner_id == source_id).values(owner_id=target_id))
    views = list(
        (await session.scalars(select(FileView).where(FileView.user_id == source_id))).all()
    )
    for view in views:
        existing = await session.scalar(
            select(FileView).where(
                FileView.file_id == view.file_id, FileView.user_id == target_id
            )
        )
        if existing:
            await session.delete(view)
        else:
            view.user_id = target_id
    source = await session.get(User, source_id)
    if source is not None:
        # Bulk delete avoids the ORM cascade deleting the account we just
        # moved to the target user.
        await session.execute(delete(User).where(User.id == source_id))
    await session.flush()


async def _upsert_identity(
    session: AsyncSession,
    *,
    settings: Settings,
    platform: str,
    handle: str,
    union_id: str | None,
    display_name: str,
    email: str | None,
    access_token: str | None,
    refresh_token: str | None,
    expires_in: int | None,
    link_user_id: str | None = None,
) -> User:
    account = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.platform_user_id == handle,
        )
    )
    if account is not None:
        user = await session.get(User, account.user_id)
        if user is None:
            raise AuthError("身份记录损坏")
        if link_user_id and link_user_id != user.id:
            linked = await session.get(User, link_user_id)
            if linked is not None:
                if not _same_person(linked, user):
                    # Never swap the session into a different person's workspace.
                    # Before this guard the login silently merged the two accounts
                    # and the browser ended up in whichever one had more messages.
                    platform_label = PLATFORM_NAMES.get(platform, platform)
                    owner_hint = user.email or user.display_name
                    raise AuthError(
                        f"该{platform_label}账号（{handle}）已绑定到 Relay 账号「{user.display_name}」"
                        f"（{owner_hint}）。它不是当前账号的身份，所以没有切换/合并。"
                        f"要使用它请直接用那个账号登录；要把绑定换到当前账号，"
                        f"请先在「{owner_hint}」的「平台配置 → 当前账号的平台身份」里解绑，"
                        f"或在服务器上执行 scripts/move_platform_binding.py 迁移绑定。"
                    )
                linked_count = await _message_count(session, linked.id)
                account_count = await _message_count(session, user.id)
                if linked_count >= account_count:
                    target, source = linked, user
                else:
                    target, source = user, linked
                await _merge_users(session, source.id, target.id)
                account = await session.scalar(
                    select(PlatformAccount).where(
                        PlatformAccount.platform == platform,
                        PlatformAccount.platform_user_id == handle,
                    )
                )
                user = target
        if account is None:
            raise AuthError("身份合并失败")
        account.union_id = union_id or account.union_id
        account.display_name = display_name or account.display_name
        account.access_token_enc = encrypt_secret(access_token, settings) or account.access_token_enc
        account.refresh_token_enc = encrypt_secret(refresh_token, settings) or account.refresh_token_enc
        if expires_in:
            account.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        account.status = "connected"
        await session.commit()
        return user

    user = None
    if link_user_id:
        user = await session.get(User, link_user_id)
    if user is None and email:
        user = await session.scalar(select(User).where(User.email == email))
    if user is None and settings.single_operator:
        # Single-operator deployment: the Feishu/DingTalk/WeCom identities all
        # belong to the same operator. Link a new platform login to the only
        # existing user instead of creating a second workspace.
        users = list(
            (
                await session.scalars(
                    select(User).where(User.is_placeholder.is_(False))
                )
            ).all()
        )
        if len(users) == 1:
            user = users[0]
    if user is None:
        user = User(email=email, display_name=display_name or "平台用户")
        session.add(user)
        await session.flush()
    account = PlatformAccount(
        user_id=user.id,
        platform=platform,
        platform_user_id=handle,
        union_id=union_id,
        display_name=display_name,
        email=email,
        access_token_enc=encrypt_secret(access_token, settings),
        refresh_token_enc=encrypt_secret(refresh_token, settings),
        token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None,
        status="connected",
    )
    session.add(account)
    await session.commit()
    return user


async def exchange_oauth_code(
    session: AsyncSession,
    settings: Settings,
    platform: str,
    code: str,
    link_user_id: str | None = None,
) -> User:
    async with httpx.AsyncClient(timeout=20) as client:
        if platform == "feishu":
            token_resp = await client.post(
                "https://accounts.feishu.cn/oauth/v3/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.feishu_app_id,
                    "client_secret": settings.feishu_app_secret,
                    "code": code,
                    "redirect_uri": settings.feishu_redirect_uri,
                    "scope": settings.feishu_oauth_scope,
                },
            )
            data = token_resp.json()
            if data.get("code") != 0:
                raise AuthError(
                    f"飞书登录失败: {data.get('error') or data.get('code')} "
                    f"{data.get('error_description') or data.get('msg') or ''}"
                )
            user_token = data.get("access_token")
            if not user_token:
                raise AuthError("飞书未返回 user_access_token")
            info = await client.get(
                "https://open.feishu.cn/open-apis/authen/v1/user_info",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            info_data = info.json().get("data", {})
            handle = info_data.get("open_id") or info_data.get("user_id")
            if not handle:
                raise AuthError("飞书未返回用户标识")
            return await _upsert_identity(
                session,
                settings=settings,
                platform=platform,
                handle=handle,
                union_id=info_data.get("union_id"),
                display_name=info_data.get("name", ""),
                email=info_data.get("email") or info_data.get("enterprise_email"),
                access_token=user_token,
                refresh_token=data.get("refresh_token"),
                expires_in=data.get("expires_in"),
                link_user_id=link_user_id,
            )

        if platform == "dingtalk":
            token_resp = await client.post(
                "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                json={
                    "clientId": settings.dingtalk_client_id,
                    "clientSecret": settings.dingtalk_client_secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            data = token_resp.json()
            if "accessToken" not in data:
                raise AuthError(f"钉钉登录失败: {data}")
            user_token = data["accessToken"]
            info = await client.get(
                "https://api.dingtalk.com/v1.0/contact/users/me",
                headers={"x-acs-dingtalk-access-token": user_token},
            )
            info_data = info.json()
            union_id = info_data.get("unionId") or info_data.get("openId")
            if not union_id:
                raise AuthError("钉钉未返回用户标识")
            # Robot oToMessages expects userId, while todo APIs need unionId.
            # Resolve the operator's userId once and store it as the account handle.
            legacy = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={
                    "appkey": settings.dingtalk_client_id,
                    "appsecret": settings.dingtalk_client_secret,
                },
            )
            legacy_data = legacy.json()
            if not legacy_data.get("access_token"):
                raise AuthError(f"钉钉应用凭证获取失败: {legacy_data.get('errmsg')}")
            by_union = await client.post(
                "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                params={"access_token": legacy_data["access_token"]},
                json={"unionid": union_id},
            )
            by_union_data = by_union.json()
            if str(by_union_data.get("errcode")) != "0":
                raise AuthError(
                    "钉钉 unionId 换 userId 失败，请确认应用已开通成员信息读权限: "
                    f"{by_union_data.get('errmsg')}"
                )
            user_id = (by_union_data.get("result") or {}).get("userid")
            if not user_id:
                raise AuthError("钉钉未返回 userId")
            return await _upsert_identity(
                session,
                settings=settings,
                platform=platform,
                handle=user_id,
                union_id=union_id,
                display_name=info_data.get("nick", ""),
                email=info_data.get("email"),
                access_token=user_token,
                refresh_token=data.get("refreshToken"),
                expires_in=data.get("expireIn"),
                link_user_id=link_user_id,
            )

        if platform == "wecom":
            token_resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": settings.wecom_corp_id, "corpsecret": settings.wecom_secret},
            )
            token_data = token_resp.json()
            if token_data.get("errcode") != 0:
                raise AuthError(f"企微 token 失败: {token_data.get('errmsg')}")
            app_token = token_data["access_token"]
            info = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
                params={"access_token": app_token, "code": code},
            )
            info_data = info.json()
            if info_data.get("errcode") != 0 or not info_data.get("userid"):
                raise AuthError(f"企微登录失败: {info_data.get('errmsg')}")
            handle = info_data["userid"]
            detail = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                params={"access_token": app_token, "userid": handle},
            )
            detail_data = detail.json()
            return await _upsert_identity(
                session,
                settings=settings,
                platform=platform,
                handle=handle,
                union_id=None,
                display_name=detail_data.get("name", ""),
                email=detail_data.get("email"),
                access_token=None,
                refresh_token=None,
                expires_in=None,
                link_user_id=link_user_id,
            )

    raise AuthError("不支持的平台")


async def user_auth_payload(session: AsyncSession, user: User, settings: Settings) -> dict[str, Any]:
    from .payloads import account_payload, user_payload

    accounts = (
        await session.scalars(select(PlatformAccount).where(PlatformAccount.user_id == user.id))
    ).all()
    return {
        "user": user_payload(user),
        "accounts": [account_payload(account) for account in accounts],
        "demo_mode": settings.demo_mode,
        "dev_login": settings.dev_login,
        "allow_registration": settings.allow_registration,
        "has_password": bool(user.password_hash),
        "configured": {
            "feishu": bool(settings.feishu_app_id and settings.feishu_app_secret),
            "dingtalk": bool(settings.dingtalk_client_id and settings.dingtalk_client_secret),
            "wecom": bool(settings.wecom_corp_id and settings.wecom_secret),
        },
    }
