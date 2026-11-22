import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  FileArchive,
  FileImage,
  FileSpreadsheet,
  FileText,
  Files as FilesIcon,
  Loader2,
  Search,
} from "lucide-react";
import { api } from "../api";
import type { FileItem, Platform } from "../types";
import { cn, clipText, formatBytes, formatDateTime, platformMeta } from "../utils";
import { EmptyState, PlatformBadge } from "../components/Primitives";
import { FilePreview } from "../components/FilePreview";

const platforms: Array<"" | Platform> = ["", "feishu", "dingtalk", "wecom"];

function fileIcon(mime: string) {
  if (mime.startsWith("image/")) return <FileImage size={18} />;
  if (mime.includes("sheet") || mime.includes("csv")) return <FileSpreadsheet size={18} />;
  if (mime.includes("zip") || mime.includes("archive")) return <FileArchive size={18} />;
  return <FileText size={18} />;
}

export function FilesPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<"recent" | "all">("recent");
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState<"" | Platform>("");
  const [preview, setPreview] = useState<FileItem | null>(null);

  const filesQuery = useQuery({
    queryKey: ["files", tab, q, platform],
    queryFn: () =>
      api.files({
        recent: tab === "recent",
        q: q || undefined,
        platform: platform || undefined,
      }),
  });

  const items = filesQuery.data?.items || [];
  const totalSize = items.reduce((sum, file) => sum + file.size_bytes, 0);

  return (
    <div className="page files-page">
      <header className="page-head">
        <div>
          <div className="eyebrow-row">
            <span className="eyebrow">FILES</span>
            <span className="live-pill">
              <FilesIcon size={12} /> {items.length} 个文件
            </span>
          </div>
          <h1>文件</h1>
          <p>三个平台里打开过的文件集中在这里，随时回到原消息看上下文。</p>
        </div>
      </header>

      <section className="stats-grid compact-stats">
        <div className="stat-card accent">
          <div className="stat-icon">
            <FilesIcon size={18} />
          </div>
          <div>
            <span>当前结果</span>
            <strong>{items.length}</strong>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon violet">
            <Download size={18} />
          </div>
          <div>
            <span>总体积</span>
            <strong>{formatBytes(totalSize)}</strong>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green">
            <ExternalLink size={18} />
          </div>
          <div>
            <span>可定位原消息</span>
            <strong>{items.filter((file) => file.message_id).length}</strong>
          </div>
        </div>
      </section>

      <div className="tasks-toolbar">
        <div className="tabs">
          <button className={cn("tab", tab === "recent" && "active")} onClick={() => setTab("recent")}>
            最近打开
          </button>
          <button className={cn("tab", tab === "all" && "active")} onClick={() => setTab("all")}>
            全部文件
          </button>
        </div>
        <div className="toolbar-right">
          <select className="select compact" value={platform} onChange={(event) => setPlatform(event.target.value as "" | Platform)}>
            {platforms.map((item) => (
              <option value={item} key={item || "all"}>
                {item ? platformMeta[item].label : "全部平台"}
              </option>
            ))}
          </select>
          <label className="search-input small">
            <Search size={14} />
            <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索文件名、发送人、消息内容" />
          </label>
        </div>
      </div>

      {filesQuery.isLoading && (
        <div className="list-loading card">
          <Loader2 className="spin" size={18} /> 正在加载文件…
        </div>
      )}

      {!filesQuery.isLoading && items.length === 0 && (
        <div className="card">
          <EmptyState
            title={tab === "recent" ? "还没有打开过的文件" : "没有找到文件"}
            description={
              tab === "recent"
                ? "在消息流里点开一个附件，它会自动出现在这里。"
                : "换个搜索词或平台筛选再试试。"
            }
            icon={<FilesIcon size={26} />}
          />
        </div>
      )}

      {items.length > 0 && (
        <div className="file-table card">
          <div className="file-table-head">
            <span>文件</span>
            <span>来源</span>
            <span>大小</span>
            <span>时间</span>
            <span>操作</span>
          </div>
          {items.map((file) => (
            <div className="file-row" key={file.id}>
              <button className="file-main" onClick={() => setPreview(file)}>
                <span className={cn("file-icon tone", file.mime_type.startsWith("image/") ? "image" : "doc")}>
                  {fileIcon(file.mime_type)}
                </span>
                <span className="file-name-wrap">
                  <strong>{file.filename}</strong>
                  <span>
                    {file.sender_name ? `${file.sender_name} · ` : ""}
                    {file.message_text ? clipText(file.message_text, 42) : file.opened ? `打开 ${file.open_count} 次` : "尚未打开"}
                  </span>
                </span>
              </button>
              <span className="file-source-cell">
                <PlatformBadge platform={file.platform} compact />
                {file.sender_name && <span className="file-source-name">{file.sender_name}</span>}
              </span>
              <span className="muted-text">{formatBytes(file.size_bytes)}</span>
              <span className="muted-text">{formatDateTime(file.created_at)}</span>
              <span className="file-actions">
                <button className="icon-button" title="预览" onClick={() => setPreview(file)}>
                  <FileText size={15} />
                </button>
                <a className="icon-button" title="下载" href={`/api/files/${file.id}/download`}>
                  <Download size={15} />
                </a>
                {file.message_id && (
                  <button
                    className="icon-button"
                    title="定位原消息"
                    onClick={() => navigate(`/?focus=${file.message_id}`)}
                  >
                    <ExternalLink size={15} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {preview && (
        <FilePreview
          file={preview}
          onClose={() => setPreview(null)}
          onLocate={(messageId) => {
            setPreview(null);
            navigate(`/?focus=${messageId}`);
          }}
        />
      )}
    </div>
  );
}
