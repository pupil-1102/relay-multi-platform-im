import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileText, Loader2 } from "lucide-react";
import { api } from "../api";
import type { FileItem } from "../types";
import { formatBytes, formatDateTime, isImage, isPdf, isText } from "../utils";
import { PlatformBadge } from "./Primitives";

export function FilePreview({
  file,
  onClose,
  onLocate,
}: {
  file: FileItem;
  onClose: () => void;
  onLocate?: (messageId: string) => void;
}) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [previewable, setPreviewable] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;

    const load = async () => {
      setStatus("loading");
      try {
        const opened = await api.openFile(file.id);
        setPreviewable(opened.previewable);
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        await queryClient.invalidateQueries({ queryKey: ["messages"] });
        const response = await fetch(opened.content_url, { credentials: "include" });
        if (!response.ok) throw new Error(`读取文件失败 (${response.status})`);
        const mime = opened.file.mime_type;
        if (isText(mime)) {
          setText(await response.text());
        } else {
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          revoked = url;
          if (!cancelled) setBlobUrl(url);
        }
        if (!cancelled) setStatus("ready");
      } catch (error) {
        if (!cancelled) {
          setStatus("error");
          setMessage(error instanceof Error ? error.message : "文件打开失败");
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [file.id]);

  return (
    <div className="drawer-overlay" onMouseDown={onClose}>
      <aside className="drawer file-drawer" onMouseDown={(event) => event.stopPropagation()}>
        <header className="drawer-head">
          <div className="file-drawer-title">
            <span className="file-icon large">
              <FileText size={20} />
            </span>
            <div>
              <h2 title={file.filename}>{file.filename}</h2>
              <div className="file-meta-line">
                <PlatformBadge platform={file.platform} compact />
                {file.sender_name && <span>{file.sender_name}</span>}
                <span>{formatBytes(file.size_bytes)}</span>
                <span>{formatDateTime(file.created_at)}</span>
              </div>
            </div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>

        <div className="drawer-body file-preview-body">
          {status === "loading" && (
            <div className="preview-loading">
              <Loader2 size={22} className="spin" />
              正在准备文件…
            </div>
          )}
          {status === "error" && (
            <div className="preview-error">
              <strong>无法预览</strong>
              <p>{message}</p>
              <a className="button secondary" href={`/api/files/${file.id}/download`}>
                <Download size={15} /> 下载文件
              </a>
            </div>
          )}
          {status === "ready" && previewable && isImage(file.mime_type) && blobUrl && (
            <img className="preview-image" src={blobUrl} alt={file.filename} />
          )}
          {status === "ready" && previewable && isPdf(file.mime_type) && blobUrl && (
            <iframe className="preview-pdf" src={blobUrl} title={file.filename} />
          )}
          {status === "ready" && previewable && isText(file.mime_type) && (
            <pre className="preview-text">{text}</pre>
          )}
          {status === "ready" && !previewable && (
            <div className="preview-download-only">
              <FileText size={48} />
              <h3>该类型暂不支持在线预览</h3>
              <p>下载到本地后查看，文件仍保留与原消息的关联。</p>
            </div>
          )}
        </div>

        <footer className="drawer-footer">
          <a className="button secondary" href={`/api/files/${file.id}/download`}>
            <Download size={15} /> 下载
          </a>
          {file.message_id && onLocate && (
            <button className="button primary" onClick={() => onLocate(file.message_id!)}>
              <ExternalLink size={15} /> 定位原消息
            </button>
          )}
        </footer>
      </aside>
    </div>
  );
}
