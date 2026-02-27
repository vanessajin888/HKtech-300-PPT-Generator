import React, { useState } from "react";
import type { Suggestion } from "../api/client";

interface ChatRoundMeta {
  id: string;
  instruction: string;
  createdAt?: string;
  suggestionCount: number;
}

interface SuggestionListProps {
  fileId?: string;
  suggestions: Suggestion[];
  acceptedIds: string[];
  onGenerate: (instruction: string) => Promise<void>;
  onToggleAccept: (id: string) => void;
  onApply: () => Promise<void>;
  onJumpTo: (slideIndex: number, shapeId: string) => void;
  rounds: ChatRoundMeta[];
  activeRoundId?: string | null;
  onSelectRound: (roundId: string) => void;
  onAcceptAll: () => Promise<void>;
  onDeclineAll: () => void;
}

export const SuggestionList: React.FC<SuggestionListProps> = ({
  fileId,
  suggestions,
  acceptedIds,
  onGenerate,
  onToggleAccept,
  onApply,
  onJumpTo,
  rounds,
  activeRoundId,
  onSelectRound,
  onAcceptAll,
  onDeclineAll,
}) => {
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);

  const handleGenerate = async () => {
    if (!fileId || !instruction.trim()) return;
    setLoading(true);
    try {
      await onGenerate(instruction.trim());
    } finally {
      setLoading(false);
    }
  };

  const handleApply = async () => {
    if (!fileId || !acceptedIds.length) return;
    setApplying(true);
    try {
      await onApply();
    } finally {
      setApplying(false);
    }
  };

  const allAccepted = suggestions.length > 0 && acceptedIds.length === suggestions.length;

  const toggleAll = () => {
    if (!suggestions.length) return;
    if (allAccepted) {
      suggestions.forEach((s) => {
        if (acceptedIds.includes(s.id)) {
          onToggleAccept(s.id);
        }
      });
    } else {
      suggestions.forEach((s) => {
        if (!acceptedIds.includes(s.id)) {
          onToggleAccept(s.id);
        }
      });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 对话区域：类似 ChatGPT 的气泡式历史 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "8px 12px 4px",
          borderBottom: "1px solid #e5e7eb",
          backgroundColor: "#f9fafb",
          fontSize: 13,
        }}
      >
        {rounds.length === 0 ? (
          <div style={{ color: "#9ca3af", fontSize: 12 }}>
            还没有对话，先在上面输入一条指令吧。
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {rounds.map((r) => {
              const isActive = r.id === activeRoundId;
              return (
                <div
                  key={r.id}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                  }}
                >
                  {/* 用户气泡 */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "flex-end",
                    }}
                  >
                    <div
                      style={{
                        maxWidth: "80%",
                        backgroundColor: "#2563eb",
                        color: "#ffffff",
                        padding: "6px 10px",
                        borderRadius: 999,
                        borderBottomRightRadius: 4,
                        boxShadow: "0 6px 14px rgba(37,99,235,0.35)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        cursor: "pointer",
                      }}
                      onClick={() => onSelectRound(r.id)}
                    >
                      {r.instruction}
                    </div>
                  </div>

                  {/* 助手气泡 */}
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "flex-start",
                    }}
                  >
                    <div
                      onClick={() => onSelectRound(r.id)}
                      style={{
                        maxWidth: "82%",
                        backgroundColor: "#ffffff",
                        padding: "8px 10px",
                        borderRadius: 12,
                        borderBottomLeftRadius: 4,
                        border: isActive
                          ? "1px solid rgba(37,99,235,0.5)"
                          : "1px solid #e5e7eb",
                        boxShadow: isActive
                          ? "0 8px 18px rgba(37,99,235,0.18)"
                          : "0 4px 10px rgba(15,23,42,0.04)",
                        cursor: "pointer",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          marginBottom: 4,
                        }}
                      >
                        <span
                          style={{
                            fontWeight: 600,
                            color: "#111827",
                            fontSize: 12,
                          }}
                        >
                          AI · Cursor-like suggestions
                        </span>
                        <span
                          style={{
                            fontSize: 11,
                            color: "#9ca3af",
                          }}
                        >
                          {r.createdAt ?? ""}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#374151",
                          marginBottom: 2,
                        }}
                      >
                        为当前 PPT 生成了{" "}
                        <strong>{r.suggestionCount}</strong> 条建议。
                      </div>
                      <div style={{ fontSize: 11, color: "#6b7280" }}>
                        点击该气泡可在下方查看并勾选本轮建议。
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 输入区域放在最底部，更接近 ChatGPT 体验 */}
      <div
        style={{
          padding: 10,
          borderTop: "1px solid #e5e7eb",
          backgroundColor: "#f9fafb",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 12, color: "#4b5563" }}>
          Chat bot
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 4,
            gap: 8,
          }}
        >
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={onDeclineAll}
              disabled={!fileId || !suggestions.length}
              style={{
                padding: "4px 10px",
                borderRadius: 999,
                border: "1px solid #e5e7eb",
                backgroundColor: "#ffffff",
                color: "#6b7280",
                fontSize: 12,
                cursor:
                  !fileId || !suggestions.length ? "not-allowed" : "pointer",
              }}
            >
              Decline
            </button>
            <button
              type="button"
              onClick={() => void onAcceptAll()}
              disabled={!fileId || !suggestions.length || applying}
              style={{
                padding: "4px 12px",
                borderRadius: 999,
                border: "none",
                backgroundColor:
                  !fileId || !suggestions.length || applying ? "#9ca3af" : "#16a34a",
                color: "#ffffff",
                fontSize: 12,
                cursor:
                  !fileId || !suggestions.length || applying
                    ? "not-allowed"
                    : "pointer",
                boxShadow:
                  !fileId || !suggestions.length || applying
                    ? "none"
                    : "0 4px 12px rgba(22,163,74,0.45)",
              }}
            >
              {applying ? "Applying..." : "Accept"}
            </button>
          </div>
          <div style={{ fontSize: 11, color: "#9ca3af" }}>
            当前建议：{suggestions.length} 条
          </div>
        </div>

        <textarea
          rows={2}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="例如：把 2024 的数据更新到 2026，并把这一页的要点更简洁"
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
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            onClick={handleGenerate}
            disabled={!fileId || !instruction.trim() || loading}
            style={{
              padding: "6px 14px",
              borderRadius: 999,
              border: "none",
              backgroundColor:
                !fileId || !instruction.trim() || loading ? "#9ca3af" : "#2563eb",
              color: "#fff",
              fontSize: 13,
              cursor:
                !fileId || !instruction.trim() || loading ? "not-allowed" : "pointer",
              boxShadow:
                !fileId || !instruction.trim() || loading
                  ? "none"
                  : "0 6px 16px rgba(37,99,235,0.4)",
            }}
          >
            {loading ? "生成中..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
};

