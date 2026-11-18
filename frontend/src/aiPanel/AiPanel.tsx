import React, { useMemo, useState } from "react";
import type { GlobalState, ReviewPatch } from "./types";

type Role = "user" | "assistant";

interface ChatMessage {
  id: string;
  role: Role;
  text: string;
}

interface AiPanelProps {
  fileId: string | null;
  patches: ReviewPatch[];
  globalState: GlobalState;
  currentPatchIndex: number;
  onAsk: (instruction: string) => Promise<string>;
  onPreview: (patch: ReviewPatch) => Promise<void>;
  onAccept: (patch: ReviewPatch) => Promise<void>;
  onDecline: (patch: ReviewPatch) => Promise<void>;
}

const PatchCard: React.FC<{
  patch: ReviewPatch;
  disabled: boolean;
  onPreview: () => void;
  onAccept: () => void;
  onDecline: () => void;
}> = ({ patch, disabled, onPreview, onAccept, onDecline }) => {
  const stateLabel =
    patch.uiState === "accepted"
      ? "已接受"
      : patch.uiState === "declined"
      ? "已跳过"
      : patch.uiState === "previewing"
      ? "预览中"
      : "待处理";

  const stateColor =
    patch.uiState === "accepted"
      ? "#22c55e"
      : patch.uiState === "declined"
      ? "#9ca3af"
      : patch.uiState === "previewing"
      ? "#f59e0b"
      : "#3b82f6";

  return (
    <div
      style={{
        marginTop: 8,
        padding: 12,
        borderRadius: 10,
        border: "1px solid #e5e7eb",
        backgroundColor: "#ffffff",
        boxShadow: "0 10px 30px rgba(15,23,42,0.08)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
        <div style={{ fontWeight: 600, color: "#111827" }}>
          第 {patch.slide_index + 1} 页 · 文本框 {patch.shape_index + 1}
        </div>
        <div style={{ fontSize: 11, color: stateColor }}>{stateLabel}</div>
      </div>
      <div style={{ fontSize: 12, color: "#6b7280" }}>
        {patch.description || "AI 建议对这里的表述做一点优化。"}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 8,
          fontSize: 12,
        }}
      >
        <div
          style={{
            padding: 8,
            borderRadius: 8,
            backgroundColor: "#f9fafb",
            border: "1px solid #e5e7eb",
          }}
        >
          <div style={{ fontSize: 11, color: "#9ca3af", marginBottom: 4 }}>Before</div>
          <div style={{ whiteSpace: "pre-wrap" }}>{patch.before || "（原文为空）"}</div>
        </div>
        <div
          style={{
            padding: 8,
            borderRadius: 8,
            backgroundColor: "rgba(22,163,74,0.04)",
            border: "1px solid rgba(22,163,74,0.25)",
          }}
        >
          <div style={{ fontSize: 11, color: "#16a34a", marginBottom: 4 }}>After</div>
          <div style={{ whiteSpace: "pre-wrap" }}>
            {patch.action === "delete_textbox" ? "（将删除此文本框）" : patch.after}
          </div>
        </div>
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 4,
        }}
      >
        <button
          type="button"
          disabled={disabled || patch.uiState !== "pending"}
          onClick={onPreview}
          style={{
            padding: "4px 10px",
            borderRadius: 999,
            border: "1px solid #d1d5db",
            backgroundColor: "#ffffff",
            color: "#111827",
            fontSize: 12,
            cursor:
              disabled || patch.uiState !== "pending" ? "not-allowed" : "pointer",
          }}
        >
          Preview
        </button>
        <button
          type="button"
          disabled={disabled || patch.uiState === "accepted"}
          onClick={onAccept}
          style={{
            padding: "4px 10px",
            borderRadius: 999,
            border: "none",
            backgroundColor:
              disabled || patch.uiState === "accepted" ? "#9ca3af" : "#16a34a",
            color: "#ffffff",
            fontSize: 12,
            cursor:
              disabled || patch.uiState === "accepted" ? "not-allowed" : "pointer",
          }}
        >
          Accept
        </button>
        <button
          type="button"
          disabled={disabled || patch.uiState === "declined"}
          onClick={onDecline}
          style={{
            padding: "4px 10px",
            borderRadius: 999,
            border: "none",
            backgroundColor:
              disabled || patch.uiState === "declined" ? "#9ca3af" : "#e5e7eb",
            color: "#111827",
            fontSize: 12,
            cursor:
              disabled || patch.uiState === "declined" ? "not-allowed" : "pointer",
          }}
        >
          Decline
        </button>
      </div>
    </div>
  );
};

export const AiPanel: React.FC<AiPanelProps> = ({
  fileId,
  patches,
  globalState,
  currentPatchIndex,
  onAsk,
  onPreview,
  onAccept,
  onDecline,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const currentPatch = useMemo(() => {
    if (currentPatchIndex < 0 || currentPatchIndex >= patches.length) return null;
    return patches[currentPatchIndex];
  }, [patches, currentPatchIndex]);

  const send = async () => {
    const text = input.trim();
    if (!text || !fileId || sending) return;
    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      role: "user",
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);
    try {
      const reply = await onAsk(text);
      const aiMsg: ChatMessage = {
        id: `a_${Date.now()}`,
        role: "assistant",
        text: reply,
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (e: any) {
      const aiMsg: ChatMessage = {
        id: `a_${Date.now()}`,
        role: "assistant",
        text: `调用 AI 失败：${String(e?.message || e)}`,
      };
      setMessages((prev) => [...prev, aiMsg]);
    } finally {
      setSending(false);
    }
  };

  const disabled = !fileId || sending || globalState === "analyzing";

  return (
    <div
      style={{
        width: 360,
        borderLeft: "1px solid #e5e7eb",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        backgroundColor: "#f9fafb",
      }}
    >
      <div
        style={{
          padding: "10px 12px",
          borderBottom: "1px solid #e5e7eb",
          fontWeight: 600,
          fontSize: 13,
        }}
      >
        AI Assistant
      </div>

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "10px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {messages.length === 0 && (
          <div style={{ fontSize: 12, color: "#9ca3af" }}>
            这里可以像 Cursor 一样和 AI 对话，描述你想要的 PPT 修改。
          </div>
        )}
        {messages.map((m) => {
          const isUser = m.role === "user";
          return (
            <div
              key={m.id}
              style={{
                display: "flex",
                justifyContent: isUser ? "flex-end" : "flex-start",
              }}
            >
              <div
                style={{
                  maxWidth: "80%",
                  padding: "6px 10px",
                  borderRadius: 16,
                  borderBottomRightRadius: isUser ? 4 : 16,
                  borderBottomLeftRadius: isUser ? 16 : 4,
                  backgroundColor: isUser ? "#2563eb" : "#ffffff",
                  color: isUser ? "#ffffff" : "#111827",
                  fontSize: 13,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  boxShadow: isUser
                    ? "0 6px 16px rgba(37,99,235,0.35)"
                    : "0 4px 10px rgba(15,23,42,0.06)",
                }}
              >
                {m.text}
              </div>
            </div>
          );
        })}

        {currentPatch && (
          <PatchCard
            patch={currentPatch}
            disabled={disabled}
            onPreview={() => onPreview(currentPatch)}
            onAccept={() => onAccept(currentPatch)}
            onDecline={() => onDecline(currentPatch)}
          />
        )}
      </div>

      <div
        style={{
          borderTop: "1px solid #e5e7eb",
          padding: 10,
          backgroundColor: "#f9fafb",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <textarea
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="例如：帮我通盘优化逻辑结构、总结页和标题页的表达"
          style={{
            width: "100%",
            resize: "none",
            padding: 8,
            borderRadius: 8,
            border: "1px solid #d1d5db",
            fontSize: 13,
            backgroundColor: "#ffffff",
          }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
          }}
        >
          <button
            type="button"
            disabled={!fileId || !input.trim() || sending}
            onClick={send}
            style={{
              padding: "6px 14px",
              borderRadius: 999,
              border: "none",
              backgroundColor:
                !fileId || !input.trim() || sending ? "#9ca3af" : "#2563eb",
              color: "#fff",
              fontSize: 13,
              cursor:
                !fileId || !input.trim() || sending ? "not-allowed" : "pointer",
            }}
          >
            {sending ? "生成中..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
};


