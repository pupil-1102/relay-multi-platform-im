import { forwardRef } from "react";
import {
  AlertCircle,
  CheckCheck,
  FileText,
  ListPlus,
  MessageSquareReply,
  Paperclip,
  Rows3,
} from "lucide-react";
import type { Message } from "../types";
import { cn, formatTime, platformMeta, statusMeta } from "../utils";
import { Avatar, PlatformBadge, StatusPill } from "./Primitives";

interface Props {
  message: Message;
  highlight?: boolean;
  onReply: (message: Message) => void;
  onCreateTask: (message: Message) => void;
  onOpenTask: (taskId: string) => void;
  onOpenFile: (fileId: string) => void;
}

export const MessageItem = forwardRef<HTMLDivElement, Props>(function MessageItem(
  { message, highlight, onReply, onCreateTask, onOpenTask, onOpenFile },
  ref,
) {
  const outbound = message.direction === "outbound";
  const imageFile = message.files.find((file) => file.mime_type.startsWith("image/"));
  const otherFiles = message.files.filter((file) => !file.mime_type.startsWith("image/"));
  return (
    <div
      ref={ref}
      id={`message-${message.id}`}
      className={cn("message-row", outbound && "outbound", highlight && "highlighted")}
    >
      <Avatar name={outbound ? "我" : message.sender_name || "?"} size={38} />
      <div className="message-main">
        <div className="message-meta">
          <strong>{outbound ? "我" : message.sender_name || "未知发送人"}</strong>
          <PlatformBadge platform={message.platform} compact />
          {message.chat_type === "group" && <span className="chat-tag">群聊</span>}
          {message.auto_reply_rule_id && <span className="auto-tag">自动回复</span>}
          <span className="message-time">{formatTime(message.created_at)}</span>
          {outbound && (
            <span className={cn("delivery-tag", message.delivery_status)}>
              {message.delivery_status === "failed" ? <AlertCircle size={12} /> : <CheckCheck size={12} />}
              {message.delivery_status === "failed" ? "发送失败" : "已发送"}
            </span>
          )}
        </div>

        <div className="bubble-wrap">
          <div className={cn("bubble", outbound && "bubble-out", message.content_type === "file" && "bubble-file")}>
            {imageFile && (
              <button
                className="message-image-button"
                onClick={() => onOpenFile(imageFile.id)}
                title="查看图片"
              >
                <img
                  className="message-image"
                  src={`/api/files/${imageFile.id}/content`}
                  alt={imageFile.filename}
                  loading="lazy"
                />
              </button>
            )}
            {message.text && !(imageFile && message.text === "[图片]") && <p>{message.text}</p>}

            {otherFiles.length > 0 && (
              <div className="attachment-list">
                {otherFiles.map((file) => (
                  <button className="attachment-chip" key={file.id} onClick={() => onOpenFile(file.id)}>
                    <span className="file-icon">
                      <FileText size={16} />
                    </span>
                    <span className="attachment-info">
                      <span className="attachment-name">{file.filename}</span>
                      <span className="attachment-size">
                        <Paperclip size={11} /> {Math.max(1, Math.round(file.size_bytes / 1024))} KB
                      </span>
                    </span>
                    {file.opened && <span className="opened-dot" title="已打开过" />}
                  </button>
                ))}
              </div>
            )}

            {message.task && (
              <button className="task-inline-card" onClick={() => onOpenTask(message.task!.id)}>
                <span className="task-inline-icon">
                  <Rows3 size={15} />
                </span>
                <span className="task-inline-text">
                  <span>{message.task.title}</span>
                  <span className={cn("task-inline-status", message.task.status)}>
                    {statusMeta[message.task.status].label}
                  </span>
                </span>
                <span className="task-inline-arrow">→</span>
              </button>
            )}

            {message.error && <p className="message-error">{message.error}</p>}
          </div>

          <div className="message-actions">
            <button onClick={() => onReply(message)} title="回复">
              <MessageSquareReply size={14} /> 回复
            </button>
            {!message.task && (
              <button onClick={() => onCreateTask(message)} title="转为待办">
                <ListPlus size={14} /> 转为待办
              </button>
            )}
            {message.task && (
              <button onClick={() => onOpenTask(message.task!.id)} title="查看待办">
                <Rows3 size={14} /> 查看待办
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
