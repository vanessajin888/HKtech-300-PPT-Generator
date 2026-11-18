/** 开发环境（包括通过 ngrok 访问 Vite dev server）统一走 Vite 代理 /api；
 *  只有真正打包部署到独立后端时，才使用完整后端地址。
 */
export const BASE_URL =
  import.meta.env.DEV ? "/api" : "http://localhost:8001";

export interface UploadResponse {
  file_id: string;
  file_name: string;
}

export async function uploadPpt(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

export async function getFileUrl(fileId: string): Promise<{ file_url: string }> {
  // 注意：document.url 是由 DocumentServer 服务端下载的，不是浏览器下载的。
  // Docker Desktop 下容器访问宿主机用 host.docker.internal。
  //
  // - 本机 localhost 开发：直接走后端下载地址（最稳）
  // - 公网/ngrok 演示：必须返回“公网可访问”的 download URL（让对方电脑上的 ONLYOFFICE 也能下载）
  if (import.meta.env.DEV && typeof window !== "undefined") {
    const host = window.location.hostname;
    const isLocal =
      host === "localhost" || host === "127.0.0.1" || host === "::1";
    if (isLocal) {
      return {
        file_url: `http://host.docker.internal:8001/download?file_id=${encodeURIComponent(
          fileId,
        )}`,
      };
    }
    // 外网用户：走后端 /file/{id}，由后端依据 BACKEND_PUBLIC_BASE_URL 生成公网 download URL
    const res = await fetch(`${BASE_URL}/file/${encodeURIComponent(fileId)}`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  const res = await fetch(`${BASE_URL}/file/${encodeURIComponent(fileId)}`);
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

import type { SlidePatch, ScanDeckResponse, ApplyPatchesResponse } from "../aiPanel/types";

export async function scanDeck(
  fileId: string,
  instruction: string,
  currentSlideIndex?: number | null,
): Promise<ScanDeckResponse> {
  const res = await fetch(`${BASE_URL}/ai/scan-deck`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_id: fileId,
      instruction,
      current_slide_index:
        typeof currentSlideIndex === "number" ? currentSlideIndex : null,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function applyPatches(
  fileId: string,
  patches: SlidePatch[],
): Promise<ApplyPatchesResponse> {
  const res = await fetch(`${BASE_URL}/ai/apply-patches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, patches }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface ClearStorageResponse {
  deleted_files: number;
  deleted_bytes: number;
}

export async function clearStorage(): Promise<ClearStorageResponse> {
  const res = await fetch(`${BASE_URL}/clear-storage`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

export async function getOnlyOfficeLastSaved(fileId: string): Promise<{ last_saved_at: string | null }> {
  const res = await fetch(
    `${BASE_URL}/onlyoffice/last-saved/${encodeURIComponent(fileId)}`,
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// forcesave：通过 DocumentServer CommandService 把当前缓存版本强制落盘到存储。
// 需要传入 document.key（docKey），以便和编辑器当前会话对齐。
export async function forceSaveOnlyOffice(fileId: string, docKey: string): Promise<void> {
  const url = `${BASE_URL}/onlyoffice/force-save/${encodeURIComponent(
    fileId,
  )}?doc_key=${encodeURIComponent(docKey)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
}
