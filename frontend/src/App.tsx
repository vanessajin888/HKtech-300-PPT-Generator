import React, { useCallback, useState, useEffect } from "react";
import { OnlyOfficeEditor } from "./editor/OnlyOfficeEditor";
import { AiPanel } from "./aiPanel/AiPanel";
import type { OnlyOfficeEditorInstance } from "./editor/types";
import type { GlobalState, PatchState, ReviewPatch } from "./aiPanel/types";
import {
  uploadPpt,
  getFileUrl,
  scanDeck,
  applyPatches,
  clearStorage,
  BASE_URL,
  getOnlyOfficeLastSaved,
  forceSaveOnlyOffice,
} from "./api/client";

const OO_PLUGIN_GUID = "asc.{D4A5F5D5-0A9E-4B7D-A3F2-8C0E0B5C9B51}";

const App: React.FC = () => {
  const [fileId, setFileId] = useState<string | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string>("未选择文件");
  const [patches, setPatches] = useState<ReviewPatch[]>([]);
  const [editor, setEditor] = useState<OnlyOfficeEditorInstance | null>(null);
  const [status, setStatus] = useState<string>("");
  const [globalState, setGlobalState] = useState<GlobalState>("idle");
  const [currentPatchIndex, setCurrentPatchIndex] = useState(0);
  const [aiPanelOpen, setAiPanelOpen] = useState(true);
  const [currentSlideIndex, setCurrentSlideIndex] = useState<number | null>(null);
  const [docDirty, setDocDirty] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [docKey, setDocKey] = useState<string | null>(null);

  // 手动编辑自动同步：文档变脏后，1.5s 内无新操作则触发一次保存（让 DocumentServer callback 回写后端）
  const autoSaveTimerRef = React.useRef<number | null>(null);
  const lastAutoSaveAtRef = React.useRef<number>(0);

  // 监听 ONLYOFFICE 插件上报的“当前页”
  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      try {
        const raw = ev.data;
        const msg = typeof raw === "string" ? JSON.parse(raw) : raw;
        if (!msg || msg.type !== "aiPatchBridge:event") return;
        const payload = msg.payload || {};
        if (payload.event === "currentSlide" && typeof payload.slideIndex === "number") {
          setCurrentSlideIndex(payload.slideIndex);
        }
      } catch (_) {}
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const handleClearStorage = useCallback(async () => {
    if (
      typeof window !== "undefined" &&
      !window.confirm("确定要清空 storage 缓存吗？这会删除 uploads/outputs/objects（以及遗留的 renders）下的所有文件。")
    ) {
      return;
    }
    try {
      setStatus("正在清空 storage 缓存...");
      const res = await clearStorage();
      const mb = res.deleted_bytes / (1024 * 1024);
      setStatus(
        `已清理 ${res.deleted_files} 个文件，释放约 ${mb.toFixed(1)} MB。下次上传会重新生成缓存。`,
      );
    } catch (err: any) {
      setStatus(`清空 storage 失败：${String(err?.message || err)}`);
    }
  }, []);

  const waitForBackendSave = useCallback(
    async (timeoutMs: number = 15000) => {
      if (!fileId) return;
      const start = Date.now();
      const baseline = lastSavedAt;
      while (Date.now() - start < timeoutMs) {
        try {
          const res = await getOnlyOfficeLastSaved(fileId);
          const ts = res.last_saved_at || null;
          if (ts && ts !== baseline) {
            setLastSavedAt(ts);
            setDocDirty(false);
            return;
          }
        } catch (_) {}
        await new Promise((r) => setTimeout(r, 600));
      }
    },
    [fileId, lastSavedAt],
  );

  const handleDownloadPpt = useCallback(async () => {
    if (!fileId) return;

    // 若检测到文档有未同步到后端的手动修改，先触发一次 forcesave 并等待回调成功
    if (docDirty) {
      setStatus("检测到你有手动修改，先保存到后端再下载 PPTX...");
      try {
        const key = docKey || `${fileId}_v0`;
        await forceSaveOnlyOffice(fileId, key);
      } catch (e: any) {
        // forcesave 失败时不中断下载，但会在控制台输出，便于排查
        // eslint-disable-next-line no-console
        console.error("forceSaveOnlyOffice before download failed", e);
      }
      await waitForBackendSave(20000);
    }

    const url = `${BASE_URL}/download?file_id=${encodeURIComponent(fileId)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }, [fileId, docDirty, docKey, waitForBackendSave]);

  const handleUpload: React.ChangeEventHandler<HTMLInputElement> = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setStatus("上传中...");
      const res = await uploadPpt(file);
      setFileId(res.file_id);
      setFileName(res.file_name);
      setDocKey(`${res.file_id}_v0`);
      const urlRes = await getFileUrl(res.file_id);
      setFileUrl(urlRes.file_url);
      setPatches([]);
      setGlobalState("idle");
      setCurrentPatchIndex(0);
      setStatus("上传完成，可以 Scan Deck。");
    } catch (err: any) {
      setStatus(`上传失败：${String(err?.message || err)}`);
    } finally {
      e.target.value = "";
    }
  };

  const handleAsk = useCallback(
    async (instruction: string): Promise<string> => {
      if (!fileId) {
        return "请先上传 PPTX 文件，然后再告诉我你想怎么改。";
      }
      setGlobalState("analyzing");
      setStatus("AI 正在分析你的 PPT...");

      const res = await scanDeck(fileId, instruction, currentSlideIndex);
      const reviewPatches: ReviewPatch[] = res.patches.map((p) => ({
        ...p,
        uiState: "pending",
      }));

      if (!reviewPatches.length) {
        setPatches([]);
        setGlobalState("done");
        setStatus("没有找到需要修改的内容。");
        return "我看了一圈，这套 PPT 暂时没有明显需要修改的地方。";
      }

      setPatches(reviewPatches);
      setCurrentPatchIndex(0);
      setGlobalState("reviewing");
      setStatus(`我找到了 ${reviewPatches.length} 处可以优化的地方，我们一个一个来看。`);

      const lines = reviewPatches.slice(0, 3).map((p) => {
        return `- 第 ${p.slide_index + 1} 页，第 ${p.shape_index + 1} 个文本框`;
      });

      return `我已经看完了这份演示文稿，初步找到了 ${
        reviewPatches.length
      } 处可以优化的地方：\n${lines.join(
        "\n",
      )}\n\n我们从第一条建议开始，你可以逐条 Preview / Accept / Decline。`;
    },
    [fileId, currentSlideIndex, setPatches],
  );

  const sendPluginCommand = useCallback(
    (action: string, payload: Record<string, unknown> = {}) => {
      try {
        const msg = {
          frameEditorId: "frameEditor",
          guid: OO_PLUGIN_GUID,
          type: "onExternalPluginMessage",
          data: {
            type: "command",
            action,
            payload,
          },
        };
        const iframe = document.querySelector(
          'iframe[name="frameEditor"]',
        ) as HTMLIFrameElement | null;
        if (!iframe?.contentWindow) return;
        iframe.contentWindow.postMessage(JSON.stringify(msg), "*");
      } catch (e) {
        console.error("sendPluginCommand error", e);
      }
    },
    [],
  );

  useEffect(() => {
    if (!docDirty) {
      if (autoSaveTimerRef.current) {
        window.clearTimeout(autoSaveTimerRef.current);
        autoSaveTimerRef.current = null;
      }
      return;
    }
    // 防抖：连续操作只保存一次
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
    }
      autoSaveTimerRef.current = window.setTimeout(() => {
      autoSaveTimerRef.current = null;
      const now = Date.now();
      // 最小间隔，避免某些场景频繁触发
      if (now - lastAutoSaveAtRef.current < 3000) return;
      lastAutoSaveAtRef.current = now;

      // 自动保存：通过 DocumentServer forcesave + 回调，确保 outputs/{file_id}.pptx 同步
      (async () => {
        try {
          if (!fileId) return;
          const key = docKey || `${fileId}_v0`;
          setStatus("检测到手动编辑，正在自动保存并同步到后端...");
          await forceSaveOnlyOffice(fileId, key);
          await waitForBackendSave(20000);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error("auto forceSaveOnlyOffice failed", e);
        }
      })();
    }, 1500);
  }, [docDirty, fileId, docKey, waitForBackendSave]);

  const ensureSyncedBeforeApply = useCallback(async () => {
    if (!fileId) return;
    if (!docDirty) return;
    setStatus("检测到你有手动修改，先同步保存到后端再执行 AI 修改...");
    try {
      // 触发一次文档保存：通过插件 Api.Save（accept 命令）
      sendPluginCommand("accept", {});
    } catch (_) {
      // 出错时不再额外处理，直接进入 waitForBackendSave（可能没有 callback）
    }
    await waitForBackendSave(20000);
  }, [docDirty, fileId, sendPluginCommand, waitForBackendSave]);


  const updatePatchState = (id: string, uiState: PatchState) => {
    setPatches((prev) => prev.map((p) => (p.id === id ? { ...p, uiState } : p)));
  };

  const gotoNextOrDone = (messageWhenDone?: string) => {
    setCurrentPatchIndex((idx) => {
      const next = idx + 1;
      if (next >= patches.length) {
        setGlobalState("done");
        if (messageWhenDone) {
          setStatus(messageWhenDone);
        } else {
          setStatus("本轮 AI 建议已经全部 review 完成。");
        }
        return idx;
      }
      setGlobalState("reviewing");
      setStatus(`继续看第 ${next + 1} 条建议。`);
      return next;
    });
  };

  const handlePreviewPatch = useCallback(
    async (patch: ReviewPatch) => {
      if (!fileId) return;
      setGlobalState("previewing");
      setStatus(
        `正在预览：第 ${patch.slide_index + 1} 页，第 ${
          patch.shape_index + 1
        } 个文本框的修改效果（仅预览，不会真正保存）。`,
      );
      updatePatchState(patch.id, "previewing");

      try {
        // 通过 ONLYOFFICE 插件在当前会话内展示
        if (patch.action === "add_slide") {
          // 新增页的预览：当前插件侧不做“临时新增页”，仅提示用户 Accept 后会生成新页并自动刷新
          setStatus("预览：将生成一页新 PPT。点击 Accept 后会自动写回并刷新编辑器。");
        } else if (patch.action === "add_textbox") {
          sendPluginCommand("goto", { slideIndex: patch.slide_index });
          sendPluginCommand("addTextbox", {
            slideIndex: patch.slide_index,
            text: patch.after,
            position: patch.position || "after_title",
          });
        } else if (patch.action === "delete_textbox") {
          // 预览阶段不真正删除，先高亮提示
          sendPluginCommand("goto", { slideIndex: patch.slide_index });
          sendPluginCommand("highlight", {
            slideIndex: patch.slide_index,
            shapeIndex: patch.shape_index,
          });
        } else {
          // edit: GoTo + Highlight + SetText
          sendPluginCommand("preview", {
            slideIndex: patch.slide_index,
            shapeIndex: patch.shape_index,
            text: patch.after,
          });
        }
      } catch (e) {
        console.error(e);
        setStatus("预览时出错，但不影响你继续 Accept / Decline。");
        setGlobalState("reviewing");
        updatePatchState(patch.id, "pending");
        return;
      }

      // 预览结束后，让状态回到 reviewing，卡片保持当前高亮状态由前端 UI 表达
      setTimeout(() => {
        if (globalState === "previewing") {
          setGlobalState("reviewing");
          setStatus("预览结束，你可以选择接受或跳过这一条。");
        }
      }, 1000);
    },
    [fileId, globalState, sendPluginCommand],
  );

  const handleAcceptPatch = useCallback(
    async (patch: ReviewPatch) => {
      if (!fileId) return;
      await ensureSyncedBeforeApply();
      updatePatchState(patch.id, "accepted");
      setStatus("✅ 已应用这条修改，正在保存版本...");

      try {
        // 1) 先在 ONLYOFFICE 当前会话内立即展示修改
        if (patch.action === "add_slide") {
          // 新增页：插件侧先不做即时插页（避免 API 兼容问题），由后端写回后 refreshFile 呈现
          sendPluginCommand("goto", { slideIndex: Math.max(0, patch.slide_index - 1) });
        } else if (patch.action === "add_textbox") {
          sendPluginCommand("goto", { slideIndex: patch.slide_index });
          sendPluginCommand("addTextbox", {
            slideIndex: patch.slide_index,
            text: patch.after,
            position: patch.position || "after_title",
          });
        } else if (patch.action === "delete_textbox") {
          sendPluginCommand("goto", { slideIndex: patch.slide_index });
          sendPluginCommand("deleteTextbox", {
            slideIndex: patch.slide_index,
            shapeIndex: patch.shape_index,
          });
        } else {
          sendPluginCommand("setText", {
            slideIndex: patch.slide_index,
            shapeIndex: patch.shape_index,
            text: patch.after,
          });
          sendPluginCommand("goto", {
            slideIndex: patch.slide_index,
          });
        }
        // 可选：通知插件进行一次保存（Api.Save），保持与规范对齐
        sendPluginCommand("accept", {});
        // 2) 后端写回 PPTX（用于版本/下载）
        const res = await applyPatches(fileId, [patch]);
        setStatus(`✅ 已应用这条修改，版本号：${res.version_id}。1 秒后自动进入下一条。`);
        // 后端已将 PPTX 写回到 storage/outputs/{fileId}.pptx，
        // 这里通过新的 docKey + url query 来强制 ONLYOFFICE 重新加载新版本。
        if (editor && typeof editor.refreshFile === "function" && fileUrl) {
          const ts = Date.now();
          const hasQuery = fileUrl.includes("?");
          const refreshedUrl = `${fileUrl}${hasQuery ? "&" : "?"}v=${ts}`;
          const nextKey = `${fileId}_${ts}`;
          setDocKey(nextKey);
          try {
            editor.refreshFile({
              documentType: "slide",
              document: {
                fileType: "pptx",
                key: nextKey,
                title: fileName,
                url: refreshedUrl,
              },
              editorConfig: {
                mode: "edit",
                lang: "zh-CN",
              },
            });
          } catch (e) {
            console.error("refreshFile(config) failed", e);
          }
        }
      } catch (e) {
        console.error(e);
        setStatus("应用修改时出错，这条建议暂时保留为待处理状态。");
        updatePatchState(patch.id, "pending");
        return;
      }

      setTimeout(() => {
        gotoNextOrDone();
      }, 1000);
    },
    [fileId, patches.length, editor, fileUrl, fileName],
  );

  const handleDeclinePatch = useCallback(
    async (patch: ReviewPatch) => {
      updatePatchState(patch.id, "declined");
      setStatus("已跳过这一条建议，1 秒后自动进入下一条。");
      setTimeout(() => {
        gotoNextOrDone();
      }, 1000);
    },
    [patches.length],
  );

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        fontFamily:
          "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        fontSize: 13,
      }}
    >
      <header
        style={{
          padding: "8px 16px",
          borderBottom: "1px solid #e5e7eb",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          backgroundColor: "#f9fafb",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 600 }}>PPT AI 编辑（ONLYOFFICE 模式）</span>
          <span>当前文件：{fileName}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button
            type="button"
            onClick={handleDownloadPpt}
            disabled={!fileId}
            style={{
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid #e5e7eb",
              backgroundColor: fileId ? "#ffffff" : "#f3f4f6",
              fontSize: 12,
              cursor: fileId ? "pointer" : "not-allowed",
              color: fileId ? "#111827" : "#9ca3af",
            }}
            title={fileId ? "下载当前版本 PPTX" : "请先上传文件"}
          >
            下载 PPTX
          </button>
          <button
            type="button"
            onClick={handleClearStorage}
            style={{
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid #e5e7eb",
              backgroundColor: "#f9fafb",
              fontSize: 12,
              cursor: "pointer",
              color: "#6b7280",
            }}
          >
            清空缓存 storage
          </button>
          <label
            style={{
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid #d1d5db",
              cursor: "pointer",
              backgroundColor: "#fff",
            }}
          >
            选择 PPTX 上传
            <input
              type="file"
              accept=".pptx"
              style={{ display: "none" }}
              onChange={handleUpload}
            />
          </label>
        </div>
      </header>

      <div
        style={{
          padding: "6px 16px",
          borderBottom: "1px solid #e5e7eb",
          fontSize: 12,
          color: "#6b7280",
        }}
      >
        {status}
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {fileId && fileUrl ? (
            <OnlyOfficeEditor
              fileId={fileId}
              fileUrl={fileUrl}
              title={fileName}
              docKey={docKey || `${fileId}_v0`}
              onEditorReady={setEditor}
              onDirtyChange={setDocDirty}
            />
          ) : (
            <div style={{ padding: 24 }}>请先上传 PPTX 文件。</div>
          )}
        </div>
        {aiPanelOpen ? (
          <div style={{ position: "relative", height: "100%" }}>
            <button
              type="button"
              onClick={() => setAiPanelOpen(false)}
              title="收起 AI 面板"
              style={{
                position: "absolute",
                left: -14,
                top: 14,
                width: 28,
                height: 28,
                borderRadius: 999,
                border: "1px solid #e5e7eb",
                backgroundColor: "#ffffff",
                cursor: "pointer",
                boxShadow: "0 10px 30px rgba(15,23,42,0.08)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#111827",
                zIndex: 5,
              }}
            >
              ‹
            </button>
            <AiPanel
              fileId={fileId}
              patches={patches}
              globalState={globalState}
              currentPatchIndex={currentPatchIndex}
              onAsk={handleAsk}
              onPreview={handlePreviewPatch}
              onAccept={handleAcceptPatch}
              onDecline={handleDeclinePatch}
            />
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setAiPanelOpen(true)}
            title="展开 AI 面板"
            style={{
              position: "absolute",
              right: 10,
              top: 14,
              width: 28,
              height: 28,
              borderRadius: 999,
              border: "1px solid #e5e7eb",
              backgroundColor: "#ffffff",
              cursor: "pointer",
              boxShadow: "0 10px 30px rgba(15,23,42,0.08)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#111827",
              zIndex: 5,
            }}
          >
            ›
          </button>
        )}
      </div>
    </div>
  );
};

export default App;
