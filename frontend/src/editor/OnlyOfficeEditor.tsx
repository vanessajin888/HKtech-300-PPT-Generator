import React, { useEffect, useRef } from "react";
import type { OnlyOfficeConfig, OnlyOfficeEditorInstance } from "./types";

// 方案 B：ONLYOFFICE api.js 优先从同源 /web-apps/... 加载（由 Vite 代理到本机 80）
// 外网通过 ngrok 访问时也无需改 env；如有需要仍可用 VITE_ONLYOFFICE_API_JS_URL 覆盖。
const LOCAL_ONLYOFFICE_API_JS_URL =
  "http://localhost/web-apps/apps/api/documents/api.js";

const DEFAULT_ONLYOFFICE_API_JS_URL =
  typeof window !== "undefined"
    ? `${window.location.origin}/web-apps/apps/api/documents/api.js`
    : LOCAL_ONLYOFFICE_API_JS_URL;

const ONLYOFFICE_API_JS_URL = String(
  import.meta.env.VITE_ONLYOFFICE_API_JS_URL || "",
).trim() || DEFAULT_ONLYOFFICE_API_JS_URL;

// 插件 config.json 也走同源 /api/oo-plugins/...（Vite 代理到后端 8001）
// 关键：给 config.json 加版本参数，强制 DocumentServer 不命中旧缓存
const DEFAULT_PLUGIN_CONFIG_URL =
  typeof window !== "undefined"
    ? `${window.location.origin}/api/oo-plugins/patcher/config.json`
    : "http://localhost:8001/oo-plugins/patcher/config.json";

function buildCallbackUrl(fileId: string): string {
  // 本机：ONLYOFFICE 跑在 Docker 容器里，通过 host.docker.internal 访问宿主 backend:8001
  return `http://host.docker.internal:8001/onlyoffice/callback/${encodeURIComponent(
    fileId,
  )}`;
}

function loadOnlyOfficeApiJs(src: string): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.DocsAPI) return Promise.resolve();

  const existing = document.querySelector(
    'script[data-onlyoffice-api="1"]',
  ) as HTMLScriptElement | null;
  if (existing) {
    // 如果已存在脚本但 src 不同，说明我们需要切换到另一个地址（ngrok ↔ localhost）
    if (existing.src && existing.src !== src) {
      try {
        existing.remove();
      } catch (_) {}
    } else {
      const state = existing.dataset.onlyofficeState || "";
      if (state === "loaded") return Promise.resolve();
      if (state === "error") {
        try {
          existing.remove();
        } catch (_) {}
      } else {
        return new Promise((resolve, reject) => {
          existing.addEventListener(
            "load",
            () => {
              existing.dataset.onlyofficeState = "loaded";
              resolve();
            },
            { once: true },
          );
          existing.addEventListener(
            "error",
            () => {
              existing.dataset.onlyofficeState = "error";
              reject(new Error("ONLYOFFICE api.js 加载失败"));
            },
            { once: true },
          );
          // 超时兜底：避免脚本已失败但没触发事件导致永远 pending
          setTimeout(() => {
            if (!window.DocsAPI) {
              reject(new Error("ONLYOFFICE api.js 加载超时"));
            }
          }, 12000);
        });
      }
    }
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = src;
    script.async = true;
    script.dataset.onlyofficeApi = "1";
    script.dataset.onlyofficeState = "loading";
    script.onload = () => {
      script.dataset.onlyofficeState = "loaded";
      resolve();
    };
    script.onerror = () => {
      script.dataset.onlyofficeState = "error";
      reject(new Error(`ONLYOFFICE api.js 加载失败：${src}`));
    };
    document.head.appendChild(script);
    setTimeout(() => {
      if (!window.DocsAPI) {
        reject(new Error(`ONLYOFFICE api.js 加载超时：${src}`));
      }
    }, 12000);
  });
}

interface OnlyOfficeEditorProps {
  fileId: string;
  fileUrl: string;
  title: string;
  // 仅用于 ONLYOFFICE 版本刷新：可以是 "fileId_v0"、"fileId_1234567890" 等
  docKey: string;
  onEditorReady?: (instance: OnlyOfficeEditorInstance) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

export const OnlyOfficeEditor: React.FC<OnlyOfficeEditorProps> = ({
  fileId,
  fileUrl,
  title,
  docKey,
  onEditorReady,
  onDirtyChange,
}) => {
  const editorRef = useRef<OnlyOfficeEditorInstance | null>(null);
  const containerId = "docEditor";

  useEffect(() => {
    let cancelled = false;

    const config: OnlyOfficeConfig = {
      documentType: "slide",
      document: {
        fileType: "pptx",
        // 关键约束：document.key 由外部传入，用于区分版本
        key: docKey,
        title,
        url: fileUrl,
      },
      editorConfig: {
        mode: "edit",
        lang: "zh-CN",
        user: { id: "local-user", name: "本地用户" },
        callbackUrl: buildCallbackUrl(fileId),
        plugins: {
          autostart: ["asc.{D4A5F5D5-0A9E-4B7D-A3F2-8C0E0B5C9B51}"],
          pluginsData: [DEFAULT_PLUGIN_CONFIG_URL],
        },
      },
      events: {
        onDocumentReady: () => {
          // eslint-disable-next-line no-console
          console.log("[ONLYOFFICE] onDocumentReady");
        },
        onDocumentStateChange: (e: any) => {
          // e: true 表示有未保存修改，false 表示已保存/无修改
          try {
            const dirty = typeof e === "boolean" ? e : e?.data === true;
            onDirtyChange && onDirtyChange(dirty);
          } catch (_) {}
        },
        onError: (e: any) => {
          // eslint-disable-next-line no-console
          console.error("[ONLYOFFICE] onError", e);
        },
        onRequestRefreshFile: () => {
          // ONLYOFFICE 会在它判断文件版本已过期时触发这个事件
          // 我们这里先打日志；实际刷新由外部调用 refreshFile(config) 主动触发
          // eslint-disable-next-line no-console
          console.log("[ONLYOFFICE] onRequestRefreshFile");
        },
      },
    };

    // 只在“本机访问”时才允许 fallback 到 localhost。
    // 外网用户如果 fallback 到 localhost，会导致一直 ERR_CONNECTION_REFUSED，看起来像“编辑器空白”。
    const primary = ONLYOFFICE_API_JS_URL;
    const isLocalHost =
      typeof window !== "undefined" &&
      (window.location.hostname === "localhost" ||
        window.location.hostname === "127.0.0.1" ||
        window.location.hostname === "::1");
    const fallback =
      isLocalHost && primary !== LOCAL_ONLYOFFICE_API_JS_URL
        ? LOCAL_ONLYOFFICE_API_JS_URL
        : "";

    loadOnlyOfficeApiJs(primary)
      .catch((e) => {
        if (!fallback) throw e;
        console.warn("[ONLYOFFICE] primary api.js failed, fallback to localhost");
        return loadOnlyOfficeApiJs(fallback);
      })
      .then(() => {
        if (cancelled) return;
        if (!window.DocsAPI) {
          console.error("DocsAPI 未加载：ONLYOFFICE api.js 可能被拦截或地址不可达。");
          return;
        }
        editorRef.current = new window.DocsAPI.DocEditor(containerId, config);
        if (onEditorReady && editorRef.current) {
          onEditorReady(editorRef.current);
        }
      })
      .catch((e) => console.error(e));

    return () => {
      cancelled = true;
      if (editorRef.current) {
        editorRef.current.destroyEditor();
        editorRef.current = null;
      }
    };
  }, [fileId, fileUrl, title, docKey, onEditorReady, onDirtyChange]);

  return (
    <div
      id={containerId}
      style={{
        width: "100%",
        height: "100%",
        border: "none",
      }}
    />
  );
};

