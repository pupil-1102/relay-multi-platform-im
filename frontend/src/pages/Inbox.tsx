import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Inbox as InboxIcon,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
  UserRound,
  Users,
} from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Conversation, FileItem, Message, Platform, Task } from "../types";
import { cn, conversationId, formatDateLabel, formatTime, platformMeta } from "../utils";
import { useToast } from "../Toast";
import { ConversationComposer } from "../components/ConversationComposer";
import { CreateTaskModal } from "../components/CreateTaskModal";
import { FilePreview } from "../components/FilePreview";
import { MessageItem } from "../components/MessageItem";
import { StartConversationModal } from "../components/StartConversationModal";
import { Avatar, EmptyState, PlatformBadge } from "../components/Primitives";

const platforms: Array<"all" | Platform> = ["all", "feishu", "dingtalk", "wecom"];

export function InboxPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus");
  const platformParam = params.get("platform");
  const initialPlatform: "all" | Platform =
    platformParam === "feishu" || platformParam === "dingtalk" || platformParam === "wecom"
      ? platformParam
      : "all";
  const [platform, setPlatform] = useState<"all" | Platform>(initialPlatform);
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(params.get("chat"));
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [createFor, setCreateFor] = useState<Message | null>(null);
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const conversationsQuery = useQuery({
    queryKey: ["conversations", platform, q],
    queryFn: () =>
      api.conversations({
        platform: platform === "all" ? undefined : platform,
        q: q || undefined,
      }),
    refetchInterval: 5000,
  });

  const focusMessageQuery = useQuery({
    queryKey: ["message-focus", focus],
    queryFn: () => api.message(focus!),
    enabled: Boolean(focus),
  });

  const messagesQuery = useQuery({
    queryKey: ["conversation-messages", selectedId],
    queryFn: () => api.conversationMessages(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: 5000,
  });

  const conversations = conversationsQuery.data?.items || [];
  const linkedPlatforms = new Set((me?.accounts || []).map((account) => account.platform));
  const selected = useMemo(
    () => conversations.find((item) => item.id === selectedId) || null,
    [conversations, selectedId],
  );
  const messages = messagesQuery.data?.items || [];

  const groups = useMemo(() => {
    const result: { label: string; messages: Message[] }[] = [];
    for (const message of messages) {
      const label = formatDateLabel(message.created_at);
      const last = result[result.length - 1];
      if (last && last.label === label) last.messages.push(message);
      else result.push({ label, messages: [message] });
    }
    return result;
  }, [messages]);

  useEffect(() => {
    if (conversations.length === 0) return;
    if (selectedId) return;
    setSelectedId(conversations[0].id);
  }, [conversations, selectedId]);

  useEffect(() => {
    const message = focusMessageQuery.data;
    if (!message?.platform_chat_id) return;
    setSelectedId(conversationId(message.platform, message.platform_chat_id));
  }, [focusMessageQuery.data]);

  useEffect(() => {
    setReplyTo(null);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    void (async () => {
      try {
        await api.markConversationRead(selectedId);
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        await queryClient.invalidateQueries({ queryKey: ["overview"] });
      } catch {
        /* keep the badge if marking read fails */
      }
    })();
  }, [messages.length, queryClient, selectedId]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    if (focus) {
      const timer = window.setTimeout(() => {
        const element = document.getElementById(`message-${focus}`);
        if (!element) return;
        element.scrollIntoView({ behavior: "smooth", block: "center" });
        setHighlighted(focus);
      }, 180);
      const clear = window.setTimeout(() => setHighlighted(null), 4200);
      return () => {
        window.clearTimeout(timer);
        window.clearTimeout(clear);
      };
    }
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }, [focus, messages.length, selectedId]);

  const selectConversation = (conversation: Conversation) => {
    setSelectedId(conversation.id);
    const next = new URLSearchParams(params);
    next.set("chat", conversation.id);
    next.delete("focus");
    setParams(next, { replace: true });
  };

  const clearFocus = () => {
    const next = new URLSearchParams(params);
    next.delete("focus");
    setParams(next, { replace: true });
  };

  const loginPlatform = (item: Platform) => {
    if (!me?.configured?.[item]) {
      toast.push(`${platformMeta[item].label} OAuth 尚未配置`, "info");
      return;
    }
    const next = `/?platform=${item}`;
    window.location.href = `/api/auth/${item}/start?next=${encodeURIComponent(next)}`;
  };

  const choosePlatform = (item: "all" | Platform) => {
    if (item !== "all" && !linkedPlatforms.has(item)) {
      loginPlatform(item);
      return;
    }
    setPlatform(item);
    setSelectedId(null);
    const next = new URLSearchParams(params);
    if (item === "all") next.delete("platform");
    else next.set("platform", item);
    setParams(next, { replace: true });
  };

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    if (selectedId) {
      void queryClient.invalidateQueries({ queryKey: ["conversation-messages", selectedId] });
    }
  };

  const onCreatedTask = async (task: Task) => {
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    await queryClient.invalidateQueries({ queryKey: ["conversation-messages"] });
    await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    await queryClient.invalidateQueries({ queryKey: ["overview"] });
    toast.push("已创建待办，可前往待办页处理", "success");
    setCreateFor(null);
    navigate(`/tasks?focus=${task.id}`);
  };

  return (
    <div className="page conversation-page">
      <header className="page-head conversation-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">CONVERSATIONS</span>
            <span className="live-pill">
              <span className="live-dot online" /> 实时
            </span>
          </div>
          <h1>会话</h1>
          <p>每个群聊、私聊独立成一条会话。打开会话查看上下文，并直接在对话里回复。</p>
        </div>
        <div className="page-actions">
          <button className="button secondary" onClick={refresh} title="刷新">
            <RefreshCw size={15} /> 刷新
          </button>
        </div>
      </header>

      {focus && (
        <div className="focus-banner">
          <MessageSquare size={15} />
          正在定位原消息
          <button className="text-button" onClick={clearFocus}>
            取消定位
          </button>
        </div>
      )}

      <div className="conversation-layout">
        <aside className="conversation-sidebar card">
          <div className="conversation-sidebar-head">
            <div>
              <h2>全部会话</h2>
              <span>{conversations.length} 个</span>
            </div>
            <div className="conversation-sidebar-actions">
              <button
                className="button primary small"
                onClick={() => setStartOpen(true)}
                title="发起私聊"
              >
                <Plus size={14} /> 发起
              </button>
              <button className="icon-button small" onClick={refresh} title="刷新会话">
                <RefreshCw size={14} />
              </button>
            </div>
          </div>

          <div className="conversation-filters">
            <div className="conversation-chips">
              {platforms.map((item) => (
                <button
                  key={item}
                  className={cn("conversation-chip", platform === item && "active")}
                  onClick={() => choosePlatform(item)}
                >
                  {item === "all" ? (
                    "全部"
                  ) : (
                    <>
                      <span className="platform-dot" style={{ background: platformMeta[item].color }} />
                      {platformMeta[item].label}
                      {!linkedPlatforms.has(item) && (
                        <span className="platform-login-hint">
                          {me?.configured?.[item] ? "登录" : "未配置"}
                        </span>
                      )}
                    </>
                  )}
                </button>
              ))}
            </div>
            <label className="search-input conversation-search">
              <Search size={14} />
              <input
                value={q}
                onChange={(event) => setQ(event.target.value)}
                placeholder="搜索会话"
              />
              {q && <button onClick={() => setQ("")}>✕</button>}
            </label>
          </div>

          <div className="conversation-scroll">
            {conversationsQuery.isLoading && (
              <div className="list-loading">
                <Loader2 className="spin" size={18} /> 正在加载会话…
              </div>
            )}
            {conversationsQuery.isError && (
              <div className="list-error">加载失败：{(conversationsQuery.error as Error).message}</div>
            )}
            {!conversationsQuery.isLoading && conversations.length === 0 && (
              <EmptyState
                title="还没有会话"
                description="机器人收到飞书、钉钉或企业微信消息后，群聊和私聊会分别出现在这里。"
                icon={<InboxIcon size={24} />}
              />
            )}
            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                className={cn("conversation-item", selectedId === conversation.id && "active")}
                onClick={() => selectConversation(conversation)}
              >
                <Avatar
                  name={conversation.title}
                  size={42}
                  muted={conversation.chat_type !== "group"}
                />
                <div className="conversation-item-main">
                  <div className="conversation-item-top">
                    <strong>{conversation.title}</strong>
                    <span className="conversation-item-end">
                      {conversation.unread_count > 0 && (
                        <span className="conversation-unread">
                          {conversation.unread_count > 99 ? "99+" : conversation.unread_count}
                        </span>
                      )}
                      <span className="conversation-time">{formatTime(conversation.last_at)}</span>
                    </span>
                  </div>
                  <div className="conversation-item-sub">
                    <span className="conversation-platform-dot" style={{ background: platformMeta[conversation.platform].color }} />
                    <span className="conversation-preview">
                      {conversation.last_sender_name && (
                        <em>{conversation.last_sender_name === "我" ? "我" : conversation.last_sender_name}：</em>
                      )}
                      {conversation.last_message}
                    </span>
                  </div>
                  <div className="conversation-item-meta">
                    <span>{conversation.chat_type === "group" ? "群聊" : "私聊"}</span>
                    <span>{conversation.message_count} 条消息</span>
                    {me?.demo_mode && <span>演示</span>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="chat-panel card">
          {selected ? (
            <>
              <header className="chat-header">
                <button
                  className="icon-button small chat-back"
                  onClick={() => setSelectedId(null)}
                  aria-label="返回会话列表"
                >
                  <ArrowLeft size={16} />
                </button>
                <Avatar name={selected.title} size={42} muted={selected.chat_type !== "group"} />
                <div className="chat-title">
                  <h2>{selected.title}</h2>
                  <div>
                    <PlatformBadge platform={selected.platform} compact />
                    <span className="chat-type">
                      {selected.chat_type === "group" ? <Users size={13} /> : <UserRound size={13} />}
                      {selected.chat_type === "group" ? "群聊" : "私聊"}
                    </span>
                    <span className="chat-count">{selected.message_count} 条消息</span>
                  </div>
                </div>
                <button className="icon-button small" onClick={refresh} title="刷新消息">
                  <RefreshCw size={15} />
                </button>
              </header>

              <div className="chat-history" ref={scrollRef}>
                {messagesQuery.isLoading && (
                  <div className="list-loading">
                    <Loader2 className="spin" size={18} /> 正在加载消息…
                  </div>
                )}
                {messagesQuery.isError && (
                  <div className="list-error">加载失败：{(messagesQuery.error as Error).message}</div>
                )}
                {!messagesQuery.isLoading && messages.length === 0 && (
                  <EmptyState
                    title="这个会话还没有消息"
                    description="在下方输入内容，发送到当前群聊或私聊。"
                    icon={<MessageSquare size={24} />}
                  />
                )}
                {groups.map((group) => (
                  <div className="message-group" key={group.label}>
                    <div className="date-divider">
                      <span>{group.label}</span>
                    </div>
                    {group.messages.map((message) => (
                      <MessageItem
                        key={message.id}
                        message={message}
                        highlight={highlighted === message.id}
                        onReply={(item) => {
                          setReplyTo(item);
                          setCreateFor(null);
                          document.querySelector(".chat-composer")?.scrollIntoView({
                            behavior: "smooth",
                            block: "end",
                          });
                        }}
                        onCreateTask={(item) => setCreateFor(item)}
                        onOpenTask={(taskId) => navigate(`/tasks?focus=${taskId}`)}
                        onOpenFile={(fileId) => {
                          const file = message.files.find((item) => item.id === fileId);
                          if (file) setPreview(file);
                        }}
                      />
                    ))}
                  </div>
                ))}
              </div>

              <ConversationComposer
                conversation={selected}
                replyTo={replyTo}
                onClearReply={() => setReplyTo(null)}
                onSent={() => {
                  requestAnimationFrame(() => {
                    const container = scrollRef.current;
                    if (container) container.scrollTop = container.scrollHeight;
                  });
                }}
              />
            </>
          ) : (
            <EmptyState
              title="选择一个会话"
              description="左侧每个群聊和私聊都是独立的会话，打开后即可查看上下文并直接回复。"
              icon={<MessageSquare size={28} />}
            />
          )}
        </section>
      </div>

      {createFor && (
        <CreateTaskModal
          message={createFor}
          onClose={() => setCreateFor(null)}
          onCreated={(task) => void onCreatedTask(task)}
        />
      )}
      {startOpen && (
        <StartConversationModal
          onClose={() => setStartOpen(false)}
          onStarted={(conversationId) => {
            setStartOpen(false);
            setSelectedId(conversationId);
            setParams({ chat: conversationId });
            void queryClient.invalidateQueries({ queryKey: ["conversations"] });
            void queryClient.invalidateQueries({ queryKey: ["conversation-messages", conversationId] });
          }}
        />
      )}
      {preview && (
        <FilePreview
          file={preview}
          onClose={() => setPreview(null)}
          onLocate={(messageId) => {
            setPreview(null);
            const message = messages.find((item) => item.id === messageId);
            const chatId = message?.platform_chat_id;
            if (chatId && message) {
              const id = conversationId(message.platform, chatId);
              setSelectedId(id);
              setParams({ chat: id, focus: messageId });
            }
          }}
        />
      )}
    </div>
  );
}
