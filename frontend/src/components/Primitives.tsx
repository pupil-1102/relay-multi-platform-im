import type { ReactNode } from "react";
import type { Platform, TaskStatus } from "../types";
import { platformMeta, statusMeta, syncMeta, avatarTone, initials, cn } from "../utils";

export function PlatformBadge({ platform, compact = false }: { platform: Platform; compact?: boolean }) {
  const meta = platformMeta[platform];
  return (
    <span className={cn("platform-badge", compact && "compact")} style={{ color: meta.color, background: meta.soft }}>
      <span className="platform-dot" style={{ background: meta.color }} />
      {meta.label}
    </span>
  );
}

export function StatusPill({ status, className = "" }: { status: TaskStatus; className?: string }) {
  const meta = statusMeta[status];
  return <span className={cn("status-pill", `status-${meta.tone}`, className)}>{meta.label}</span>;
}

export function SyncPill({ state }: { state: string }) {
  const meta = syncMeta[state] || { label: state, tone: "muted" };
  return <span className={cn("sync-pill", `sync-${meta.tone}`)}>{meta.label}</span>;
}

export function Avatar({
  name,
  size = 36,
  muted = false,
}: {
  name: string;
  size?: number;
  muted?: boolean;
}) {
  return (
    <span
      className={cn("avatar", muted && "avatar-muted")}
      data-tone={avatarTone(name)}
      style={{ width: size, height: size, fontSize: Math.max(11, size * 0.34) }}
    >
      {initials(name)}
    </span>
  );
}

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
