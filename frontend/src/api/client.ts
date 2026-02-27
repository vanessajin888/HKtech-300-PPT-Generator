const BASE_URL = "http://localhost:8000";

export interface UploadResponse {
  file_id: string;
  file_name: string;
  page_count: number;
  image_urls: string[];
}

export interface SlideMeta {
  slide_index: number;
  image_url: string;
  image_w: number;
  image_h: number;
}

export interface PptObject {
  slide_index: number;
  shape_id: string;
  text: string;
  bbox_px: { x: number; y: number; w: number; h: number };
  alignment?: string | null;
  font_size_pt?: number | null;
}

export interface ObjectsResponse {
  file_id: string;
  file_name: string;
  slide_count: number;
  slides: SlideMeta[];
  objects: PptObject[];
  // 版本信息（后端新加，老数据可能没有）
  root_file_id?: string;
  parent_file_id?: string | null;
  version_index?: number;
  created_at?: string;
}

export interface Suggestion {
  id: string;
  slide_index: number;
  shape_id: string;
  original: string;
  suggested: string;
  reason: string;
  confidence?: number;
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

export async function fetchObjects(fileId: string): Promise<ObjectsResponse> {
  const res = await fetch(`${BASE_URL}/objects?file_id=${encodeURIComponent(fileId)}`);
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

export async function fetchSuggestions(
  fileId: string,
  instruction: string
): Promise<Suggestion[]> {
  const res = await fetch(`${BASE_URL}/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, instruction }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  const data = await res.json();
  return data.suggestions ?? [];
}

export interface ApplyResponse {
  output_file_id: string;
  image_urls: string[];
}

export async function applySuggestionsApi(
  fileId: string,
  accepted: (string | { slide_index: number; shape_id: string; suggested: string })[]
): Promise<ApplyResponse> {
  const res = await fetch(`${BASE_URL}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, accepted }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

export function buildDownloadUrl(fileId: string): string {
  return `${BASE_URL}/download?file_id=${encodeURIComponent(fileId)}`;
}

