import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Clock,
  Loader2,
  Paperclip,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { api } from "../api";
import type { AutoReplyAttachment, AutoReplyRule, Platform } from "../types";
import { cn, formatDateTime, platformMeta } from "../utils";
import { useToast } from "../Toast";
import { Modal } from "../components/Modal";
import { EmptyState, PlatformBadge } from "../components/Primitives";

type Draft = {
  id?: string;
  name: string;
  platform: "" | Platform;
  match_type: "keyword" | "any";
  keywords: string;
  reply_text: string;
  attachment: AutoReplyAttachment | null;
  enabled: boolean;
  priority: number;
  cooldown_seconds: number;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  platform: "",
  match_type: "keyword",
  keywords: "",
  reply_text: "",
  attachment: null,
  enabled: true,
  priority: 100,
  cooldown_seconds: 30,
};

function draftFrom(rule: AutoReplyRule): Draft {
  return {
    id: rule.id,
    name: rule.name,
    platform: (rule.platform || "") as "" | Platform,
    match_type: rule.match_type,
    keywords: rule.keywords,
    reply_text: rule.reply_text,
    attachment: rule.attachment,
    enabled: rule.enabled,
    priority: rule.priority,
    cooldown_seconds: rule.cooldown_seconds,
  };
}

export function AutoReplyPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const rulesQuery = useQuery({ queryKey: ["auto-reply"], queryFn: api.autoReplyRules });
  const logQuery = useQuery({ queryKey: ["auto-reply-log"], queryFn: api.autoReplyLog });

  const rules = rulesQuery.data?.items || [];
  const logs = logQuery.data?.items || [];
  const globalEnabled = rulesQuery.data?.enabled !== false;

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["auto-reply"] });
    await queryClient.invalidateQueries({ queryKey: ["auto-reply-log"] });
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.name.trim()) {
      toast.push("请填写规则名称", "error");
      return;
    }
    if (!draft.reply_text.trim()) {
      toast.push("请填写自动回复内容", "error");
      return;
    }
    if (draft.match_type === "keyword" && !draft.keywords.trim()) {
      toast.push("关键词规则至少填写一个关键词", "error");
      return;
    }
    setBusy("save");
    const body = {
      name: draft.name.trim(),
      platform: draft.platform || null,
      match_type: draft.match_type,
      keywords: draft.keywords.trim(),
      reply_text: draft.reply_text.trim(),
      attachment_file_id: draft.attachment?.id ?? null,
      enabled: draft.enabled,
      priority: Number(draft.priority) || 100,
      cooldown_seconds: Number(draft.cooldown_seconds) || 0,
    };
    try {
      if (draft.id) {
        await api.updateAutoReplyRule(draft.id, body);
        toast.push("规则已更新", "success");
      } else {
        await api.createAutoReplyRule(body);
        toast.push("规则已创建，收到匹配消息会自动回复", "success");
      }
      setDraft(null);
      await refresh();
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "保存失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const asset = await api.uploadAutoReplyAsset(file);
      setDraft((current) => (current ? { ...current, attachment: asset } : current));
      toast.push(`附件已上传：${asset.filename}`, "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "附件上传失败", "error");
    } finally {
      setUploading(false);
    }
  };

  const toggle = async (rule: AutoReplyRule) => {
    setBusy(rule.id);
    try {
      await api.updateAutoReplyRule(rule.id, {
        name: rule.name,
        platform: rule.platform,
        match_type: rule.match_type,
        keywords: rule.keywords,
        reply_text: rule.reply_text,
        attachment_file_id: rule.attachment?.id ?? null,
        enabled: !rule.enabled,
        priority: rule.priority,
        cooldown_seconds: rule.cooldown_seconds,
      });
      await refresh();
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "操作失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (rule: AutoReplyRule) => {
    if (!window.confirm(`删除规则「${rule.name}」？`)) return;
    setBusy(rule.id);
    try {
      await api.deleteAutoReplyRule(rule.id);
      toast.push("规则已删除", "info");
      await refresh();
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "删除失败", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page auto-reply-page">
      <header className="page-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">AUTOMATION</span>
            <span className="live-pill">
              <Bot size={12} /> {globalEnabled ? "自动回复已开启" : "自动回复已关闭"}
            </span>
          </div>
          <h1>自动回复</h1>
          <p>按优先级匹配入站文本消息，用引用回复自动应答；命中冷却、回显和自身的消息都会被跳过。</p>
        </div>
        <button className="button primary" onClick={() => setDraft({ ...EMPTY_DRAFT })}>
          <Plus size={15} /> 新建规则
        </button>
      </header>

      {!globalEnabled && (
        <div className="settings-warning">
          <Zap size={14} /> 服务端已关闭自动回复（AUTO_REPLY_ENABLED=false），规则不会触发。
        </div>
      )}

      {rulesQuery.isLoading && (
        <div className="list-loading">
          <Loader2 size={16} className="spin" /> 正在加载规则…
        </div>
      )}

      {!rulesQuery.isLoading && rules.length === 0 && (
        <EmptyState
          title="还没有自动回复规则"
          description="新建一条关键词规则，例如包含「报价」时自动回复价格说明。"
        />
      )}

      <div className="rules-list">
        {rules.map((rule) => (
          <article className={cn("rule-card", !rule.enabled && "inactive")} key={rule.id}>
            <div className="rule-main">
              <div className="rule-head">
                <strong>{rule.name}</strong>
                {rule.platform ? (
                  <PlatformBadge platform={rule.platform} compact />
                ) : (
                  <span className="rule-scope">全部平台</span>
                )}
                <span className={cn("rule-state", rule.enabled ? "on" : "off")}>
                  {rule.enabled ? "启用中" : "已停用"}
                </span>
              </div>
              <div className="rule-match">
                {rule.match_type === "keyword" ? (
                  <>
                    <span className="rule-label">关键词</span>
                    {rule.keyword_list.length ? (
                      rule.keyword_list.map((keyword) => (
                        <span className="keyword-chip" key={keyword}>
                          {keyword}
                        </span>
                      ))
                    ) : (
                      <span className="rule-empty">未填写</span>
                    )}
                  </>
                ) : (
                  <span className="rule-label">匹配全部消息（兜底）</span>
                )}
              </div>
              <p className="rule-reply">{rule.reply_text}</p>
              {rule.attachment && (
                <div className="rule-attachment">
                  <Paperclip size={12} /> {rule.attachment.filename}
                </div>
              )}
              <div className="rule-stats">
                <span>
                  <Clock size={12} /> 冷却 {rule.cooldown_seconds}s
                </span>
                <span>优先级 {rule.priority}</span>
                <span>已触发 {rule.hit_count} 次</span>
                {rule.last_hit_at && <span>最近 {formatDateTime(rule.last_hit_at)}</span>}
              </div>
            </div>
            <div className="rule-actions">
              <label className="switch">
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  disabled={busy === rule.id}
                  onChange={() => void toggle(rule)}
                />
                <span className="switch-track" />
              </label>
              <button className="icon-button" onClick={() => setDraft(draftFrom(rule))} title="编辑">
                <Pencil size={15} />
              </button>
              <button className="icon-button" onClick={() => void remove(rule)} title="删除">
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        ))}
      </div>

      <section className="auto-reply-log">
        <div className="section-head">
          <h2>最近自动回复</h2>
          <span className="muted-text">{logs.length} 条</span>
        </div>
        {logs.length === 0 ? (
          <p className="muted-text">还没有自动回复记录。</p>
        ) : (
          <div className="log-list">
            {logs.map((item) => (
              <div className="log-row" key={item.message_id}>
                <span className="log-rule">{item.rule_name}</span>
                <PlatformBadge platform={item.platform} compact />
                <span className={cn("log-status", item.delivery_status)}>
                  {item.delivery_status === "sent" ? "已发送" : item.delivery_status}
                </span>
                <span className="log-text">{item.text}</span>
                <span className="log-time">{formatDateTime(item.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {draft && (
        <Modal
          open
          onClose={() => setDraft(null)}
          title={draft.id ? "编辑自动回复规则" : "新建自动回复规则"}
          subtitle="匹配到入站文本消息后，以一个引用回复发出"
          wide
          footer={
            <>
              <button className="button ghost" onClick={() => setDraft(null)} disabled={busy === "save"}>
                取消
              </button>
              <button className="button primary" onClick={() => void save()} disabled={busy === "save"}>
                {busy === "save" ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
                保存规则
              </button>
            </>
          }
        >
          <div className="rule-form">
            <label className="field">
              <span>规则名称</span>
              <input
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="例如：报价咨询自动回复"
              />
            </label>

            <div className="field-row">
              <label className="field">
                <span>适用平台</span>
                <select
                  value={draft.platform}
                  onChange={(event) =>
                    setDraft({ ...draft, platform: event.target.value as "" | Platform })
                  }
                >
                  <option value="">全部平台</option>
                  <option value="feishu">飞书</option>
                  <option value="dingtalk">钉钉</option>
                  <option value="wecom">企业微信</option>
                </select>
              </label>
              <label className="field">
                <span>匹配方式</span>
                <select
                  value={draft.match_type}
                  onChange={(event) =>
                    setDraft({ ...draft, match_type: event.target.value as "keyword" | "any" })
                  }
                >
                  <option value="keyword">包含关键词</option>
                  <option value="any">全部消息（兜底）</option>
                </select>
              </label>
            </div>

            {draft.match_type === "keyword" && (
              <label className="field">
                <span>关键词（逗号分隔，任一命中即触发）</span>
                <input
                  value={draft.keywords}
                  onChange={(event) => setDraft({ ...draft, keywords: event.target.value })}
                  placeholder="报价,价格,多少钱"
                />
              </label>
            )}

            <label className="field">
              <span>自动回复内容</span>
              <textarea
                rows={4}
                value={draft.reply_text}
                onChange={(event) => setDraft({ ...draft, reply_text: event.target.value })}
                placeholder="您好 {{sender_name}}，报价稍后由销售同事回复您。"
              />
            </label>
            <p className="field-hint">
              可用变量：{"{{sender_name}}"} 对方称呼、{"{{platform}}"} 平台名、{"{{rule_name}}"} 规则名。
            </p>

            <div className="rule-attachment-editor">
              <span className="rule-label">附件（随回复一起发送，可选）</span>
              {draft.attachment ? (
                <div className="rule-attachment-chip">
                  <Paperclip size={14} />
                  <span>{draft.attachment.filename}</span>
                  <button
                    type="button"
                    className="icon-button"
                    title="移除附件"
                    onClick={() => setDraft({ ...draft, attachment: null })}
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <label className="button ghost small file-button">
                  {uploading ? <Loader2 size={14} className="spin" /> : <Upload size={14} />}
                  {uploading ? "上传中…" : "上传附件"}
                  <input
                    type="file"
                    hidden
                    disabled={uploading}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) void upload(file);
                      event.target.value = "";
                    }}
                  />
                </label>
              )}
            </div>

            <div className="field-row">
              <label className="field">
                <span>优先级（越小越先匹配）</span>
                <input
                  type="number"
                  value={draft.priority}
                  onChange={(event) => setDraft({ ...draft, priority: Number(event.target.value) })}
                />
              </label>
              <label className="field">
                <span>同一会话冷却（秒）</span>
                <input
                  type="number"
                  value={draft.cooldown_seconds}
                  onChange={(event) =>
                    setDraft({ ...draft, cooldown_seconds: Number(event.target.value) })
                  }
                />
              </label>
            </div>

            <label className="settings-check">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              <span>
                <strong>保存后立即启用</strong>
                <em>关闭后规则保留但不参与匹配</em>
              </span>
            </label>

            <p className="field-hint">
              平台选择仅影响匹配范围；{platformMeta.feishu.label}、{platformMeta.dingtalk.label}、
              {platformMeta.wecom.label} 都支持。触发失败不会影响消息入库。
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}
