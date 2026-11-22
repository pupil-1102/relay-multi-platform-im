import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, Paperclip, Reply, Send, X } from "lucide-react";
import { api } from "../api";
import type { Message, Platform } from "../types";
import { formatBytes, platformMeta, cn } from "../utils";
import { useToast } from "../Toast";
import { PlatformBadge } from "./Primitives";

export function Composer({
  replyTo,
  onClearReply,
  onSent,
}: {
  replyTo?: Message | null;
  onClearReply?: () => void;
  onSent?: (message: Message) => void;
}) {
  const [platform, setPlatform] = useState<Platform>("feishu");
  const [contactId, setContactId] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (replyTo) setPlatform(replyTo.platform);
  }, [replyTo]);

  const contacts = useQuery({
    queryKey: ["contacts", platform],
    queryFn: () => api.contacts({ platform }),
  });

  const options = useMemo(() => contacts.data?.items || [], [contacts.data]);
  useEffect(() => {
    if (!options.some((contact) => contact.id === contactId)) {
      setContactId(options[0]?.id || "");
    }
  }, [options, contactId]);

  const submit = async () => {
    if (!text.trim() && !file) return;
    setBusy(true);
    try {
      let payload: Message;
      if (file) {
        if (replyTo) {
          toast.push("文件发送暂不支持引用回复，请直接发送给联系人", "info");
          setBusy(false);
          return;
        }
        if (file.size > 50 * 1024 * 1024) {
          toast.push("文件不能超过 50MB", "error");
          setBusy(false);
          return;
        }
        const form = new FormData();
        form.append("platform", platform);
        form.append("to_user_id", contactId);
        form.append("text", text.trim());
        form.append("client_msg_id", `web-file-${Date.now()}-${Math.random().toString(16).slice(2)}`);
        form.append("file", file, file.name);
        payload = await api.sendFile(form);
      } else if (replyTo) {
        payload = await api.send({
          platform: replyTo.platform,
          to_handle: replyTo.platform_chat_id || replyTo.platform_chat_id || undefined,
          text: text.trim(),
          reply_to_message_id: replyTo.id,
          client_msg_id: `web-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        });
      } else {
        payload = await api.send({
          platform,
          to_user_id: contactId,
          text: text.trim(),
          client_msg_id: `web-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        });
      }
      setText("");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      onClearReply?.();
      onSent?.(payload);
      await queryClient.invalidateQueries({ queryKey: ["messages"] });
      await queryClient.invalidateQueries({ queryKey: ["files"] });
      toast.push(file ? "文件已发送" : "消息已发送", "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "发送失败", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="composer card">
      <div className="composer-head">
        <div>
          <h3>发送消息</h3>
          <p>选择平台与联系人，经统一格式转换后由对应 Adapter 发出</p>
        </div>
        <span className="composer-hint">文本 · 文件</span>
      </div>

      {replyTo ? (
        <div className="reply-banner">
          <Reply size={14} />
          <div>
            <strong>回复 {replyTo.sender_name || "原消息"}</strong>
            <span>{replyTo.text || replyTo.files[0]?.filename || "附件消息"}</span>
          </div>
          <button className="icon-button small" onClick={onClearReply} aria-label="取消回复">
            <X size={14} />
          </button>
        </div>
      ) : (
        <div className="platform-tabs">
          {(Object.keys(platformMeta) as Platform[]).map((item) => (
            <button
              key={item}
              className={cn("platform-tab", platform === item && "active")}
              onClick={() => setPlatform(item)}
              style={platform === item ? { color: platformMeta[item].color, borderColor: platformMeta[item].color } : undefined}
            >
              <span className="platform-dot" style={{ background: platformMeta[item].color }} />
              {platformMeta[item].label}
            </button>
          ))}
        </div>
      )}

      {!replyTo && (
        <label className="field compact">
          <span>收件人</span>
          <select value={contactId} onChange={(event) => setContactId(event.target.value)}>
            {options.length === 0 && <option value="">该平台暂无可选联系人</option>}
            {options.map((contact) => (
              <option key={contact.id} value={contact.id}>
                {contact.display_name} · {contact.platforms.find((item) => item.platform === platform)?.handle}
              </option>
            ))}
          </select>
        </label>
      )}

      <textarea
        className="composer-text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={replyTo ? "写下回复内容…发送时会引用原消息" : "输入要发送的文本消息…"}
        rows={3}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            void submit();
          }
        }}
      />

      {file && (
        <div className="composer-file">
          <span className="file-icon">
            <FileText size={15} />
          </span>
          <span className="composer-file-info">
            <strong>{file.name}</strong>
            <span>{formatBytes(file.size)}</span>
          </span>
          <button className="icon-button small" onClick={() => setFile(null)} aria-label="移除附件">
            <X size={14} />
          </button>
        </div>
      )}

      <input
        ref={fileInput}
        type="file"
        hidden
        onChange={(event) => {
          const selected = event.target.files?.[0] || null;
          if (selected && selected.size > 50 * 1024 * 1024) {
            toast.push("文件不能超过 50MB", "error");
            event.target.value = "";
            return;
          }
          setFile(selected);
        }}
      />

      <div className="composer-footer">
        <div className="composer-tools">
          {!replyTo && (
            <button className="attach-button" onClick={() => fileInput.current?.click()} type="button">
              <Paperclip size={14} /> 附件
            </button>
          )}
          <span className="composer-tip">⌘/Ctrl + Enter 发送</span>
        </div>
        <button
          className="button primary"
          disabled={busy || (!text.trim() && !file) || (!replyTo && !contactId)}
          onClick={() => void submit()}
        >
          {busy ? <Loader2 size={15} className="spin" /> : <Send size={15} />}
          发送
        </button>
      </div>
    </section>
  );
}
