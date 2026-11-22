export type Platform = "feishu" | "dingtalk" | "wecom";

export interface User {
  id: string;
  display_name: string;
  email: string | null;
  avatar_url: string | null;
}

export interface Account {
  platform: Platform;
  display_name: string;
  status: string;
  platform_user_id: string;
}

export interface AuthMe {
  user: User;
  accounts: Account[];
  demo_mode: boolean;
  dev_login: boolean;
  allow_registration?: boolean;
  has_password?: boolean;
  configured: Record<Platform, boolean>;
}

export interface FileItem {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  platform: Platform;
  message_id: string | null;
  download_status: string;
  created_at: string;
  opened: boolean;
  last_opened_at: string | null;
  open_count: number;
  sender_name?: string | null;
  message_text?: string | null;
}

export type TaskStatus = "open" | "in_progress" | "done";

export interface TaskBrief {
  id: string;
  title: string;
  status: TaskStatus;
  sync_state: string;
  source_platform: Platform | null;
}

export interface Message {
  id: string;
  platform: Platform;
  direction: "inbound" | "outbound";
  text: string;
  content_type: string;
  sender_name: string;
  sender_user_id: string | null;
  platform_chat_id: string | null;
  chat_type: "direct" | "group";
  task_id: string | null;
  auto_reply_rule_id: string | null;
  reply_to_message_id: string | null;
  reply_to_platform_msg_id: string | null;
  delivery_status: string;
  error: string | null;
  created_at: string;
  files: FileItem[];
  task: TaskBrief | null;
}

export interface Conversation {
  id: string;
  platform: Platform;
  platform_chat_id: string;
  chat_type: "direct" | "group";
  title: string;
  last_message: string;
  last_sender_name: string;
  last_at: string;
  message_count: number;
  unread_count: number;
}

export interface ConversationPage {
  items: Conversation[];
}

export interface ChatMember {
  id: string;
  name: string;
}

export interface StartConversationResponse {
  message: Message;
  conversation_id: string;
  title: string;
}

export interface TaskLink {
  id: string;
  platform: Platform;
  platform_task_id: string;
  platform_status: string;
  last_synced_at: string | null;
  state: string;
  sync_pending: boolean;
}

export interface SourceMessage {
  id: string;
  platform: Platform;
  text: string;
  sender_name: string;
  chat_type: string;
  created_at: string;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  source_platform: Platform | null;
  source_message_id: string | null;
  origin: string;
  assignee_id: string | null;
  assignee_name: string | null;
  due_at: string | null;
  priority: string;
  sync_state: string;
  sync_error: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  links: TaskLink[];
  source_message: SourceMessage | null;
}

export interface TaskCounts {
  all: number;
  open: number;
  in_progress: number;
  done: number;
  active: number;
}

export interface TaskList {
  items: Task[];
  counts: TaskCounts;
}

export interface Contact {
  id: string;
  display_name: string;
  email: string | null;
  initials: string;
  platforms: { platform: Platform; handle: string; display_name: string }[];
}

export interface PlatformConfigField {
  key: string;
  label: string;
  kind: "text" | "secret" | "bool";
  value: string | boolean;
  placeholder?: string;
  hint?: string;
  secret_set?: boolean;
}

export interface PlatformConfigItem {
  platform: Platform;
  label: string;
  enabled: boolean;
  configured: boolean;
  source: "database" | "environment";
  updated_at: string | null;
  fields: PlatformConfigField[];
}

export interface AutoReplyAttachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

export interface AutoReplyRule {
  id: string;
  name: string;
  platform: Platform | null;
  match_type: "keyword" | "any";
  keywords: string;
  keyword_list: string[];
  reply_text: string;
  attachment: AutoReplyAttachment | null;
  enabled: boolean;
  priority: number;
  cooldown_seconds: number;
  hit_count: number;
  last_hit_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutoReplyLogItem {
  message_id: string;
  rule_id: string | null;
  rule_name: string;
  platform: Platform;
  chat_id: string | null;
  text: string;
  delivery_status: string;
  created_at: string;
}

export interface ActionStep {
  status: string;
  message?: string;
  platform?: string;
  links?: { platform: string; status: string; message?: string }[];
  target?: string;
  message_id?: string;
  platform_msg_id?: string;
}

export interface ActionResponse {
  action: {
    id: string;
    client_action_id: string;
    status: string;
    steps: Record<string, ActionStep>;
  };
  task: Task;
}

export interface FileOpenResponse {
  file: FileItem;
  previewable: boolean;
  content_url: string;
  download_url: string;
}

export interface MessagePage {
  items: Message[];
  next_before: string | null;
}

export interface MessageContext {
  items: Message[];
  target_id: string;
  target_index: number;
}
