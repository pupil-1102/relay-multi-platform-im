import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, FileText, Loader2, Paperclip, Reply, Send, Smile, X } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Conversation, Message } from "../types";
import { formatBytes } from "../utils";
import { useToast } from "../Toast";
import { PlatformBadge } from "./Primitives";

const EMOJIS = [
  "😀", "😄", "😁", "😂", "🤣", "😊", "😍", "🤔", "😎", "😅",
  "👍", "👏", "🙏", "🤝", "💪", "👌", "🎉", "🔥", "✅", "❤️",
  "🚀", "💡", "📌", "😭", "😴", "🤯", "😇", "🤗", "👀", "🙌",
];


export function ConversationComposer({
  conversation,
  replyTo,
  onClearReply,
  onSent,
}: {
  conversation: Conversation;
  replyTo: Message | null;
  onClearReply: () => void;
  onSent?: (message: Message) => void;
}) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [showEmoji, setShowEmoji] = useState(false);
  const [showMentions, setShowMentions] = useState(false);
  const [mentions, setMentions] = useState<{ id: string; name: string }[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const toast = useToast();
  const queryClient = useQueryClient();
  const { me } = useAuth();

  const canMention =
    conversation.chat_type === "group" &&
    (conversation.platform === "feishu" || conversation.platform === "dingtalk");
  const ownHandle = me?.accounts.find((account) => account.platform === conversation.platform)?.platform_user_id;
  const membersQuery = useQuery({
    queryKey: ["conversation-members", conversation.id],
    queryFn: () => api.conversationMembers(conversation.id),
    enabled: canMention && showMentions,
  });

  const members = (membersQuery.data?.items || []).filter((member) => member.id !== ownHandle);
  const hasOtherMembers = (membersQuery.data?.items || []).some((member) => member.id !== ownHandle);

  useEffect(() => {
    setText("");
    setFile(null);
    setMentions([]);
    setShowEmoji(false);
    setShowMentions(false);
    if (fileInput.current) fileInput.current.value = "";
  }, [conversation.id]);

  const insertAtCursor = (value: string) => {
    const input = textareaRef.current;
    const start = input?.selectionStart ?? text.length;
    const end = input?.selectionEnd ?? text.length;
    const next = text.slice(0, start) + value + text.slice(end);
    setText(next);
    requestAnimationFrame(() => {
      input?.focus();
      const cursor = start + value.length;
      input?.setSelectionRange(cursor, cursor);
    });
  };

  const insertEmoji = (emoji: string) => {
    insertAtCursor(emoji);
    setShowEmoji(false);
  };

  const insertMention = (member: { id: string; name: string }) => {
    insertAtCursor(`@${member.name} `);
    setMentions((current) =>
      current.some((item) => item.id === member.id) ? current : [...current, member],
    );
    setShowMentions(false);
  };

  const submit = async () => {
    if (!text.trim() && !file) return;
    setBusy(true);
    try {
      let payload: Message;
      if (file) {
        if (replyTo) {
          toast.push("文件发送暂不支持引用回复", "info");
          return;
        }
        if (file.size > 50 * 1024 * 1024) {
          toast.push("文件不能超过 50MB", "error");
          return;
        }
        const form = new FormData();
        form.append("file", file, file.name);
        form.append("text", text.trim());
        form.append("client_msg_id", `web-file-${Date.now()}-${Math.random().toString(16).slice(2)}`);
        payload = await api.sendConversationFile(conversation.id, form);
      } else {
        payload = await api.sendConversation(conversation.id, {
          text: text.trim(),
          reply_to_message_id: replyTo?.id,
          client_msg_id: `web-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          mentions: mentions.length ? mentions : undefined,
        });
      }
      setText("");
      setFile(null);
      setMentions([]);
      setShowEmoji(false);
      setShowMentions(false);
      if (fileInput.current) fileInput.current.value = "";
      onClearReply();
      onSent?.(payload);
      await queryClient.invalidateQueries({ queryKey: ["conversation-messages", conversation.id] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
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
    <div className="chat-composer">
      <div className="chat-composer-target">
        <span>
          发送到 <strong>{conversation.title}</strong>
        </span>
        <PlatformBadge platform={conversation.platform} compact />
      </div>

      {replyTo && (
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
      )}

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

      {conversation.platform === "wecom" && conversation.chat_type === "group" && (
        <div className="composer-platform-note">
          企业微信 aibot 群聊仅接收 @机器人 文本；群图片/文件请发机器人单聊。
        </div>
      )}

      <div className="chat-composer-box">
        <div className="composer-actions">
          <button
            type="button"
            className="composer-action-button"
            onClick={() => {
              setShowEmoji((value) => !value);
              setShowMentions(false);
            }}
            title="发送表情"
          >
            <Smile size={16} /> 表情
          </button>
          {canMention && (
            <button
              type="button"
              className="composer-action-button"
              onClick={() => {
                setShowMentions((value) => !value);
                setShowEmoji(false);
              }}
              title="提及群成员"
            >
              <AtSign size={16} /> @人
            </button>
          )}
          {mentions.length > 0 && (
            <div className="composer-mentions">
              {mentions.map((mention) => (
                <span key={mention.id} className="composer-mention-chip">
                  @{mention.name}
                  <button
                    type="button"
                    onClick={() => setMentions((current) => current.filter((item) => item.id !== mention.id))}
                    aria-label={`移除 ${mention.name}`}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        {showEmoji && (
          <div className="composer-popover emoji-popover">
            <div className="emoji-grid">
              {EMOJIS.map((emoji) => (
                <button
                  type="button"
                  key={emoji}
                  className="emoji-button"
                  onClick={() => insertEmoji(emoji)}
                >
                  {emoji}
                </button>
              ))}
            </div>
          </div>
        )}

        {showMentions && (
          <div className="composer-popover mention-popover">
            <div className="composer-popover-head">
              <strong>提及群成员</strong>
              <button type="button" className="icon-button small" onClick={() => setShowMentions(false)}>
                <X size={13} />
              </button>
            </div>
            {membersQuery.isLoading && (
              <div className="composer-popover-empty">
                <Loader2 size={14} className="spin" /> 正在加载成员…
              </div>
            )}
            {!membersQuery.isLoading && membersQuery.data?.error && (
              <div className="composer-popover-empty">{membersQuery.data.error}</div>
            )}
            {!membersQuery.isLoading && !membersQuery.data?.error && members.length === 0 && (
              <div className="composer-popover-empty">
                {hasOtherMembers ? "没有可提及的群成员" : "群里除你之外没有其他成员"}
              </div>
            )}
            <div className="mention-list">
              {members.map((member) => (
                <button
                  type="button"
                  key={member.id}
                  className="mention-option"
                  onClick={() => insertMention(member)}
                >
                  <span className="mention-avatar">{member.name.slice(0, 1)}</span>
                  <span>{member.name}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <textarea
          ref={textareaRef}
          className="composer-text"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`发消息到 ${conversation.title}…`}
          rows={2}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer-footer">
          <div className="composer-tools">
            <button
              className="attach-button"
              onClick={() => fileInput.current?.click()}
              type="button"
              title="发送文件"
            >
              <Paperclip size={15} /> 文件
            </button>
            <span className="composer-tip">Enter 发送 · Shift+Enter 换行</span>
          </div>
          <button
            className="button primary"
            disabled={busy || (!text.trim() && !file)}
            onClick={() => void submit()}
          >
            {busy ? <Loader2 size={15} className="spin" /> : <Send size={15} />}
            发送
          </button>
        </div>
      </div>

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
    </div>
  );
}
