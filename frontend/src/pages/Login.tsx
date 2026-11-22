import { useNavigate, useSearchParams, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  CheckCircle2,
  Loader2,
  LogIn,
  MessageSquare,
  ShieldCheck,
  UserPlus,
  Zap,
} from "lucide-react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import type { Platform } from "../types";
import { platformMeta } from "../utils";
import { useToast } from "../Toast";
import { useState } from "react";

const platformCopy: Record<Platform, { title: string; subtitle: string }> = {
  feishu: { title: "飞书", subtitle: "任务 v2 · 引用回复 · 任务事件" },
  dingtalk: { title: "钉钉", subtitle: "Stream · 待办事件 · 群引用" },
  wecom: { title: "企业微信", subtitle: "智能机器人 · 待办状态 · H5 创建" },
};

type Mode = "login" | "register";

export function Login() {
  const { me, loginDemo, loginWithPassword, registerAccount } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const config = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig });

  if (me) {
    return <Navigate to="/" replace />;
  }

  const authError = params.get("auth_error");
  const allowRegistration = config.data?.allow_registration !== false;
  const isRegister = mode === "register";

  const submit = async () => {
    const mail = email.trim();
    if (!mail || !mail.includes("@")) {
      setFormError("请输入有效的邮箱地址");
      return;
    }
    if (!password) {
      setFormError("请输入密码");
      return;
    }
    if (isRegister && password.length < 8) {
      setFormError("密码至少需要 8 位");
      return;
    }
    setFormError("");
    setFormBusy(true);
    try {
      if (isRegister) {
        await registerAccount(mail, password, displayName);
        toast.push("账号已创建，欢迎使用 Relay", "success");
      } else {
        await loginWithPassword(mail, password);
      }
      navigate("/", { replace: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : "操作失败";
      setFormError(message);
    } finally {
      setFormBusy(false);
    }
  };

  const demo = async () => {
    setDemoBusy(true);
    try {
      await loginDemo();
      navigate("/", { replace: true });
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "登录失败", "error");
    } finally {
      setDemoBusy(false);
    }
  };

  return (
    <div className="login-page">
      <section className="login-brand">
        <div className="login-brand-top">
          <div className="brand-mark large">R</div>
          <div>
            <div className="brand-name light">Relay</div>
            <div className="brand-sub light">统一消息台</div>
          </div>
        </div>
        <div className="login-hero">
          <p className="eyebrow">ONE INBOX · ONE ACTION LOOP</p>
          <h1>
            三个平台的消息，
            <br />
            一个地方处理完。
          </h1>
          <p className="login-lead">
            接收、阅读、回复、建待办、回写状态。平台差异封装在 Adapter 层，业务闭环只经过一套统一模型。
          </p>
          <ul className="login-points">
            <li>
              <CheckCircle2 size={17} /> 三平台消息实时汇聚，引用回复保留上下文
            </li>
            <li>
              <CheckCircle2 size={17} /> 待办状态双向同步，sync_token 抑制事件回声
            </li>
            <li>
              <CheckCircle2 size={17} /> 消息一键转待办，文件与上下文可回溯
            </li>
          </ul>
        </div>
        <div className="login-brand-foot">
          <span>
            <ShieldCheck size={14} /> 平台凭据仅存于后端
          </span>
          <span>
            <Zap size={14} /> WebSocket 实时推送
          </span>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-card-head">
            <div className="login-icon">
              {isRegister ? <UserPlus size={21} /> : <Building2 size={21} />}
            </div>
            <div>
              <h2>{isRegister ? "创建账号" : "登录工作台"}</h2>
              <p>{isRegister ? "注册后即可独立收发消息、待办与文件" : "使用邮箱密码，或授权平台账号登录"}</p>
            </div>
          </div>

          {authError && <div className="auth-error">{authError}</div>}

          <div className="login-tabs" role="tablist" aria-label="登录或注册">
            <button
              type="button"
              role="tab"
              aria-selected={!isRegister}
              className={`login-tab ${!isRegister ? "active" : ""}`}
              onClick={() => {
                setMode("login");
                setFormError("");
              }}
            >
              <LogIn size={14} /> 登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={isRegister}
              className={`login-tab ${isRegister ? "active" : ""}`}
              onClick={() => {
                if (!allowRegistration) {
                  toast.push("当前部署已关闭注册，请联系管理员", "info");
                  return;
                }
                setMode("register");
                setFormError("");
              }}
              disabled={!allowRegistration}
            >
              <UserPlus size={14} /> 注册
            </button>
          </div>

          <form
            className="login-form"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <label className="field">
              <span>邮箱</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                aria-label="邮箱"
              />
            </label>
            {isRegister && (
              <label className="field">
                <span>显示名称（可选）</span>
                <input
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="你的姓名"
                  autoComplete="nickname"
                  aria-label="显示名称"
                />
              </label>
            )}
            <label className="field">
              <span>密码</span>
              <input
                type="password"
                autoComplete={isRegister ? "new-password" : "current-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={isRegister ? "至少 8 位" : "输入密码"}
                aria-label="密码"
              />
            </label>
            {formError && <p className="login-form-error">{formError}</p>}
            <button className="button primary wide" type="submit" disabled={formBusy}>
              {formBusy ? (
                <Loader2 size={16} className="spin" />
              ) : isRegister ? (
                <UserPlus size={16} />
              ) : (
                <LogIn size={16} />
              )}
              {isRegister ? "创建账号" : "登录"}
            </button>
            {!allowRegistration && (
              <p className="login-hint">当前部署已关闭自助注册，请使用平台账号登录或联系管理员。</p>
            )}
          </form>

          <div className="login-divider">
            <span>或使用平台账号</span>
          </div>

          <div className="login-platforms">
            {(Object.keys(platformMeta) as Platform[]).map((platform) => {
              const configured = config.data?.configured?.[platform];
              return (
                <a
                  key={platform}
                  className={`login-platform ${configured ? "" : "disabled"}`}
                  href={`/api/auth/${platform}/start`}
                  onClick={(event) => {
                    if (!configured) {
                      event.preventDefault();
                      toast.push(`${platformMeta[platform].label} OAuth 尚未配置，请先填写环境变量`, "info");
                    }
                  }}
                >
                  <span className="login-platform-dot" style={{ background: platformMeta[platform].color }} />
                  <span className="login-platform-main">
                    <strong>{platformCopy[platform].title}</strong>
                    <span>{platformCopy[platform].subtitle}</span>
                  </span>
                  <span className="login-platform-state">
                    {configured ? "OAuth 登录" : "未配置"}
                    <ArrowRight size={14} />
                  </span>
                </a>
              );
            })}
          </div>

          {config.data?.dev_login && (
            <>
              <div className="login-divider">
                <span>或</span>
              </div>
              <button className="button ghost wide" onClick={() => void demo()} disabled={demoBusy}>
                {demoBusy ? <Loader2 size={16} className="spin" /> : <MessageSquare size={16} />}
                {config.data?.demo_mode ? "进入演示工作台" : "本地试运行登录"}
              </button>
            </>
          )}

          <p className="login-note">
            {config.data?.demo_mode
              ? "演示模式使用 mock 平台适配器，全部闭环能力可操作、可验收；真实平台凭据配置后可切换。"
              : "账号注册与邮箱登录可直接使用；平台账号授权后才会接收该平台的真实消息。"}
          </p>
        </div>

        <div className="login-meta">
          <span>Relay 1.0</span>
          <span>FastAPI · React · SQLite</span>
          <span>多平台工作台</span>
        </div>
      </section>
    </div>
  );
}
