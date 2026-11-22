import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  CheckSquare,
  Files,
  Inbox,
  LogOut,
  Menu,
  Radio,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import { useToast } from "../Toast";
import { useWebSocket } from "../useWebSocket";
import { cn, platformMeta } from "../utils";
import { Avatar } from "./Primitives";

export function Layout() {
  const { me, loading, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const authError = searchParams.get("auth_error");
  const toast = useToast();
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);

  const onEvent = useCallback(
    (event: { type: string; data: any }) => {
      if (event.type === "message.created") {
        const preview = String(event.data?.text || event.data?.files?.[0]?.filename || "附件消息").slice(0, 36);
        if (event.data?.direction === "inbound") {
          toast.push(`新消息 · ${event.data?.sender_name || "平台用户"}：${preview}`, "info");
        }
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        void queryClient.invalidateQueries({ queryKey: ["conversation-messages"] });
        void queryClient.invalidateQueries({ queryKey: ["messages"] });
        void queryClient.invalidateQueries({ queryKey: ["files"] });
        void queryClient.invalidateQueries({ queryKey: ["overview"] });
      }
      if (event.type === "task.action" || event.type === "task.updated" || event.type === "task.created") {
        const status = event.data?.status;
        if (status === "partial") toast.push("任务部分同步，请查看详情", "info");
        if (status === "failed") toast.push("任务操作失败，请重试", "error");
        void queryClient.invalidateQueries({ queryKey: ["tasks"] });
        void queryClient.invalidateQueries({ queryKey: ["overview"] });
        void queryClient.invalidateQueries({ queryKey: ["conversation-messages"] });
      }
    },
    [queryClient, toast],
  );

  useWebSocket(onEvent, setConnected, Boolean(me));

  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: api.overview,
    enabled: Boolean(me),
    refetchInterval: 5_000,
  });
  const platformStatus = useQuery({
    queryKey: ["platform-status"],
    queryFn: api.platformStatus,
    enabled: Boolean(me),
    refetchInterval: 15_000,
  });

  // An OAuth failure redirects back with ?auth_error=...; a toast makes sure it
  // is noticed even when the user is looking at another panel.
  useEffect(() => {
    if (authError) toast.push(`平台绑定未完成：${authError}`, "error");
  }, [authError, toast]);

  if (loading) {
    return (
      <div className="boot-screen">
        <div className="brand-mark large">R</div>
        <div className="boot-text">正在连接消息台…</div>
      </div>
    );
  }
  if (!me) {
    const target = authError ? `/login?auth_error=${encodeURIComponent(authError)}` : "/login";
    return <Navigate to={target} replace state={{ from: location.pathname }} />;
  }

  const navItems = [
    { to: "/", label: "会话", icon: <Inbox size={18} />, count: overview.data?.unread },
    {
      to: "/tasks",
      label: "待办",
      icon: <CheckSquare size={18} />,
      count: overview.data?.active_tasks,
    },
    { to: "/files", label: "文件", icon: <Files size={18} />, count: undefined },
    { to: "/auto-reply", label: "自动回复", icon: <Bot size={18} />, count: undefined },
    { to: "/settings", label: "平台配置", icon: <SettingsIcon size={18} />, count: undefined },
  ];

  const platformEntries = Object.entries(platformStatus.data?.platforms || {});
  const failedPlatforms = platformEntries
    .filter(([, value]) => value.state === "error")
    .map(([key]) => platformMeta[key as keyof typeof platformMeta]?.label || key);
  const runningPlatforms = platformEntries.filter(([, value]) => value.state === "running").length;
  const enabledPlatforms = platformEntries.filter(([, value]) => value.state !== "disabled").length;
  const sidebarTitle = me.demo_mode
    ? "三平台已接入"
    : failedPlatforms.length
      ? "平台配置待处理"
      : runningPlatforms
        ? "平台已接入"
        : "等待平台连接";
  const sidebarDetail = me.demo_mode
    ? "飞书 · 钉钉 · 企业微信"
    : failedPlatforms.length
      ? `${failedPlatforms.join("、")}配置异常`
      : `${runningPlatforms}/${enabledPlatforms || 0} 个平台已连接`;
  const sidebarLine = failedPlatforms.length
    ? `${failedPlatforms.join("、")}：配置错误`
    : connected
      ? "实时通道已连接"
      : "正在重连…";

  return (
    <div className="app-shell">
      <aside className={cn("sidebar", mobileNav && "open")}>
        <div className="sidebar-brand">
          <div className="brand-mark">R</div>
          <div>
            <div className="brand-name">Relay</div>
            <div className="brand-sub">统一消息台</div>
          </div>
          <button className="icon-button mobile-only" onClick={() => setMobileNav(false)} aria-label="关闭菜单">
            <X size={18} />
          </button>
        </div>

        <button className="search-pill" onClick={() => navigate("/")}>
          <Search size={15} />
          <span>搜索消息、待办</span>
          <kbd>⌘K</kbd>
        </button>

        <nav className="sidebar-nav">
          <div className="nav-caption">工作区</div>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => cn("nav-item", isActive && "active")}
              onClick={() => setMobileNav(false)}
            >
              {item.icon}
              <span>{item.label}</span>
              {typeof item.count === "number" && item.count > 0 && (
                <span className={cn("nav-count", item.to === "/" && "unread")}>{item.count}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-card">
          <div className="sidebar-card-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <strong>{sidebarTitle}</strong>
            <p>{sidebarDetail}</p>
            <span className={cn("connection-line", failedPlatforms.length ? "error" : connected ? "online" : "offline")}>
              <Radio size={11} /> {sidebarLine}
            </span>
          </div>
        </div>

        <div className="sidebar-user">
          <Avatar name={me.user.display_name} size={36} />
          <div className="sidebar-user-info">
            <strong>{me.user.display_name}</strong>
            <span>{me.user.email || "平台登录用户"}</span>
          </div>
          <button className="icon-button" onClick={() => void logout()} title="退出登录">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      {mobileNav && <div className="sidebar-backdrop" onClick={() => setMobileNav(false)} />}

      <main className="main-shell">
        <div className="mobile-topbar">
          <button className="icon-button" onClick={() => setMobileNav(true)} aria-label="打开菜单">
            <Menu size={20} />
          </button>
          <div className="brand-inline">
            <div className="brand-mark small">R</div>
            <span>Relay</span>
          </div>
          <span className={cn("live-dot", connected ? "online" : "offline")} />
        </div>
        <Outlet />
        {authError && (
          <div className="global-auth-error" role="alert">
            <AlertTriangle size={16} />
            <div className="global-auth-error-body">
              <strong>平台绑定未完成</strong>
              <span>{authError}</span>
            </div>
            <button
              className="text-button"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("auth_error");
                setSearchParams(next, { replace: true });
              }}
            >
              关闭
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
