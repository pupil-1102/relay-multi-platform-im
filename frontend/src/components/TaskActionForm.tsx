import { useState } from "react";
import { Check, Loader2, MessageSquare, RotateCcw, Send, Play } from "lucide-react";
import { api } from "../api";
import type { ActionResponse, Task, TaskStatus } from "../types";
import { useToast } from "../Toast";
import { cn } from "../utils";

function newActionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `act-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function TaskActionForm({
  task,
  onUpdated,
  compact = false,
}: {
  task: Task;
  onUpdated: (task: Task) => void;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResponse | null>(null);
  const toast = useToast();
  const canReply = Boolean(task.source_message_id);

  const run = async (target?: TaskStatus, withReply = false) => {
    const reply = withReply ? text.trim() : "";
    if (withReply && !reply) {
      toast.push("请先输入回复内容", "error");
      return;
    }
    if (!target && !withReply) {
      toast.push("请选择操作", "error");
      return;
    }
    setBusy(withReply ? "reply" : target || "action");
    try {
      const response = await api.taskAction(task.id, {
        client_action_id: newActionId(),
        reply_text: withReply ? reply : undefined,
        target_status: target,
        action_type: target ? "status" : "reply",
      });
      setResult(response);
      onUpdated(response.task);
      if (response.action.status === "succeeded") {
        toast.push(target === "done" ? "已回复并标记完成" : "操作已完成", "success");
        if (withReply) setText("");
      } else if (response.action.status === "partial") {
        toast.push("操作部分完成，请查看同步结果", "info");
      } else {
        toast.push(response.action.steps.reply?.message || "操作失败", "error");
      }
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "操作失败", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={cn("task-action-form", compact && "compact")}>
      <div className="action-textarea-wrap">
        <MessageSquare size={16} />
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={canReply ? "写下处理意见，发送时会引用原消息…" : "该任务没有源消息，无法引用回复；可直接变更状态"}
          disabled={!canReply}
          rows={compact ? 2 : 3}
        />
      </div>
      <div className="action-buttons">
        {canReply && (
          <>
            <button
              className="button primary"
              disabled={Boolean(busy) || !text.trim()}
              onClick={() => void run(undefined, true)}
            >
              {busy === "reply" ? <Loader2 size={15} className="spin" /> : <Send size={15} />}
              仅回复
            </button>
            <button
              className="button success"
              disabled={Boolean(busy) || !text.trim()}
              onClick={() => void run("done", true)}
            >
              {busy === "done" ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
              回复并完成
            </button>
          </>
        )}
        {task.status !== "done" && (
          <button className="button ghost" disabled={Boolean(busy)} onClick={() => void run("done")}>
            <Check size={15} /> 仅标记完成
          </button>
        )}
        {task.status === "done" ? (
          <button className="button ghost" disabled={Boolean(busy)} onClick={() => void run("open")}>
            <RotateCcw size={15} /> 重新打开
          </button>
        ) : task.status === "open" ? (
          <button className="button ghost" disabled={Boolean(busy)} onClick={() => void run("in_progress")}>
            <Play size={15} /> 开始处理
          </button>
        ) : null}
      </div>

      {result && (
        <div className={`action-result ${result.action.status}`}>
          <div className="action-result-head">
            <strong>
              {result.action.status === "succeeded"
                ? "操作成功"
                : result.action.status === "partial"
                  ? "部分完成"
                  : "操作失败"}
            </strong>
            <span>步骤结果</span>
          </div>
          <ul>
            {Object.entries(result.action.steps).map(([key, step]) => (
              <li key={key}>
                <span className={`step-dot ${step.status}`} />
                <span className="step-name">{key === "reply" ? "引用回复" : "状态同步"}</span>
                <span className="step-message">
                  {step.status === "ok"
                    ? "成功"
                    : step.status === "skipped"
                      ? step.message || "跳过"
                      : step.message || (step.links || []).filter((link) => link.status !== "ok").map((link) => `${link.platform}: ${link.message}`).join("；")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
