import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Building2, Check, ClipboardCopy, Loader2, Smartphone } from "lucide-react";
import { api } from "../api";
import { useToast } from "../Toast";
import { PlatformBadge } from "../components/Primitives";

declare global {
  interface Window {
    ww?: any;
    wx?: any;
  }
}

export function WeComTodoPage() {
  const [params] = useSearchParams();
  const taskId = params.get("task_id") || "";
  const navigate = useNavigate();
  const toast = useToast();
  const [todoId, setTodoId] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const inWeCom = /wxwork/i.test(navigator.userAgent);

  const loadScript = (src: string) =>
    new Promise<void>((resolve, reject) => {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("企微 JS-SDK 加载失败"));
      document.head.appendChild(script);
    });

  const configureSdk = async () => {
    const config = await api.wecomConfig(window.location.href.split("#")[0]);
    if (config.demo || !config.signature || config.signature === "demo") {
      throw new Error("当前是演示模式，未配置企微 JS-SDK 签名");
    }
    await loadScript("https://res.wx.qq.com/open/js/jweixin-1.6.0.js");
    const sdk = window.ww || window.wx;
    if (!sdk) throw new Error("企微 JS-SDK 未注入");
    await new Promise<void>((resolve, reject) => {
      sdk.config({
        debug: false,
        appId: config.corp_id,
        timestamp: config.timestamp,
        nonceStr: config.nonce_str,
        signature: config.signature,
        jsApiList: ["createTodo"],
      });
      sdk.ready(() => resolve());
      sdk.error((error: unknown) => reject(new Error(`JS-SDK 配置失败: ${String(error)}`)));
    });
    return sdk;
  };

  const link = async (id: string) => {
    if (!taskId) {
      toast.push("缺少 task_id，请从任务详情进入", "error");
      return;
    }
    setBusy(true);
    try {
      await api.linkWecom(taskId, id);
      setCreated(true);
      toast.push("企微待办已关联", "success");
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "关联失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const createViaSdk = async () => {
    if (!inWeCom) {
      toast.push("请在企业微信客户端内打开此页面", "info");
      return;
    }
    setBusy(true);
    try {
      const sdk = await configureSdk();
      const result = await new Promise<any>((resolve, reject) => {
        try {
          const maybe = sdk.createTodo({
            content: "来自 Relay 的任务",
            remindTime: "",
            success: resolve,
            fail: reject,
          });
          if (maybe && typeof maybe.then === "function") {
            maybe.then(resolve, reject);
          }
        } catch (error) {
          reject(error);
        }
      });
      const id = result?.todo_id || result?.todoId || result?.id;
      if (!id) throw new Error("JS-SDK 未返回 todo_id");
      await link(id);
    } catch (error) {
      setBusy(false);
      toast.push(error instanceof Error ? error.message : "创建失败", "error");
    }
  };

  return (
    <div className="page wecom-page">
      <header className="page-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">WECOM H5</span>
            <PlatformBadge platform="wecom" compact />
          </div>
          <h1>在企微创建待办</h1>
          <p>企业微信服务端没有创建待办接口。请在企微客户端内创建，再把 todo_id 回传到 Relay。</p>
        </div>
      </header>

      <div className="wecom-grid">
        <section className="card wecom-card">
          <div className="wecom-card-head">
            <span className="wecom-icon">
              <Smartphone size={20} />
            </span>
            <div>
              <h2>路径 A · 企微客户端 JS-SDK</h2>
              <p>页面需在企微客户端内打开，并完成 JS-SDK 签名。</p>
            </div>
          </div>
          <div className={inWeCom ? "env-pill online" : "env-pill"}>
            {inWeCom ? "已检测到企业微信环境" : "当前不是企业微信环境"}
          </div>
          <button className="button primary" onClick={() => void createViaSdk()} disabled={!inWeCom || busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <Building2 size={15} />}
            调用 ww.createTodo 创建
          </button>
          <p className="field-hint">
            真实接入时，后端会通过 <code>/api/wecom/jsapi-config</code> 下发签名；创建成功后自动 POST
            到 <code>/api/tasks/{taskId || ":task_id"}/wecom-link</code>。
          </p>
        </section>

        <section className="card wecom-card">
          <div className="wecom-card-head">
            <span className="wecom-icon alt">
              <ClipboardCopy size={20} />
            </span>
            <div>
              <h2>路径 B · 手动回传 todo_id</h2>
              <p>适合已在企微客户端创建好待办，或当前在浏览器里操作。</p>
            </div>
          </div>
          <label className="field">
            <span>todo_id</span>
            <input value={todoId} onChange={(event) => setTodoId(event.target.value)} placeholder="例如 17c7d2bd9f20d652840f72f59e796AAA" />
          </label>
          <button className="button secondary" disabled={!todoId.trim() || busy || !taskId} onClick={() => void link(todoId.trim())}>
            {busy ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
            关联到任务
          </button>
          {!taskId && <p className="field-hint">缺少 task_id：请从任务详情页的“关联企微待办”进入。</p>}
          {created && (
            <button className="button ghost" onClick={() => navigate("/tasks")}>
              返回待办页
            </button>
          )}
        </section>
      </div>
    </div>
  );
}
