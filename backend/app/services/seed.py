from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import (
    Direction,
    FileRecord,
    FileStatus,
    FileView,
    Message,
    PlatformAccount,
    SyncState,
    Task,
    TaskLink,
    TaskOrigin,
    TaskStatus,
    User,
)
from ..platform.mock import DEMO_SVG
from .auth import ensure_demo_user
from .files import save_bytes

PDF_BYTES = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 78>>stream
BT /F1 20 Tf 72 760 Td (Relay demo document) Tj 0 -30 Td /F1 11 Tf (Unified inbox / task loop) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f 
0000000010 00000 n 
0000000053 00000 n 
0000000100 00000 n 
0000000190 00000 n 
0000000317 00000 n 
trailer<</Size 6/Root 1 0 R>>
startxref
386
%%EOF
"""


async def seed_demo(session: AsyncSession, settings: Settings, adapters) -> None:
    existing = await session.scalar(select(Message).limit(1))
    if existing:
        return

    owner = await ensure_demo_user(session, settings)
    now = datetime.now(UTC)

    contacts = [
        ("周予安", "zhou.yuan@relay.local", "feishu", "ou_bob", "on_bob", "周予安"),
        ("周予安", "zhou.yuan@relay.local", "dingtalk", "bob_ding", "union_bob", "周予安"),
        ("陈可", "chen.ke@relay.local", "dingtalk", "carol_ding", "union_carol", "陈可"),
        ("陈可", "chen.ke@relay.local", "wecom", "chen.ke", None, "陈可"),
        ("顾南", "gu.nan@relay.local", "wecom", "gu.nan", None, "顾南"),
        ("顾南", "gu.nan@relay.local", "feishu", "ou_dave", "on_dave", "顾南"),
        ("苏禾", "su.he@relay.local", "feishu", "ou_erin", "on_erin", "苏禾"),
    ]
    user_by_email: dict[str, User] = {}
    for display_name, email, platform, handle, union, account_name in contacts:
        user = user_by_email.get(email)
        if user is None:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name=display_name)
                session.add(user)
                await session.flush()
            user_by_email[email] = user
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform,
                PlatformAccount.platform_user_id == handle,
            )
        )
        if account is None:
            session.add(
                PlatformAccount(
                    user_id=user.id,
                    platform=platform,
                    platform_user_id=handle,
                    union_id=union,
                    display_name=account_name,
                    email=email,
                    status="connected",
                )
            )
    await session.flush()

    bob = user_by_email["zhou.yuan@relay.local"]
    carol = user_by_email["chen.ke@relay.local"]
    dave = user_by_email["gu.nan@relay.local"]
    erin = user_by_email["su.he@relay.local"]

    def file_bytes(kind: str) -> tuple[bytes, str, str]:
        if kind == "budget":
            data = b"PK\x03\x04relay-demo-xlsx-placeholder\n" + b"Q4 budget\n" * 40
            return data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Q4预算表.xlsx"
        if kind == "roadmap":
            return DEMO_SVG, "image/svg+xml", "产品路线图-2026.svg"
        if kind == "review":
            data = "议题,负责人,时间\n版本范围,苏禾,10:00\n灰度计划,顾南,10:20\n回滚预案,周予安,10:40\n".encode()
            return data, "text/csv", "评审材料.csv"
        return PDF_BYTES, "application/pdf", "合同摘要.pdf"

    async def add_file(message: Message, kind: str, *, opened: bool = False) -> FileRecord:
        data, mime, filename = file_bytes(kind)
        key, digest, size = save_bytes(settings, data, filename)
        record = FileRecord(
            owner_id=owner.id,
            message_id=message.id,
            platform=message.platform,
            platform_file_key=f"file_{kind}_{message.platform}",
            filename=filename,
            mime_type=mime,
            size_bytes=size,
            sha256=digest,
            storage_key=key,
            download_status=FileStatus.READY,
            created_at=message.created_at,
        )
        session.add(record)
        await session.flush()
        adapter = adapters.get(message.platform)
        if adapter is not None and hasattr(adapter, "seed_file"):
            adapter.seed_file(record.platform_file_key, data)
        if opened:
            session.add(
                FileView(
                    file_id=record.id,
                    user_id=owner.id,
                    first_opened_at=now - timedelta(minutes=20),
                    last_opened_at=now - timedelta(minutes=8),
                    open_count=3,
                )
            )
        return record

    # ---- inbound messages -------------------------------------------------
    m1 = Message(
        owner_id=owner.id,
        platform="feishu",
        direction=Direction.INBOUND,
        platform_msg_id="om_budget_001",
        platform_chat_id="oc_finance",
        chat_type="group",
        sender_platform_id="ou_bob",
        sender_name="周予安",
        sender_user_id=bob.id,
        content_type="file",
        text="Q4 预算表我更新了一版，麻烦今天下班前确认一下。重点看市场费用和差旅两项。",
        created_at=now - timedelta(hours=2, minutes=10),
        delivery_status="received",
    )
    session.add(m1)
    await session.flush()
    await add_file(m1, "budget")

    m2 = Message(
        owner_id=owner.id,
        platform="dingtalk",
        direction=Direction.INBOUND,
        platform_msg_id="dt_contract_msg_001",
        platform_chat_id="cid_contract",
        chat_type="group",
        sender_platform_id="carol_ding",
        sender_name="陈可",
        sender_user_id=carol.id,
        content_type="text",
        text="客户续约合同走到法务了，需要你确认付款条款。合同摘要我放在附件里，今天能给个结论吗？",
        created_at=now - timedelta(hours=1, minutes=48),
        delivery_status="received",
    )
    session.add(m2)
    await session.flush()
    await add_file(m2, "contract")

    m3 = Message(
        owner_id=owner.id,
        platform="wecom",
        direction=Direction.INBOUND,
        platform_msg_id="wc_server_msg_001",
        platform_chat_id="gu.nan",
        chat_type="direct",
        sender_platform_id="gu.nan",
        sender_name="顾南",
        sender_user_id=dave.id,
        content_type="text",
        text="服务器扩容申请已经提了，帮我看下监控阈值要不要一起调整。",
        created_at=now - timedelta(hours=1, minutes=23),
        delivery_status="received",
    )
    session.add(m3)
    await session.flush()

    m4 = Message(
        owner_id=owner.id,
        platform="feishu",
        direction=Direction.INBOUND,
        platform_msg_id="om_roadmap_001",
        platform_chat_id="oc_product",
        chat_type="direct",
        sender_platform_id="ou_erin",
        sender_name="苏禾",
        sender_user_id=erin.id,
        content_type="image",
        text="新版路线图，重点看第二季度。",
        created_at=now - timedelta(minutes=52),
        delivery_status="received",
    )
    session.add(m4)
    await session.flush()
    await add_file(m4, "roadmap", opened=True)

    m5 = Message(
        owner_id=owner.id,
        platform="dingtalk",
        direction=Direction.INBOUND,
        platform_msg_id="dt_review_msg_001",
        platform_chat_id="cid_review",
        chat_type="group",
        sender_platform_id="bob_ding",
        sender_name="周予安",
        sender_user_id=bob.id,
        content_type="file",
        text="明早 10 点评审，材料在这里，提前看一下。",
        created_at=now - timedelta(minutes=31),
        delivery_status="received",
    )
    session.add(m5)
    await session.flush()
    await add_file(m5, "review")

    m6 = Message(
        owner_id=owner.id,
        platform="wecom",
        direction=Direction.INBOUND,
        platform_msg_id="wc_todo_msg_001",
        platform_chat_id="chen.ke",
        chat_type="direct",
        sender_platform_id="chen.ke",
        sender_name="陈可",
        sender_user_id=carol.id,
        content_type="text",
        text="企微这个待办麻烦同步一下状态，客户在等。",
        created_at=now - timedelta(minutes=12),
        delivery_status="received",
    )
    session.add(m6)

    out1 = Message(
        owner_id=owner.id,
        platform="feishu",
        direction=Direction.OUTBOUND,
        platform_msg_id="om_out_001",
        platform_chat_id="oc_finance",
        chat_type="group",
        sender_name="我",
        receiver_platform_id="ou_bob",
        content_type="text",
        text="收到，我下午看完给你结论。",
        created_at=now - timedelta(hours=1, minutes=55),
        delivery_status="sent",
    )
    session.add(out1)
    await session.flush()

    # ---- unified tasks ----------------------------------------------------
    t1 = Task(
        owner_id=owner.id,
        title="确认 Q4 预算表",
        description="重点核对市场费用与差旅两项，今天下班前给周予安结论。",
        status=TaskStatus.OPEN,
        source_platform="feishu",
        source_message_id=m1.id,
        origin=TaskOrigin.MESSAGE,
        assignee_id=owner.id,
        due_at=now + timedelta(hours=5),
        sync_state=SyncState.SYNCED,
    )
    t2 = Task(
        owner_id=owner.id,
        title="审核客户续约合同付款条款",
        description="法务已过稿，需要确认付款节点和违约金比例。",
        status=TaskStatus.IN_PROGRESS,
        source_platform="dingtalk",
        source_message_id=m2.id,
        origin=TaskOrigin.MESSAGE,
        assignee_id=owner.id,
        due_at=now + timedelta(hours=3),
        sync_state=SyncState.SYNCED,
    )
    t3 = Task(
        owner_id=owner.id,
        title="发布 v2.3 版本",
        description="跨平台发布任务：飞书与钉钉同时跟踪，任一侧完成即同步。",
        status=TaskStatus.OPEN,
        origin=TaskOrigin.MANUAL,
        assignee_id=owner.id,
        due_at=now + timedelta(days=2),
        sync_state=SyncState.SYNCED,
    )
    t4 = Task(
        owner_id=owner.id,
        title="跟进服务器扩容申请",
        description="企微侧只支持更新已关联待办；状态通过轮询回流。",
        status=TaskStatus.OPEN,
        source_platform="wecom",
        source_message_id=m3.id,
        origin=TaskOrigin.MESSAGE,
        assignee_id=owner.id,
        due_at=now + timedelta(days=1),
        sync_state=SyncState.SYNCED,
    )
    session.add_all([t1, t2, t3, t4])
    await session.flush()

    m1.task_id = t1.id
    m2.task_id = t2.id
    m3.task_id = t4.id

    links = [
        TaskLink(
            task_id=t1.id,
            platform="feishu",
            platform_task_id="ft_budget_001",
            platform_status="open",
            last_synced_at=now - timedelta(hours=1),
        ),
        TaskLink(
            task_id=t2.id,
            platform="dingtalk",
            platform_task_id="dt_contract_001",
            platform_status="in_progress",
            last_synced_at=now - timedelta(minutes=50),
        ),
        TaskLink(
            task_id=t3.id,
            platform="feishu",
            platform_task_id="ft_release_002",
            platform_status="open",
            last_synced_at=now - timedelta(hours=3),
        ),
        TaskLink(
            task_id=t3.id,
            platform="dingtalk",
            platform_task_id="dt_release_002",
            platform_status="open",
            last_synced_at=now - timedelta(hours=3),
        ),
        TaskLink(
            task_id=t4.id,
            platform="wecom",
            platform_task_id="wc_todo_001",
            platform_status="open",
            last_synced_at=now - timedelta(minutes=40),
        ),
    ]
    session.add_all(links)

    for platform, task_id, status in [
        ("feishu", "ft_budget_001", "open"),
        ("dingtalk", "dt_contract_001", "in_progress"),
        ("feishu", "ft_release_002", "open"),
        ("dingtalk", "dt_release_002", "open"),
        ("wecom", "wc_todo_001", "open"),
    ]:
        adapter = adapters.get(platform)
        if adapter is not None and hasattr(adapter, "seed_task"):
            adapter.seed_task(task_id, status)

    await session.commit()
