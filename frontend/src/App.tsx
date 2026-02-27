import React, { useState } from "react";
import {
  uploadPpt,
  fetchObjects,
  fetchSuggestions,
  applySuggestionsApi,
  type ObjectsResponse,
  type Suggestion,
  buildDownloadUrl,
} from "./api/client";
import { PptViewer } from "./components/PptViewer";
import { SuggestionList } from "./components/SuggestionList";

interface UploadedFileInfo {
  file_id: string;
  file_name: string;
  version_index?: number;
}

interface ChatRound {
  id: string;
  instruction: string;
  suggestions: Suggestion[];
  createdAt: string;
}

const App: React.FC = () => {
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([]);
  const [currentFileId, setCurrentFileId] = useState<string | undefined>();
  const [currentFileName, setCurrentFileName] = useState<string | undefined>();
  const [objectsData, setObjectsData] = useState<ObjectsResponse | undefined>();
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [acceptedIds, setAcceptedIds] = useState<string[]>([]);
  const [appliedShapeIds, setAppliedShapeIds] = useState<string[]>([]);
  const [selectedShapeId, setSelectedShapeId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  // 每个文件的 chat 轮次历史
  const [chatRoundsByFile, setChatRoundsByFile] = useState<
    Record<string, ChatRound[]>
  >({});
  const [activeRoundId, setActiveRoundId] = useState<string | null>(null);

  const handleUpload = async (file: File) => {
    try {
      setStatus("上传并渲染中...");
      const res = await uploadPpt(file);
      const fileId = res.file_id;
      const objRes = await fetchObjects(fileId);
      setCurrentFileId(fileId);
      setCurrentFileName(objRes.file_name);
      setUploadedFiles((prev) => [
        ...prev,
        {
          file_id: fileId,
          file_name: objRes.file_name,
          version_index: objRes.version_index ?? 0,
        },
      ]);
      setObjectsData(objRes);
      setCurrentSlideIndex(0);
      setSuggestions([]);
      setAcceptedIds([]);
      setAppliedShapeIds([]);
      setSelectedShapeId(null);
      setActiveRoundId(null);
      setStatus("上传完成，可以生成建议。");
    } catch (e: any) {
      console.error(e);
      setStatus(`上传失败: ${String(e.message || e)}`);
    }
  };

  const handleSelectFile = async (fileId: string) => {
    try {
      setStatus("加载对象数据...");
      const objRes = await fetchObjects(fileId);
      setObjectsData(objRes);
      setCurrentFileId(fileId);
      setCurrentFileName(objRes.file_name);
      setCurrentSlideIndex(0);
      setSuggestions([]);
      setAcceptedIds([]);
      setAppliedShapeIds([]);
      setSelectedShapeId(null);
      setActiveRoundId(null);
      setStatus("已切换到该文件。");
    } catch (e: any) {
      console.error(e);
      setStatus(`加载失败: ${String(e.message || e)}`);
    }
  };

  const handleGenerateSuggestions = async (instruction: string) => {
    if (!currentFileId) return;
    try {
      setStatus("生成建议中...");
      const list = await fetchSuggestions(currentFileId, instruction);
      // 记录当前轮 chat
      const roundId = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const createdAt = new Date().toLocaleTimeString("zh-CN", {
        hour12: false,
      });
      setChatRoundsByFile((prev) => {
        const existing = prev[currentFileId] ?? [];
        return {
          ...prev,
          [currentFileId]: [
            ...existing,
            { id: roundId, instruction, suggestions: list, createdAt },
          ],
        };
      });

      setActiveRoundId(roundId);
      setSuggestions(list);
      setAcceptedIds(list.map((s) => s.id)); // 默认全选
      setAppliedShapeIds([]); // 还未应用
      setStatus(`生成了 ${list.length} 条建议。`);
    } catch (e: any) {
      console.error(e);
      setStatus(`生成建议失败: ${String(e.message || e)}`);
    }
  };

  const handleToggleAccept = (id: string) => {
    setAcceptedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const applyWithIds = async (ids: string[]) => {
    if (!currentFileId) return;
    try {
      setStatus("应用修改中...");
      const res = await applySuggestionsApi(currentFileId, ids);
      const newFileId = res.output_file_id;
      setCurrentFileId(newFileId);

      // 记录哪些 shape 已经被应用（用于绿色高亮）
      const appliedShapes = suggestions
        .filter((s) => ids.includes(s.id))
        .map((s) => s.shape_id);
      setAppliedShapeIds(appliedShapes);

      // 刷新 objects 数据
      const objRes = await fetchObjects(newFileId);
      setObjectsData(objRes);
      setCurrentSlideIndex(0);

      // 将新的版本加入 Uploaded 列表（携带版本号）
      setUploadedFiles((prev) => [
        ...prev,
        {
          file_id: newFileId,
          file_name: objRes.file_name,
          version_index: objRes.version_index ?? undefined,
        },
      ]);

      // 应用之后，新版本的 chat 轮次默认沿用原文件的历史
      setChatRoundsByFile((prev) => {
        const existing = prev[currentFileId] ?? [];
        return {
          ...prev,
          [newFileId]: existing,
        };
      });
      setActiveRoundId(null);

      setStatus("修改已应用并重新渲染。");
    } catch (e: any) {
      console.error(e);
      setStatus(`应用失败: ${String(e.message || e)}`);
    }
  };

  const handleApply = async () => {
    await applyWithIds(acceptedIds.slice());
  };

  const handleAcceptAll = async () => {
    if (!currentFileId || suggestions.length === 0) return;
    const allIds = suggestions.map((s) => s.id);
    setAcceptedIds(allIds);
    await applyWithIds(allIds);
  };

  const handleDeclineAll = () => {
    if (!currentFileId || suggestions.length === 0) return;
    setAcceptedIds([]);
    setAppliedShapeIds([]);
    setStatus("本轮建议已被拒绝。");
  };

  const handleJumpTo = (slideIndex: number, shapeId: string) => {
    setCurrentSlideIndex(slideIndex);
    setSelectedShapeId(shapeId);
    // 轻微延迟后取消“选中”高亮效果
    setTimeout(() => {
      setSelectedShapeId(null);
    }, 800);
  };

  const handleDeleteFile = (fileId: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.file_id !== fileId));
    setChatRoundsByFile((prev) => {
      const next = { ...prev };
      delete next[fileId];
      return next;
    });

    if (currentFileId === fileId) {
      setCurrentFileId(undefined);
      setCurrentFileName(undefined);
      setObjectsData(undefined);
      setSuggestions([]);
      setAcceptedIds([]);
      setAppliedShapeIds([]);
      setSelectedShapeId(null);
      setActiveRoundId(null);
      setStatus("当前文件已删除，请重新上传。");
    }
  };

  const handleSelectRound = (roundId: string) => {
    if (!currentFileId) return;
    const rounds = chatRoundsByFile[currentFileId] ?? [];
    const round = rounds.find((r) => r.id === roundId);
    if (!round) return;
    setActiveRoundId(roundId);
    setSuggestions(round.suggestions);
    setAcceptedIds(round.suggestions.map((s) => s.id));
    setAppliedShapeIds([]);
    setStatus(`已切换到该轮建议（共 ${round.suggestions.length} 条）。`);
  };

  const onFileInputChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      void handleUpload(file);
      e.target.value = "";
    }
  };

  const slides = objectsData?.slides ?? [];
  const objects = objectsData?.objects ?? [];
  const highlightedShapeIds =
    suggestions.length > 0
      ? suggestions.map((s) => s.shape_id)
      : objects.map((o) => o.shape_id);

  const currentRounds: ChatRound[] =
    currentFileId && chatRoundsByFile[currentFileId]
      ? chatRoundsByFile[currentFileId]
      : [];
  const currentRoundMetas = currentRounds.map((r) => ({
    id: r.id,
    instruction: r.instruction,
    createdAt: r.createdAt,
    suggestionCount: r.suggestions.length,
  }));

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        fontSize: 13,
        background:
          "radial-gradient(circle at top left, #e0f2fe 0, #f9fafb 45%, #f3f4f6 100%)",
      }}
    >
      <header
        style={{
          padding: "10px 20px",
          borderBottom: "1px solid rgba(148,163,184,0.25)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          backgroundColor: "rgba(248,250,252,0.85)",
          backdropFilter: "blur(10px)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontWeight: 600,
            color: "#0f172a",
          }}
        >
          <div
            style={{
              width: 9,
              height: 24,
              borderRadius: 999,
              background:
                "linear-gradient(180deg, #2563eb 0%, #7c3aed 50%, #ec4899 100%)",
            }}
          />
          <span>PPT Cursor-like 可视化编辑</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <label
            style={{
              padding: "6px 12px",
              borderRadius: 999,
              border: "1px solid #d1d5db",
              backgroundColor: "#ffffff",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            选择 PPTX 上传
            <input
              type="file"
              accept=".pptx"
              style={{ display: "none" }}
              onChange={onFileInputChange}
            />
          </label>
          {currentFileId && (
            <a
              href={buildDownloadUrl(currentFileId)}
              style={{
                padding: "6px 12px",
                borderRadius: 999,
                border: "1px solid rgba(37,99,235,0.3)",
                backgroundColor: "#1d4ed8",
                textDecoration: "none",
                color: "#ffffff",
                fontSize: 12,
                boxShadow: "0 8px 18px rgba(37,99,235,0.25)",
              }}
            >
              下载当前 PPTX
            </a>
          )}
        </div>
      </header>

      <div
        style={{
          padding: "6px 20px 12px",
          fontSize: 12,
          color: "#6b7280",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>
          当前文件：<strong>{currentFileName ?? "暂无"}</strong>
        </span>
        <span>{status}</span>
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          padding: "0 16px 16px",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            height: "100%",
            display: "flex",
            minHeight: 0,
            borderRadius: 16,
            overflow: "hidden",
            backgroundColor: "#ffffff",
            boxShadow: "0 24px 80px rgba(15,23,42,0.18)",
          }}
        >
        {/* 左：PowerPoint / 历史版本列表 */}
        <aside
          style={{
            width: 240,
            borderRight: "1px solid #e5e7eb",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "#f9fafb",
          }}
        >
          <div
            style={{
              padding: 10,
              borderBottom: "1px solid #e5e7eb",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontWeight: 600 }}>PowerPoint</div>
            <div
              style={{
                fontSize: 11,
                color: "#6b7280",
              }}
            >
              History
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {uploadedFiles.map((f) => {
              const active = f.file_id === currentFileId;
              return (
                <div
                  key={f.file_id}
                  onClick={() => void handleSelectFile(f.file_id)}
                  style={{
                    padding: "8px 10px",
                    cursor: "pointer",
                    backgroundColor: active ? "#e5f0ff" : "transparent",
                    borderBottom: "1px solid #f3f4f6",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 500,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {f.file_name}
                      {typeof f.version_index === "number"
                        ? `  (v${f.version_index})`
                        : null}
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteFile(f.file_id);
                      }}
                      style={{
                        border: "none",
                        background: "transparent",
                        color: "#9ca3af",
                        cursor: "pointer",
                        padding: 0,
                        fontSize: 14,
                      }}
                      aria-label="删除该 PPT 版本"
                    >
                      ×
                    </button>
                  </div>
                  <div style={{ fontSize: 11, color: "#6b7280" }}>{f.file_id}</div>
                </div>
              );
            })}
            {!uploadedFiles.length && (
              <div style={{ padding: 10, fontSize: 12, color: "#9ca3af" }}>
                暂无上传记录。
              </div>
            )}
          </div>
        </aside>

        {/* 中：PPT 预览 */}
        <main
          style={{
            flex: 1,
            borderRight: "1px solid #e5e7eb",
            minWidth: 0,
            backgroundColor: "#f3f4f6",
          }}
        >
          <PptViewer
            slides={slides}
            objects={objects}
            currentSlideIndex={currentSlideIndex}
            highlightedShapeIds={highlightedShapeIds}
            appliedShapeIds={appliedShapeIds}
            selectedShapeId={selectedShapeId}
            onChangeSlide={setCurrentSlideIndex}
          />
        </main>

        {/* 右：Chat + Suggestions */}
        <aside
          style={{
            width: 360,
            minWidth: 320,
            backgroundColor: "#f9fafb",
            borderLeft: "1px solid #e5e7eb",
          }}
        >
          <SuggestionList
            fileId={currentFileId}
            suggestions={suggestions}
            acceptedIds={acceptedIds}
            onGenerate={handleGenerateSuggestions}
            onToggleAccept={handleToggleAccept}
            onApply={handleApply}
            onJumpTo={handleJumpTo}
            rounds={currentRoundMetas}
            activeRoundId={activeRoundId}
            onSelectRound={handleSelectRound}
            onAcceptAll={handleAcceptAll}
            onDeclineAll={handleDeclineAll}
          />
        </aside>
        </div>
      </div>
    </div>
  );
};

export default App;

