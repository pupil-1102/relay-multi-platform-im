import type { Platform, TaskStatus } from "./types";

export const platformMeta: Record<Platform, { label: string; color: string; soft: string; short: string }> = {
  feishu: { label: "飞书", color: "#3370ff", soft: "#eaf0ff", short: "飞" },
  dingtalk: { label: "钉钉", color: "#f26b1d", soft: "#fff1e8", short: "钉" },
  wecom: { label: "企业微信", color: "#1a9d5a", soft: "#e8f7ef", short: "企" },
};

export const statusMeta: Record<TaskStatus, { label: string; tone: string }> = {
  open: { label: "待处理", tone: "open" },
  in_progress: { label: "进行中", tone: "progress" },
  done: { label: "已完成", tone: "done" },
};

export const syncMeta: Record<string, { label: string; tone: string }> = {
  synced: { label: "已同步", tone: "ok" },
  local: { label: "仅本地", tone: "muted" },
  pending: { label: "同步中", tone: "warn" },
  partial: { label: "部分同步", tone: "warn" },
  failed: { label: "同步失败", tone: "danger" },
};

export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

export function initials(name: string): string {
  if (!name) return "?";
  const trimmed = name.trim();
  if (/^[A-Za-z]/.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
  }
  return trimmed.slice(-2);
}

export function avatarTone(name: string): string {
  const tones = ["blue", "violet", "teal", "amber", "rose", "slate"];
  let sum = 0;
  for (const char of name) sum += char.charCodeAt(0);
  return tones[sum % tones.length];
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    ...(sameDay ? {} : { month: "2-digit", day: "2-digit" }),
  }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatDateLabel(value: string): string {
  const date = new Date(value);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "今天";
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(date);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function isImage(mime: string): boolean {
  return mime.startsWith("image/");
}

export function isPdf(mime: string): boolean {
  return mime === "application/pdf";
}

export function isText(mime: string): boolean {
  return mime.startsWith("text/") || mime === "application/json";
}

export function clipText(text: string, max = 80): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

export function conversationId(platform: Platform, chatId: string): string {
  const bytes = new TextEncoder().encode(`${platform}:${chatId}`);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
