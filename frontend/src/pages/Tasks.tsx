import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  CheckCheck,
  CheckSquare,
  ClipboardList,
  Clock3,
  Loader2,
  Plus,
  Search,
  X,
} from "lucide-react";
import { api } from "../api";
import type { Task, TaskCounts } from "../types";
import { cn, formatDateTime, platformMeta } from "../utils";
import { useToast } from "../Toast";
import { Modal } from "../components/Modal";
import { PlatformBadge, EmptyState, StatusPill, SyncPill } from "../components/Primitives";
import { TaskActionForm } from "../components/TaskActionForm";
import { TaskCard } from "../components/TaskCard";

const tabs = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "open", label: "待处理" },
  { key: "done", label: "已完成" },
];

const emptyCounts: TaskCounts = { all: 0, open: 0, in_progress: 0, done: 0, active: 0 };

export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus");
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("all");
  const [platform, setPlatform] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Task | null>(null);
  const [wecomLinkOpen, setWecomLinkOpen] = useState(false);
  const [todoId, setTodoId] = useState("");
  const [linking, setLinking] = useState(false);

  const tasksQuery = useQuery({
    queryKey: ["tasks", status, platform, q],
    queryFn: () => api.tasks({ status, platform: platform || undefined, q: q || undefined }),
  });

  const focusQuery = useQuery({
    queryKey: ["task", focus],
    queryFn: () => api.task(focus!),
    enabled: Boolean(focus),
  });

  useEffect(() => {
    if (focusQuery.data) setSelected(focusQuery.data);
  }, [focusQuery.data]);

  useEffect(() => {
    if (!focus && selected) setSelected(null);
  }, [focus, selected]);

  const counts = tasksQuery.data?.counts || emptyCounts;
  const items = tasksQuery.data?.items || [];

  const openTask = (task: Task) => {
    setSelected(task);
    setParams({ focus: task.id });
  };

  const closeDrawer = () => {
    setSelected(null);
    const next = new URLSearchParams(params);
    next.delete("focus");
    setParams(next, { replace: true });
  };

  const refresh = async (task?: Task) => {
    if (task) {
      setSelected(task);
      queryClient.setQueryData(["task", task.id], task);
    }
    await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    await queryClient.invalidateQueries({ queryKey: ["overview"] });
  };

  const linkWecom = async () => {
    if (!selected || !todoId.trim()) return;
    setLinking(true);
    try {
      const task = await api.linkWecom(selected.id, todoId.trim());
      await refresh(task);
      setWecomLinkOpen(false);
      setTodoId("");
      toast.push("企微待办已关联", "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "关联失败", "error");
    } finally {
      setLinking(false);
    }
  };

  const platformOptions = useMemo(() => ["", "feishu", "dingtalk", "wecom"], []);

  return (
    <div className="page tasks-page">
      <header className="page-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">TASKS</span>
            <span className="live-pill">
              <CheckSquare size={12} /> {counts.active} 个进行中
            </span>
          </div>
          <h1>待办</h1>
          <p>消息转成的行动项、平台任务，以及跨平台同步状态都在这里。</p>
        </div>
        <div className="page-actions">
          <button className="button secondary" onClick={() => navigate("/")}>
            <Plus size={15} /> 从消息创建
          </button>
        </div>
      </header>

      <section className="stats-grid">
        <div className="stat-card accent">
          <div className="stat-icon">
            <ClipboardList size={18} />
          </div>
          <div>
            <span>全部待办</span>
            <strong>{counts.all}</strong>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon amber">
            <Clock3 size={18} />
          </div>
          <div>
            <span>进行中</span>
            <strong>{counts.active}</strong>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green">
            <CheckCheck size={18} />
          </div>
          <div>
            <span>已完成</span>
            <strong>{counts.done}</strong>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon violet">
            <ArrowUpRight size={18} />
          </div>
          <div>
            <span>跨平台关联</span>
            <strong>{items.filter((task) => task.links.length > 1).length}</strong>
          </div>
        </div>
      </section>

      <div className="tasks-toolbar">
        <div className="tabs">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              className={cn("tab", status === tab.key && "active")}
              onClick={() => setStatus(tab.key)}
            >
              {tab.label}
              {tab.key !== "all" && counts[tab.key as keyof TaskCounts] !== undefined && (
                <span>{counts[tab.key as keyof TaskCounts]}</span>
              )}
            </button>
          ))}
        </div>
        <div className="toolbar-right">
          <select className="select compact" value={platform} onChange={(event) => setPlatform(event.target.value)}>
            {platformOptions.map((item) => (
              <option value={item} key={item || "all"}>
                {item ? platformMeta[item as keyof typeof platformMeta].label : "全部平台"}
              </option>
            ))}
          </select>
          <label className="search-input small">
            <Search size={14} />
            <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索待办" />
          </label>
        </div>
      </div>

      {tasksQuery.isLoading && (
        <div className="list-loading card">
          <Loader2 className="spin" size={18} /> 正在加载待办…
        </div>
      )}

      {!tasksQuery.isLoading && items.length === 0 && (
        <div className="card">
          <EmptyState
            title="这个过滤条件下没有待办"
            description="去收件箱选一条消息，点“转为待办”即可创建。"
            icon={<ClipboardList size={26} />}
            action={
              <button className="button primary" onClick={() => navigate("/")}>
                去收件箱
              </button>
            }
          />
        </div>
      )}

      <div className="task-list">
        {items.map((task) => (
          <TaskCard task={task} key={task.id} onClick={() => openTask(task)} />
        ))}
      </div>

      {selected && (
        <div className="drawer-overlay" onMouseDown={closeDrawer}>
          <aside className="drawer task-drawer" onMouseDown={(event) => event.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <div className="drawer-eyebrow">
                  <StatusPill status={selected.status} />
                  <SyncPill state={selected.sync_state} />
                  {selected.source_platform && <PlatformBadge platform={selected.source_platform} compact />}
                </div>
                <h2>{selected.title}</h2>
                <p className="drawer-sub">
                  {selected.assignee_name ? `负责人：${selected.assignee_name}` : "未指派"}
                  {selected.due_at ? ` · 截止 ${formatDateTime(selected.due_at)}` : ""}
                </p>
              </div>
              <button className="icon-button" onClick={closeDrawer} aria-label="关闭">
                <X size={18} />
              </button>
            </header>

            <div className="drawer-body">
              {selected.sync_error && <div className="inline-warning">{selected.sync_error}</div>}

              <div className="drawer-section">
                <h3>描述</h3>
                <p className="drawer-description">{selected.description || "暂无描述"}</p>
              </div>

              <div className="drawer-section">
                <h3>平台同步</h3>
                <div className="link-list">
                  {selected.links.length === 0 && <p className="muted-text">暂无平台任务关联（本地待办）。</p>}
                  {selected.links.map((link) => (
                    <div className="link-row" key={link.id}>
                      <PlatformBadge platform={link.platform} />
                      <span className="link-id">{link.platform_task_id}</span>
                      <StatusPill status={(link.platform_status as Task["status"]) || "open"} />
                      <span className="link-synced">
                        {link.last_synced_at ? `同步于 ${formatDateTime(link.last_synced_at)}` : "未同步"}
                      </span>
                    </div>
                  ))}
                </div>
                {selected.source_platform === "wecom" && !selected.links.some((link) => link.platform === "wecom") && (
                  <div className="wecom-link-actions">
                    <button className="button ghost" onClick={() => setWecomLinkOpen(true)}>
                      关联已创建的企微待办
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => navigate(`/wecom/create-todo?task_id=${selected.id}`)}
                    >
                      在企微客户端创建
                    </button>
                  </div>
                )}
              </div>

              {selected.source_message && (
                <div className="drawer-section">
                  <h3>来源消息</h3>
                  <button
                    className="source-card"
                    onClick={() => navigate(`/?focus=${selected.source_message_id}`)}
                  >
                    <div className="source-card-head">
                      <PlatformBadge platform={selected.source_message.platform} compact />
                      <span>{selected.source_message.sender_name}</span>
                      <span>{formatDateTime(selected.source_message.created_at)}</span>
                    </div>
                    <p>{selected.source_message.text || "附件消息"}</p>
                    <span className="text-button">
                      定位原消息 <ArrowUpRight size={13} />
                    </span>
                  </button>
                </div>
              )}

              <div className="drawer-section">
                <h3>处理</h3>
                <TaskActionForm key={selected.id} task={selected} onUpdated={(task) => void refresh(task)} />
              </div>
            </div>
          </aside>
        </div>
      )}

      <Modal
        open={wecomLinkOpen}
        onClose={() => setWecomLinkOpen(false)}
        title="关联企业微信待办"
        subtitle="企微服务端不能创建待办；请在企微客户端创建后把 todo_id 回传。"
        footer={
          <>
            <button className="button ghost" onClick={() => setWecomLinkOpen(false)}>
              取消
            </button>
            <button className="button primary" disabled={linking || !todoId.trim()} onClick={() => void linkWecom()}>
              {linking && <Loader2 size={14} className="spin" />} 关联
            </button>
          </>
        }
      >
        <label className="field">
          <span>todo_id</span>
          <input value={todoId} onChange={(event) => setTodoId(event.target.value)} placeholder="粘贴企微待办 ID" />
        </label>
        <p className="field-hint">
          已配置企微 H5 时，也可以在企微客户端内打开 <code>/wecom/create-todo</code> 页面创建并自动回传。
        </p>
      </Modal>
    </div>
  );
}
