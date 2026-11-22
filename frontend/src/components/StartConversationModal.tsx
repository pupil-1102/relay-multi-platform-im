import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Search, Send, UserRound } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Contact, Platform } from "../types";
import { cn, platformMeta } from "../utils";
import { useToast } from "../Toast";
import { Modal } from "./Modal";
import { Avatar, PlatformBadge } from "./Primitives";

function manualHandleError(platform: Platform, value: string): string {
  const handle = value.trim();
  if (!handle || platform !== "feishu") return "";
  const digits = handle.replace(/\D/g, "");
  const valid =
    (handle.startsWith("ou_") && handle.length > 8) ||
    (handle.startsWith("oc_") && handle.length > 8) ||
    (handle.includes("@") && handle.split("@").pop()?.includes(".")) ||
    (digits.length === 11 && digits.startsWith("1"));
  if (valid) return "";
  return "飞书请填写 ou_ 开头的 open_id、邮箱或 11 位手机号";
}


export function StartConversationModal({
  onClose,
  onStarted,
}: {
  onClose: () => void;
  onStarted: (conversationId: string) => void;
}) {
  const { me } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [platform, setPlatform] = useState<Platform>("feishu");
  const [q, setQ] = useState("");
  const [contactId, setContactId] = useState("");
  const [manualOpenId, setManualOpenId] = useState("");
  const [manualName, setManualName] = useState("");
  const [text, setText] = useState(`你好，我是${me?.user.display_name || "我"}。`);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [formError, setFormError] = useState("");

  const contacts = useQuery({
    queryKey: ["contacts", "start-chat"],
    queryFn: () => api.contacts(),
  });

  const options = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (contacts.data?.items || []).filter((contact) => {
      if (contact.id === me?.user.id) return false;
      if (!contact.platforms.some((item) => item.platform === platform)) return false;
      if (!needle) return true;
      return `${contact.display_name} ${contact.email || ""}`.toLowerCase().includes(needle);
    });
  }, [contacts.data, me?.user.id, platform, q]);

  useEffect(() => {
    if (options.some((contact) => contact.id === contactId)) return;
    setContactId(options[0]?.id || "");
  }, [contactId, options]);

  const selected = options.find((contact) => contact.id === contactId) || null;
  const selectedHandle = selected?.platforms.find((item) => item.platform === platform)?.handle;
  const manualError = manualHandleError(platform, manualOpenId);

  const sync = async () => {
    setSyncing(true);
    try {
      const result = await api.syncContacts(platform);
      await queryClient.invalidateQueries({ queryKey: ["contacts"] });
      toast.push(
        result.synced > 0 ? `已同步 ${result.synced} 位联系人` : "没有同步到新联系人，请检查通讯录权限范围",
        result.synced > 0 ? "success" : "info",
      );
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "同步联系人失败", "error");
    } finally {
      setSyncing(false);
    }
  };

  const submit = async () => {
    const manual = manualOpenId.trim();
    if (manualError) {
      toast.push(manualError, "error");
      return;
    }
    setFormError("");
    if ((!selected && !manual) || !text.trim()) {
      toast.push("请选择联系人或填写外部联系人 open_id", "error");
      return;
    }
    setBusy(true);
    try {
      const response = await api.startConversation({
        platform,
        contact_id: manual ? undefined : selected?.id,
        handle: manual || undefined,
        display_name: manual ? manualName.trim() || "外部联系人" : undefined,
        text: text.trim(),
        client_msg_id: `start-chat-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      });
      toast.push("已发起私聊", "success");
      onStarted(response.conversation_id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "发起私聊失败";
      setFormError(message);
      toast.push(message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="发起会话"
      subtitle="以你本人的飞书身份，发起 1:1 私聊"
      wide
      footer={
        <>
          <button className="button ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            className="button primary"
            onClick={() => void submit()}
            disabled={busy || (!selected && !manualOpenId.trim()) || !text.trim() || Boolean(manualError)}
          >
            {busy ? <Loader2 size={15} className="spin" /> : <Send size={15} />}
            发起私聊
          </button>
        </>
      }
    >
      <div className="start-chat-platforms">
        {(Object.keys(platformMeta) as Platform[]).map((item) => (
          <button
            key={item}
            className={cn("platform-tab", platform === item && "active")}
            onClick={() => {
              setPlatform(item);
              setContactId("");
              setManualOpenId("");
              setFormError("");
            }}
            style={platform === item ? { color: platformMeta[item].color, borderColor: platformMeta[item].color } : undefined}
          >
            <span className="platform-dot" style={{ background: platformMeta[item].color }} />
            {platformMeta[item].label}
          </button>
        ))}
      </div>

      <div className="start-chat-toolbar">
        <label className="search-input">
          <Search size={14} />
          <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索联系人" />
        </label>
        <button className="button secondary small" onClick={() => void sync()} disabled={syncing}>
          {syncing ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
          同步通讯录
        </button>
      </div>

      <div className="start-chat-list">
        {contacts.isLoading && (
          <div className="list-loading">
            <Loader2 size={16} className="spin" /> 正在加载联系人…
          </div>
        )}
        {!contacts.isLoading && options.length === 0 && (
          <div className="start-chat-empty">
            <UserRound size={22} />
            <strong>没有可选联系人</strong>
            <span>请让企业管理员把联系人加入应用可用范围，或配置飞书通讯录数据权限后点击「同步通讯录」。</span>
          </div>
        )}
        {options.map((contact: Contact) => {
          const handle = contact.platforms.find((item) => item.platform === platform)?.handle;
          return (
            <button
              key={contact.id}
              className={cn("start-chat-contact", contact.id === contactId && "active")}
              onClick={() => setContactId(contact.id)}
            >
              <Avatar name={contact.display_name} size={38} />
              <span className="start-chat-contact-main">
                <strong>{contact.display_name}</strong>
                <span>{handle || contact.email || "未绑定平台账号"}</span>
              </span>
              {contact.id === contactId && <span className="start-chat-check">已选择</span>}
            </button>
          );
        })}
      </div>

      <div className="start-chat-manual">
        <div className="start-chat-manual-head">
          <strong>外部联系人</strong>
          <span>手机号/邮箱仅能解析本企业通讯录用户；外部联系人请填飞书 open_id</span>
        </div>
        <div className="start-chat-manual-fields">
          <input
            value={manualOpenId}
            onChange={(event) => {
              setManualOpenId(event.target.value);
              setFormError("");
            }}
            placeholder={
              platform === "feishu"
                ? "ou_xxx、name@example.com 或 11 位手机号"
                : "平台用户 ID"
            }
            aria-label="外部联系人 open_id 或邮箱"
          />
          <input
            value={manualName}
            onChange={(event) => setManualName(event.target.value)}
            placeholder="显示名称（可选）"
            aria-label="外部联系人显示名称"
          />
        </div>
        {manualError && <p className="start-chat-error">{manualError}</p>}
        {platform === "feishu" && !manualError && manualOpenId.trim() && (
          <p className="start-chat-hint">
            手机号/邮箱会先在飞书通讯录解析；外部联系人查不到时请改用 open_id。
          </p>
        )}
      </div>

      <label className="field start-chat-message">
        <span>首条消息</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
          placeholder="输入第一条消息，发送后会话会出现在左侧列表"
        />
      </label>

      {formError && <p className="start-chat-error start-chat-submit-error">{formError}</p>}

      {selected && selectedHandle && (
        <p className="start-chat-hint">
          将以你的身份通过 <PlatformBadge platform={platform} compact /> 给 <strong>{selected.display_name}</strong> 发私聊。
        </p>
      )}
    </Modal>
  );
}
