import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ExternalLink,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Unlink,
  UserRound,
} from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Platform, PlatformConfigItem } from "../types";
import { cn, platformMeta } from "../utils";
import { useToast } from "../Toast";

const STATE_LABEL: Record<string, string> = {
  running: "运行中",
  configured: "已配置",
  error: "异常",
  disabled: "未启用",
  stopped: "已停止",
  connecting: "连接中",
  reconnecting: "重连中",
};

type Draft = { enabled: boolean; values: Record<string, string | boolean> };

function toDraft(item: PlatformConfigItem): Draft {
  const values: Record<string, string | boolean> = {};
  for (const field of item.fields) values[field.key] = field.value;
  return { enabled: item.enabled, values };
}

export function SettingsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { me, refresh } = useAuth();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const configs = useQuery({ queryKey: ["platform-config"], queryFn: api.platformConfigs });
  const status = useQuery({
    queryKey: ["platform-status"],
    queryFn: api.platformStatus,
    refetchInterval: 15_000,
  });

  const items = useMemo(() => configs.data?.items || [], [configs.data]);

  useEffect(() => {
    if (!items.length) return;
    setDrafts((previous) => {
      const next = { ...previous };
      for (const item of items) {
        // Keep unsaved edits; only seed drafts we have never shown.
        if (!next[item.platform]) next[item.platform] = toDraft(item);
      }
      return next;
    });
  }, [items]);

  const update = (platform: string, patch: Partial<Draft>) => {
    setDrafts((previous) => {
      const current = previous[platform];
      if (!current) return previous;
      return { ...previous, [platform]: { ...current, ...patch } };
    });
  };

  const updateValue = (platform: string, key: string, value: string | boolean) => {
    setDrafts((previous) => {
      const current = previous[platform];
      if (!current) return previous;
      return {
        ...previous,
        [platform]: { ...current, values: { ...current.values, [key]: value } },
      };
    });
  };

  const save = async (item: PlatformConfigItem) => {
    const draft = drafts[item.platform];
    if (!draft) return;
    setBusy(item.platform);
    try {
      const response = await api.savePlatformConfig(item.platform, {
        enabled: draft.enabled,
        values: draft.values,
      });
      queryClient.setQueryData(["platform-config"], { items: response.items });
      const saved = response.items.find((entry) => entry.platform === item.platform);
      if (saved) {
        setDrafts((previous) => ({ ...previous, [item.platform]: toDraft(saved) }));
      }
      await queryClient.invalidateQueries({ queryKey: ["platform-status"] });
      await queryClient.invalidateQueries({ queryKey: ["auth-config"] });
      toast.push(`${item.label} 配置已保存并生效`, "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "保存失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const reset = async (item: PlatformConfigItem) => {
    if (!window.confirm(`清除 ${item.label} 的数据库配置并回到环境变量默认值？`)) return;
    setBusy(item.platform);
    try {
      const response = await api.resetPlatformConfig(item.platform);
      queryClient.setQueryData(["platform-config"], { items: response.items });
      const resetItem = response.items.find((entry) => entry.platform === item.platform);
      if (resetItem) {
        setDrafts((previous) => ({ ...previous, [item.platform]: toDraft(resetItem) }));
      }
      await queryClient.invalidateQueries({ queryKey: ["platform-status"] });
      await queryClient.invalidateQueries({ queryKey: ["auth-config"] });
      toast.push(`${item.label} 已恢复为环境变量配置`, "info");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "重置失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const unbind = async (platform: Platform) => {
    const label = platformMeta[platform].label;
    const confirmed = window.confirm(
      `解绑 ${label} 身份？\n\n解绑后将不能以该身份收发消息，历史消息与待办会保留；` +
        "之后可以用这个平台身份重新授权，或让它绑定到另一个 Relay 账号。",
    );
    if (!confirmed) return;
    setBusy(`unbind:${platform}`);
    try {
      await api.unbindPlatformAccount(platform);
      await refresh();
      await queryClient.invalidateQueries({ queryKey: ["auth-config"] });
      toast.push(`${label} 身份已解绑`, "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "解绑失败", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page settings-page">
      <header className="page-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">PLATFORMS</span>
            <span className="live-pill">
              <ShieldCheck size={12} /> 凭据加密存储
            </span>
          </div>
          <h1>平台配置</h1>
          <p>填写平台应用凭据并启用通道。保存后立即生效，无需重启；环境变量仍是部署默认值，这里保存的配置优先。</p>
        </div>
        <button
          className="button ghost"
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ["platform-config"] });
            void queryClient.invalidateQueries({ queryKey: ["platform-status"] });
          }}
        >
          <RefreshCw size={15} /> 刷新
        </button>
      </header>

      {configs.isLoading && (
        <div className="list-loading">
          <Loader2 size={16} className="spin" /> 正在加载平台配置…
        </div>
      )}

      <section className="settings-card identity-card">
        <header className="settings-card-head">
          <div className="settings-title">
            <span className="settings-identity-icon">
              <UserRound size={16} />
            </span>
            <div>
              <h2>当前账号的平台身份</h2>
              <span className="settings-source">
                {me?.user.display_name} · {me?.user.email || "未设置邮箱"}
              </span>
            </div>
          </div>
        </header>
        {(me?.accounts || []).length === 0 ? (
          <p className="muted-text">
            这个账号还没有绑定平台身份。登录平台账号后，对应的身份会出现在这里；每个身份只会属于一个 Relay 账号。
          </p>
        ) : (
          <div className="identity-list">
            {(me?.accounts || []).map((account) => (
              <div className="identity-row" key={account.platform}>
                <span className="platform-dot" style={{ background: platformMeta[account.platform].color }} />
                <span className="identity-platform">{platformMeta[account.platform].label}</span>
                <span className="identity-name">{account.display_name || "未命名"}</span>
                <span className="identity-handle">{account.platform_user_id}</span>
                <span className={cn("identity-state", account.status === "connected" ? "on" : "off")}>
                  {account.status === "connected" ? "已绑定" : account.status}
                </span>
                <button
                  className="button ghost small identity-unbind"
                  disabled={busy === `unbind:${account.platform}`}
                  onClick={() => void unbind(account.platform)}
                  title={`解绑 ${platformMeta[account.platform].label} 身份`}
                >
                  {busy === `unbind:${account.platform}` ? (
                    <Loader2 size={12} className="spin" />
                  ) : (
                    <Unlink size={12} />
                  )}
                  解绑
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="field-hint identity-hint">
          平台身份是按账号绑定的：如果这个飞书/钉钉/企微身份已经属于另一个 Relay 账号，登录时会明确提示，不会自动合并或切换账号。
        </p>
      </section>

      <div className="settings-grid">
        {items.map((item) => {
          const draft = drafts[item.platform] || toDraft(item);
          const state = status.data?.platforms?.[item.platform];
          const stateKey = state?.state || "disabled";
          const meta = platformMeta[item.platform];
          return (
            <article className="settings-card" key={item.platform}>
              <header className="settings-card-head">
                <div className="settings-title">
                  <span className="platform-dot" style={{ background: meta.color }} />
                  <div>
                    <h2>{item.label}</h2>
                    <span className="settings-source">
                      {item.source === "database" ? "数据库配置" : "环境变量默认值"}
                      {item.configured ? " · 凭据已填写" : " · 凭据未填写"}
                    </span>
                  </div>
                </div>
                <div className="settings-head-right">
                  <span className={cn("channel-state", stateKey)}>
                    {STATE_LABEL[stateKey] || stateKey}
                  </span>
                  <label className="switch">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) => update(item.platform, { enabled: event.target.checked })}
                    />
                    <span className="switch-track" />
                    <span className="switch-label">{draft.enabled ? "已启用" : "未启用"}</span>
                  </label>
                </div>
              </header>

              {state?.detail && <p className="settings-detail">{state.detail}</p>}

              <div className="settings-fields">
                {item.fields.map((field) =>
                  field.kind === "bool" ? (
                    <label className="settings-check" key={field.key}>
                      <input
                        type="checkbox"
                        checked={Boolean(draft.values[field.key])}
                        onChange={(event) => updateValue(item.platform, field.key, event.target.checked)}
                      />
                      <span>
                        <strong>{field.label}</strong>
                        {field.hint && <em>{field.hint}</em>}
                      </span>
                    </label>
                  ) : (
                    <label className="field" key={field.key}>
                      <span>{field.label}</span>
                      <input
                        type={field.kind === "secret" ? "password" : "text"}
                        value={String(draft.values[field.key] ?? "")}
                        placeholder={
                          field.kind === "secret" && field.secret_set
                            ? "已配置，留空保持不变"
                            : field.placeholder || ""
                        }
                        autoComplete="off"
                        onChange={(event) => updateValue(item.platform, field.key, event.target.value)}
                      />
                      {field.hint && <em className="field-hint">{field.hint}</em>}
                    </label>
                  ),
                )}
              </div>

              {!item.configured && (
                <p className="settings-warning">
                  <AlertTriangle size={14} /> 凭据未填写：该平台无法收发真实消息。
                </p>
              )}

              <footer className="settings-actions">
                <button
                  className="button primary"
                  onClick={() => void save(item)}
                  disabled={busy === item.platform}
                >
                  {busy === item.platform ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
                  保存并生效
                </button>
                {item.source === "database" && (
                  <button
                    className="button ghost"
                    onClick={() => void reset(item)}
                    disabled={busy === item.platform}
                  >
                    <RotateCcw size={15} /> 恢复默认
                  </button>
                )}
                <a className="button ghost" href={`/api/auth/${item.platform}/start`}>
                  <ExternalLink size={15} /> 登录平台账号
                </a>
              </footer>
            </article>
          );
        })}
      </div>
    </div>
  );
}
