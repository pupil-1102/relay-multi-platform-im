import { CalendarClock, ChevronRight, Link2 } from "lucide-react";
import type { Task } from "../types";
import { cn, formatDateTime, platformMeta, statusMeta } from "../utils";
import { PlatformBadge, StatusPill, SyncPill } from "./Primitives";

export function TaskCard({ task, onClick }: { task: Task; onClick: () => void }) {
  return (
    <button className={cn("task-card", `task-${task.status}`)} onClick={onClick}>
      <span className={cn("task-status-dot", task.status)} />
      <div className="task-card-main">
        <div className="task-card-title-row">
          <strong>{task.title}</strong>
          <StatusPill status={task.status} />
        </div>
        <p className="task-card-desc">{task.description || "暂无描述"}</p>
        <div className="task-card-meta">
          {task.source_message && (
            <span className="meta-item">
              <Link2 size={12} />
              {task.source_message.sender_name}：{task.source_message.text || "附件消息"}
            </span>
          )}
          {task.due_at && (
            <span className="meta-item">
              <CalendarClock size={12} /> {formatDateTime(task.due_at)}
            </span>
          )}
          <span className="meta-item">
            {task.links.length > 0 ? task.links.map((link) => (
              <PlatformBadge key={link.id} platform={link.platform} compact />
            )) : <span className="muted-text">本地待办</span>}
          </span>
        </div>
      </div>
      <div className="task-card-side">
        <SyncPill state={task.sync_state} />
        <span className="task-card-assignee">{task.assignee_name || "未指派"}</span>
        <ChevronRight size={16} className="chev" />
      </div>
    </button>
  );
}
