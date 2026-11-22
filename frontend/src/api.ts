import type {
  ActionResponse,
  AuthMe,
  AutoReplyAttachment,
  AutoReplyLogItem,
  AutoReplyRule,
  Contact,
  ConversationPage,
  FileItem,
  FileOpenResponse,
  Message,
  MessageContext,
  MessagePage,
  Platform,
  PlatformConfigItem,
  Task,
  TaskList,
  TaskStatus,
  StartConversationResponse,
} from "./types";

export interface AutoReplyRuleInput {
  name: string;
  platform: Platform | null;
  match_type: "keyword" | "any";
  keywords: string;
  reply_text: string;
  attachment_file_id?: string | null;
  enabled: boolean;
  priority: number;
  cooldown_seconds: number;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((x: any) => x.msg).join("；");
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<AuthMe>("/api/auth/me"),
  authConfig: () =>
    request<{
      demo_mode: boolean;
      dev_login: boolean;
      allow_registration: boolean;
      configured: Record<Platform, boolean>;
    }>("/api/auth/config"),
  devLogin: () => request<AuthMe>("/api/auth/dev-login", { method: "POST" }),
  register: (body: { email: string; password: string; display_name?: string }) =>
    request<AuthMe>("/api/auth/register", { method: "POST", body: JSON.stringify(body) }),
  loginWithPassword: (body: { email: string; password: string }) =>
    request<AuthMe>("/api/auth/login", { method: "POST", body: JSON.stringify(body) }),
  changePassword: (body: { password: string; current_password?: string }) =>
    request<AuthMe>("/api/auth/password", { method: "POST", body: JSON.stringify(body) }),
  unbindPlatformAccount: (platform: Platform) =>
    request<AuthMe>(`/api/auth/accounts/${platform}`, { method: "DELETE" }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  messages: (params: { platform?: string; q?: string; before?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.platform) query.set("platform", params.platform);
    if (params.q) query.set("q", params.q);
    if (params.before) query.set("before", params.before);
    query.set("limit", String(params.limit ?? 60));
    return request<MessagePage>(`/api/messages?${query.toString()}`);
  },
  messageContext: (id: string, before = 20, after = 20) =>
    request<MessageContext>(`/api/messages/${id}/context?before=${before}&after=${after}`),
  message: (id: string) => request<Message>(`/api/messages/${id}`),

  conversations: (params: { platform?: string; q?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.platform) query.set("platform", params.platform);
    if (params.q) query.set("q", params.q);
    if (params.limit) query.set("limit", String(params.limit));
    return request<ConversationPage>(`/api/conversations?${query.toString()}`);
  },
  conversationMessages: (id: string, before?: string, limit = 200) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (before) query.set("before", before);
    return request<MessagePage>(
      `/api/conversations/${encodeURIComponent(id)}/messages?${query.toString()}`,
    );
  },
  conversationMembers: (id: string) =>
    request<{ items: { id: string; name: string }[]; error?: string }>(
      `/api/conversations/${encodeURIComponent(id)}/members`,
    ),
  markConversationRead: (id: string) =>
    request<{ ok: boolean; unread_count: number }>(
      `/api/conversations/${encodeURIComponent(id)}/read`,
      { method: "POST" },
    ),
  sendConversation: (
    id: string,
    body: {
      text: string;
      client_msg_id?: string;
      reply_to_message_id?: string;
      mentions?: { id: string; name: string }[];
    },
  ) =>
    request<Message>(`/api/conversations/${encodeURIComponent(id)}/send`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  sendConversationFile: async (id: string, form: FormData): Promise<Message> => {
    const response = await fetch(`/api/conversations/${encodeURIComponent(id)}/file`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!response.ok) {
      let message = `文件发送失败 (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, message);
    }
    return (await response.json()) as Message;
  },
  startConversation: (body: {
    platform: Platform;
    contact_id?: string;
    handle?: string;
    display_name?: string;
    text: string;
    client_msg_id?: string;
  }) =>
    request<StartConversationResponse>("/api/conversations/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  syncContacts: (platform: Platform = "feishu") =>
    request<{ synced: number; skipped: number; total: number }>(
      `/api/contacts/sync?platform=${platform}`,
      { method: "POST" },
    ),

  tasks: (params: { status?: string; platform?: string; q?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.status && params.status !== "all") query.set("status", params.status);
    if (params.platform) query.set("platform", params.platform);
    if (params.q) query.set("q", params.q);
    return request<TaskList>(`/api/tasks?${query.toString()}`);
  },
  task: (id: string) => request<Task>(`/api/tasks/${id}`),
  createTask: (body: {
    source_message_id: string;
    title?: string;
    description?: string;
    assignee_id?: string;
    due_at?: string;
    sync_platform?: boolean;
  }) => request<{ task: Task; created: boolean }>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  taskAction: (
    id: string,
    body: {
      client_action_id: string;
      reply_text?: string;
      target_status?: TaskStatus;
      action_type?: string;
    },
  ) =>
    request<ActionResponse>(`/api/tasks/${id}/action`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  linkWecom: (id: string, todo_id: string) =>
    request<Task>(`/api/tasks/${id}/wecom-link`, {
      method: "POST",
      body: JSON.stringify({ todo_id }),
    }),

  files: (params: { q?: string; platform?: string; recent?: boolean } = {}) => {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.platform) query.set("platform", params.platform);
    if (params.recent) query.set("recent", "true");
    return request<{ items: FileItem[] }>(`/api/files?${query.toString()}`);
  },
  openFile: (id: string) => request<FileOpenResponse>(`/api/files/${id}/open`, { method: "POST" }),

  contacts: (params: { q?: string; platform?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.platform) query.set("platform", params.platform);
    return request<{ items: Contact[] }>(`/api/contacts?${query.toString()}`);
  },

  send: (body: {
    platform: Platform;
    to_user_id?: string;
    to_handle?: string;
    text: string;
    client_msg_id?: string;
    reply_to_message_id?: string;
  }) => request<Message>("/api/send", { method: "POST", body: JSON.stringify(body) }),

  sendFile: async (form: FormData): Promise<Message> => {
    const response = await fetch("/api/send/file", {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!response.ok) {
      let message = `文件发送失败 (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, message);
    }
    return (await response.json()) as Message;
  },

  overview: () =>
    request<{ messages: number; tasks: number; active_tasks: number; unread: number }>(
      "/api/overview",
    ),
  platformStatus: () =>
    request<{
      platforms: Record<string, { state: string; detail: string }>;
      realtime: Record<string, { state: string; detail: string }>;
    }>("/api/platforms/status"),
  platformConfigs: () => request<{ items: PlatformConfigItem[] }>("/api/platforms/config"),
  savePlatformConfig: (
    platform: Platform,
    body: { enabled: boolean; values: Record<string, string | boolean> },
  ) =>
    request<{
      ok: boolean;
      items: PlatformConfigItem[];
      platforms: Record<string, { state: string; detail: string }>;
    }>(`/api/platforms/${platform}/config`, { method: "PUT", body: JSON.stringify(body) }),
  resetPlatformConfig: (platform: Platform) =>
    request<{
      ok: boolean;
      items: PlatformConfigItem[];
      platforms: Record<string, { state: string; detail: string }>;
    }>(`/api/platforms/${platform}/config`, { method: "DELETE" }),

  autoReplyRules: () =>
    request<{ items: AutoReplyRule[]; enabled: boolean }>("/api/auto-reply/rules"),
  createAutoReplyRule: (body: AutoReplyRuleInput) =>
    request<AutoReplyRule>("/api/auto-reply/rules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateAutoReplyRule: (id: string, body: AutoReplyRuleInput) =>
    request<AutoReplyRule>(`/api/auto-reply/rules/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteAutoReplyRule: (id: string) =>
    request<{ ok: boolean }>(`/api/auto-reply/rules/${id}`, { method: "DELETE" }),
  autoReplyLog: () => request<{ items: AutoReplyLogItem[] }>("/api/auto-reply/log"),
  uploadAutoReplyAsset: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/auto-reply/assets", {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!response.ok) {
      let message = `附件上传失败 (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, message);
    }
    return (await response.json()) as AutoReplyAttachment;
  },
  health: () => request<{ status: string; demo_mode: boolean; version: string }>("/api/health"),
  wecomConfig: (url: string) =>
    request<{
      demo?: boolean;
      corp_id: string;
      agent_id: string;
      timestamp: number;
      nonce_str: string;
      signature: string;
    }>(`/api/wecom/jsapi-config?url=${encodeURIComponent(url)}`),
};
