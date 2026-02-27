import React from "react";
import type { SlideMeta, PptObject } from "../api/client";

interface PptViewerProps {
  slides: SlideMeta[];
  objects: PptObject[];
  currentSlideIndex: number;
  highlightedShapeIds: string[];
  appliedShapeIds: string[];
  selectedShapeId?: string | null;
  onChangeSlide: (index: number) => void;
}

export const PptViewer: React.FC<PptViewerProps> = ({
  slides,
  objects,
  currentSlideIndex,
  highlightedShapeIds,
  appliedShapeIds,
  selectedShapeId,
  onChangeSlide,
}) => {
  if (!slides.length) {
    return <div style={{ padding: 16 }}>请先上传 PPT 文件。</div>;
  }

  const currentSlide = slides.find((s) => s.slide_index === currentSlideIndex) ?? slides[0];
  const imgObjects = objects.filter((o) => o.slide_index === currentSlide.slide_index);

  // 统一缩放比例，让整个 PPT 页面在预览区内按比例缩放显示
  const targetMaxWidth = 900;
  const scale =
    currentSlide.image_w > targetMaxWidth
      ? targetMaxWidth / currentSlide.image_w
      : 1;

  const handlePrev = () => {
    const idx = Math.max(0, currentSlideIndex - 1);
    onChangeSlide(idx);
  };

  const handleNext = () => {
    const idx = Math.min(slides.length - 1, currentSlideIndex + 1);
    onChangeSlide(idx);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid #e5e7eb",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <button onClick={handlePrev} disabled={currentSlideIndex === 0}>
            上一页
          </button>
          <button
            onClick={handleNext}
            disabled={currentSlideIndex >= slides.length - 1}
            style={{ marginLeft: 8 }}
          >
            下一页
          </button>
        </div>
        <div>
          第 {currentSlide.slide_index + 1} / {slides.length} 页
        </div>
      </div>

      <div
        style={{
          flex: 1,
          overflow: "auto",
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
          padding: 16,
          backgroundColor: "#f9fafb",
        }}
      >
        <div
          style={{
            position: "relative",
            width: currentSlide.image_w * scale,
            height: currentSlide.image_h * scale,
            boxShadow: "0 0 8px rgba(0,0,0,0.1)",
            backgroundColor: "#fff",
          }}
        >
          <img
            src={currentSlide.image_url}
            alt={`Slide ${currentSlide.slide_index + 1}`}
            style={{
              width: currentSlide.image_w * scale,
              height: currentSlide.image_h * scale,
              objectFit: "cover",
            }}
          />

          {imgObjects.map((obj) => {
            const bbox = obj.bbox_px;
            const isHighlighted = highlightedShapeIds.includes(obj.shape_id);
            if (!isHighlighted) return null;

            const isApplied = appliedShapeIds.includes(obj.shape_id);
            const isSelected = selectedShapeId === obj.shape_id;

            const borderColor = isApplied ? "#16a34a" : "#eab308"; // green / amber
            // 细边框 + 轻微底色
            const backgroundColor = isApplied
              ? "rgba(22, 163, 74, 0.05)"
              : "rgba(234, 179, 8, 0.02)";

            const boxShadow = isSelected ? "0 0 0 2px rgba(59,130,246,0.8)" : "none";

            return (
              <div
                key={obj.shape_id}
                style={{
                  position: "absolute",
                  left: bbox.x * scale,
                  top: bbox.y * scale,
                  width: bbox.w * scale,
                  height: bbox.h * scale,
                  boxSizing: "border-box",
                  pointerEvents: "none",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    border: `2px solid ${borderColor}`,
                    backgroundColor,
                    boxShadow,
                    transition: "box-shadow 0.2s ease-out",
                  }}
                />
                {/* 额外渲染一份文字，方便 Demo 观察 */}
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    padding: 4,
                    fontSize: obj.font_size_pt
                      ? Math.max(10, obj.font_size_pt * scale)
                      : 14 * scale,
                    color: "#111827",
                    display: "flex",
                    alignItems: "center",
                    justifyContent:
                      obj.alignment === "CENTER"
                        ? "center"
                        : obj.alignment === "RIGHT"
                        ? "flex-end"
                        : "flex-start",
                    overflow: "hidden",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.2,
                  }}
                >
                  {obj.text}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

