import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Loader2, Sparkles } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Message, Task } from "../types";
import { clipText, formatDateTime, platformMeta } from "../utils";
import { useToast } from "../Toast";
import { Modal } from "./Modal";
import { PlatformBadge } from "./Primitives";

export function CreateTaskModal({
  message,
  onClose,
  onCreated,
}: {
  message: Message;
  onClose: () => void;
  onCreated: (task: Task) => void;
}) {
  const { me } = useAuth();
  const toast = useToast();
  const defaultTitle = useMemo(() => {
    if (message.text.trim()) return clipText(message.text, 60);
    return message.files[0]?.filename || "未命名待办";
  }, [message]);
  const [title, setTitle] = useState(defaultTitle);
  const [description, setDescription] = useState(message.text);
  const [assigneeId, setAssigneeId] = useState(me?.user.id || "");
  const [due, setDue] = useState("");
  const [syncPlatform, setSyncPlatform] = useState(message.platform !== "wecom");
  const [busy, setBusy] = useState(false);

  const contacts = useQuery({
    queryKey: ["contacts", "all"],
    queryFn: () => api.contacts(),
  });

  const submit = async () => {
    if (!title.trim()) {
      toast.push("请填写待办标题", "error");
      return;
    }
    setBusy(true);
    try {
      const response = await api.createTask({
        source_message_id: message.id,
        title: title.trim(),
        description: description.trim() || undefined,
        assignee_id: assigneeId || undefined,
        due_at: due ? new Date(due).toISOString() : undefined,
        sync_platform: syncPlatform,
      });
      toast.push(response.created ? "待办已创建" : "该消息已关联待办", "success");
      onCreated(response.task);
      onClose();
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "创建待办失败", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="转为待办"
      subtitle={`来源：${platformMeta[message.platform].label} · ${message.sender_name} · ${formatDateTime(message.created_at)}`}
      footer={
        <>
          <button className="button ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="button primary" onClick={() => void submit()} disabled={busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
            创建待办
          </button>
        </>
      }
    >
      <div className="source-preview">
        <div className="source-preview-head">
          <PlatformBadge platform={message.platform} compact />
          <span>原消息</span>
        </div>
        <p>{message.text || message.files.map((file) => file.filename).join("、")}</p>
      </div>

      <label className="field">
        <span>标题</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} autoFocus />
      </label>

      <label className="field">
        <span>描述</span>
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={4}
          placeholder="补充处理要求、验收标准…"
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span>负责人</span>
          <select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}>
            {me && <option value={me.user.id}>{me.user.display_name}（我）</option>}
            {(contacts.data?.items || [])
              .filter((contact) => contact.id !== me?.user.id)
              .map((contact) => (
                <option value={contact.id} key={contact.id}>
                  {contact.display_name}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>
            <CalendarClock size={13} /> 截止时间
          </span>
          <input type="datetime-local" value={due} onChange={(event) => setDue(event.target.value)} />
        </label>
      </div>

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={syncPlatform}
          onChange={(event) => setSyncPlatform(event.target.checked)}
        />
        <span>
          同时在 {platformMeta[message.platform].label} 创建任务
          {message.platform === "wecom" && "（企微需在客户端 JS-SDK 创建，服务端会先建本地待办）"}
        </span>
      </label>

      {message.platform === "wecom" && (
        <p className="field-hint">
          企业微信服务端没有创建待办接口。保存后可在任务详情里通过企微 H5 创建并回传 todo_id，或先保留为本地待办。
        </p>
      )}
    </Modal>
  );
}
