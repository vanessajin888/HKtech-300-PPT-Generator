/* global Asc, Api */
(function () {
  const GREEN = { r: 34, g: 197, b: 94 }; // tailwind green-500

  function log(...args) {
    try {
      // eslint-disable-next-line no-console
      console.log("[AI Patch Bridge]", ...args);
    } catch (_) {}
  }

  function safeNum(v, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function normalizeText(input) {
    const s = String(input ?? "");
    // 统一换行符
    return s.replace(/\\r\\n/g, "\\n").replace(/\\r/g, "\\n");
  }

  function getShapeContent(shape) {
    try {
      if (!shape) return null;
      if (typeof shape.GetDocContent === "function") return shape.GetDocContent();
      if (typeof shape.GetContent === "function") return shape.GetContent();
      return null;
    } catch (_) {
      return null;
    }
  }

  function looksLikeBulletText(raw) {
    const t = normalizeText(raw).trim();
    if (!t) return false;
    // 用户明确写了 bullet 结构时才认为是 bullet（否则绝不自动做 bullet）
    if (/^(\s*[-*•·]\s+)/.test(t)) return true;
    if (/^\s*\d+[\.\、]\s+/.test(t)) return true;
    if (/\n\s*[-*•·]\s+/.test(t)) return true;
    if (/\n\s*\d+[\.\、]\s+/.test(t)) return true;
    return false;
  }

  function isMostlyCJK(s) {
    const text = normalizeText(s);
    if (!text) return true;
    let cjk = 0, latin = 0;
    for (const ch of Array.from(text)) {
      if (/[\u4e00-\u9fff]/.test(ch)) cjk++;
      else if (/[A-Za-z]/.test(ch)) latin++;
    }
    return cjk >= latin;
  }

  // 自动换行优化（顺序：\\n → 。！？ → ，； → 空格 → 长度）
  // 长度参考：中文 24 左右；英文 40 左右；并随正文 textbox 宽度做轻微缩放
  function formatTextForSlideBody(raw, opts) {
    const text = normalizeText(raw).trim();
    if (!text) return "";
    if (text.includes("\\n")) return text;

    // 若用户文本本身就是 bullet（但没有显式换行），这里不做“自动 bullet”，只做自然断行
    // （保持“用户没提到 bullet 就不要做”的原则）

    const widthRatio = Math.max(0.6, Math.min(1.4, Number(opts?.widthRatio ?? 1)));
    const preferCjk = typeof opts?.preferCjk === "boolean" ? opts.preferCjk : isMostlyCJK(text);
    const baseLimit = preferCjk ? 24 : 40;
    const limit = Math.max(12, Math.round(baseLimit * widthRatio));

    const strongBreak = new Set(["。", "！", "？"]);
    const midBreak = new Set(["，", "；", ",", ";", ":", "："]);

    const parts = [];
    let buf = "";
    const chars = Array.from(text);
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      buf += ch;
      const len = buf.length;

      // 1) 强断句：。！？
      if (strongBreak.has(ch) && len >= Math.max(8, Math.floor(limit * 0.4))) {
        parts.push(buf.trim());
        buf = "";
        continue;
      }
      // 2) 次断句：，；
      if (midBreak.has(ch) && len >= Math.max(10, Math.floor(limit * 0.5))) {
        parts.push(buf.trim());
        buf = "";
        continue;
      }
      // 3) 空格
      if (ch === " " && len >= Math.max(10, Math.floor(limit * 0.55))) {
        parts.push(buf.trim());
        buf = "";
        continue;
      }
      // 4) 长度硬切
      if (len >= limit) {
        parts.push(buf.trim());
        buf = "";
        continue;
      }
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts.join("\\n");
  }

  function extractTextStyleFromShape(shape) {
    try {
      const dc = shape && (shape.GetDocContent ? shape.GetDocContent() : (shape.GetContent ? shape.GetContent() : null));
      if (!dc || !dc.GetElement) return { textPr: null, paraPr: null };
      const p0 = dc.GetElement(0);
      if (!p0) return { textPr: null, paraPr: null };

      let paraPr = null;
      try {
        if (typeof p0.GetParaPr === "function") paraPr = p0.GetParaPr();
      } catch (_) {}

      // 若第一段没有 ParaPr，尝试从后续段落里捞一个（更容易拿到正文的项目符号/缩进）
      if (!paraPr) {
        try {
          for (let i = 1; i < 6; i++) {
            const pi = dc.GetElement(i);
            if (!pi) break;
            if (typeof pi.GetParaPr === "function") {
              const pp = pi.GetParaPr();
              if (pp) {
                paraPr = pp;
                break;
              }
            }
          }
        } catch (_) {}
      }

      if (!p0.GetElement) return { textPr: null, paraPr };
      const r0 = p0.GetElement(0);
      if (!r0 || typeof r0.GetTextPr !== "function") return { textPr: null, paraPr };
      const textPr = r0.GetTextPr();
      return { textPr: textPr || null, paraPr: paraPr || null };
    } catch (_) {
      return { textPr: null, paraPr: null };
    }
  }

  function buildTextPrFromRef(refTextPr) {
    const textPr = Api.CreateTextPr ? Api.CreateTextPr() : null;
    if (!textPr || !refTextPr) return textPr;
    try {
      // 字体
      if (refTextPr.GetFontFamily && textPr.SetFontFamily) {
        const ff = refTextPr.GetFontFamily();
        if (ff) textPr.SetFontFamily(ff);
      }
      // 字号
      if (refTextPr.GetFontSize && textPr.SetFontSize) {
        const fs = refTextPr.GetFontSize();
        if (fs) textPr.SetFontSize(fs);
      }
      // 加粗/斜体/下划线
      if (refTextPr.GetBold && textPr.SetBold) textPr.SetBold(!!refTextPr.GetBold());
      if (refTextPr.GetItalic && textPr.SetItalic) textPr.SetItalic(!!refTextPr.GetItalic());
      if (refTextPr.GetUnderline && textPr.SetUnderline) textPr.SetUnderline(!!refTextPr.GetUnderline());
      // 字色
      if (refTextPr.GetColor && textPr.SetColor) {
        const c = refTextPr.GetColor();
        if (c) textPr.SetColor(c);
      }
    } catch (_) {}
    return textPr;
  }

  function getFirstRunTextPrFromShape(shape) {
    try {
      const dc = shape && (shape.GetDocContent ? shape.GetDocContent() : (shape.GetContent ? shape.GetContent() : null));
      if (!dc || !dc.GetElement) return null;
      const p0 = dc.GetElement(0);
      if (!p0 || !p0.GetElement) return null;
      const r0 = p0.GetElement(0);
      if (!r0) return null;
      if (typeof r0.GetTextPr === "function") return r0.GetTextPr();
      return null;
    } catch (_) {
      return null;
    }
  }

  function getShapeTextLength(shape) {
    try {
      const dc = shape && (shape.GetDocContent ? shape.GetDocContent() : (shape.GetContent ? shape.GetContent() : null));
      if (!dc || !dc.GetElement) return 0;
      const p0 = dc.GetElement(0);
      if (!p0 || typeof p0.GetText !== "function") return 0;
      const t = p0.GetText();
      return String(t ?? "").length;
    } catch (_) {
      return 0;
    }
  }

  // 优先寻找正文 textbox：最大面积 + 中部区域 + 文字最多
  function pickBestBodyTextbox(shapes, slideW, slideH) {
    if (!shapes || !shapes.length || !slideW || !slideH) return null;
    let best = null;
    let bestScore = -1;
    for (let i = 0; i < shapes.length; i++) {
      const shp = shapes[i];
      try {
        const x = shp.GetX ? shp.GetX() : 0;
        const y = shp.GetY ? shp.GetY() : 0;
        const w = shp.GetWidth ? shp.GetWidth() : 0;
        const h = shp.GetHeight ? shp.GetHeight() : 0;
        if (!w || !h) continue;

        const area = w * h;
        if (area < (slideW * slideH) * 0.02) continue; // 太小过滤

        // 中部区域加分（中心越接近 slide 中线越好）
        const cy = y + h / 2;
        const midDist = Math.abs(cy - slideH * 0.5) / slideH;
        const midScore = 1 - Math.min(1, midDist * 2);

        const textLen = getShapeTextLength(shp);
        const score =
          area * (0.7 + 0.3 * midScore) +
          Math.min(4000, textLen * 40);

        if (score > bestScore) {
          bestScore = score;
          best = { shape: shp, x, y, w, h };
        }
      } catch (_) {}
    }
    return best;
  }

  // 标题样式来源：顶部区域的大文本框（面积优先，其次文字量）
  function pickBestTitleTextbox(shapes, slideW, slideH) {
    if (!shapes || !shapes.length || !slideW || !slideH) return null;
    let best = null;
    let bestScore = -1;
    for (let i = 0; i < shapes.length; i++) {
      const shp = shapes[i];
      try {
        const x = shp.GetX ? shp.GetX() : 0;
        const y = shp.GetY ? shp.GetY() : 0;
        const w = shp.GetWidth ? shp.GetWidth() : 0;
        const h = shp.GetHeight ? shp.GetHeight() : 0;
        if (!w || !h) continue;
        // 顶部 35% 视为标题区
        if (y > slideH * 0.35) continue;
        const area = w * h;
        if (area < (slideW * slideH) * 0.01) continue;
        const textLen = getShapeTextLength(shp);
        const score = area + Math.min(2000, textLen * 30);
        if (score > bestScore) {
          bestScore = score;
          best = { shape: shp, x, y, w, h };
        }
      } catch (_) {}
    }
    return best;
  }

  function countLines(text) {
    const t = normalizeText(text);
    if (!t) return 1;
    return t.split("\\n").length;
  }

  function getFontSizeFromTextPr(refTextPr, fallbackPt) {
    try {
      if (refTextPr && refTextPr.GetFontSize) {
        const fs = refTextPr.GetFontSize();
        const n = Number(fs);
        if (Number.isFinite(n) && n > 0) return n;
      }
    } catch (_) {}
    return fallbackPt;
  }

  function estimateHeightEmu(lines, fontSizePt) {
    const ptToEmu = 12700;
    const lineH = fontSizePt * 1.15 * ptToEmu;
    return Math.max(1, lines) * lineH * 1.4;
  }

  function rectsOverlap(a, b, padding) {
    const pad = Number.isFinite(Number(padding)) ? Number(padding) : 0;
    return !(
      a.x + a.w + pad <= b.x ||
      b.x + b.w + pad <= a.x ||
      a.y + a.h + pad <= b.y ||
      b.y + b.h + pad <= a.y
    );
  }

  function collectShapeRects(shapes) {
    const rects = [];
    if (!shapes || !shapes.length) return rects;
    for (let i = 0; i < shapes.length; i++) {
      const shp = shapes[i];
      try {
        const x = shp.GetX ? shp.GetX() : 0;
        const y = shp.GetY ? shp.GetY() : 0;
        const w = shp.GetWidth ? shp.GetWidth() : 0;
        const h = shp.GetHeight ? shp.GetHeight() : 0;
        if (!w || !h) continue;
        rects.push({ x, y, w, h });
      } catch (_) {}
    }
    return rects;
  }

  function xRangesOverlap(ax, aw, bx, bw, padding) {
    const pad = Number.isFinite(Number(padding)) ? Number(padding) : 0;
    return !(ax + aw + pad <= bx || bx + bw + pad <= ax);
  }

  // 同一列堆叠：找与模板列横向重叠的 shapes 的最低 bottom
  function findColumnBottomY(rects, colX, colW, startY, padding) {
    const pad = Number.isFinite(Number(padding)) ? Number(padding) : 0;
    let bottom = startY;
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      if (!xRangesOverlap(colX, colW, r.x, r.w, pad)) continue;
      const b = r.y + r.h;
      if (b > bottom) bottom = b;
    }
    return bottom;
  }

  // 给定候选位置，自动避让现有 shapes，尽量“像人工排版”
  function findNonOverlappingY(candidate, rects, slideH, padding) {
    let y = candidate;
    const maxIter = 18;
    const pad = Number.isFinite(Number(padding)) ? Number(padding) : 0;
    for (let iter = 0; iter < maxIter; iter++) {
      const me = { x: 0, y, w: 1, h: 1 };
      // 这里只做 y 方向避让，x/w/h 在调用方补全后再校验
      // 调用方会用同一个 y 去做完整 overlap 判断
      if (y < 0) y = 0;
      if (slideH && y > slideH * 0.95) y = slideH * 0.95;
      // 简单松弛：如果 y 落在某个矩形内部附近，就往下挪
      let bumped = false;
      for (let i = 0; i < rects.length; i++) {
        const r = rects[i];
        if (y + pad >= r.y && y <= r.y + r.h + pad) {
          y = r.y + r.h + pad;
          bumped = true;
        }
      }
      if (!bumped) return y;
    }
    return y;
  }

  // 写入方式：一个 paragraph，多行用 AddLineBreak（\\n 在部分版本不会换行）
  // 可选：overrideColor（用于“颜色从标题继承”）
  function applyTextToShape(
    shape,
    rawText,
    refTextPr,
    refParaPr,
    wrapOpts,
    overrideFontSizePt,
    overrideColor,
  ) {
    const content = getShapeContent(shape);
    if (!content || !content.RemoveAllElements) return;

    const formatted = formatTextForSlideBody(rawText, wrapOpts);
    content.RemoveAllElements();

    const textPr = buildTextPrFromRef(refTextPr);
    try {
      if (
        typeof overrideFontSizePt === "number" &&
        Number.isFinite(overrideFontSizePt) &&
        overrideFontSizePt > 0 &&
        textPr &&
        typeof textPr.SetFontSize === "function"
      ) {
        textPr.SetFontSize(overrideFontSizePt);
      }
    } catch (_) {}
    try {
      if (overrideColor && textPr && typeof textPr.SetColor === "function") {
        textPr.SetColor(overrideColor);
      }
    } catch (_) {}

    const pgh = Api.CreateParagraph();
    try {
      if (refParaPr && typeof pgh.SetParaPr === "function") {
        pgh.SetParaPr(refParaPr);
      }
    } catch (_) {}

    const run = Api.CreateRun ? Api.CreateRun() : null;
    const lines = normalizeText(formatted).split("\\n");
    if (run) {
      try {
        if (textPr && run.SetTextPr) run.SetTextPr(textPr);
      } catch (_) {}
      for (let i = 0; i < lines.length; i++) {
        run.AddText(lines[i] || "");
        if (i < lines.length - 1 && typeof run.AddLineBreak === "function") {
          run.AddLineBreak();
        }
      }
      pgh.AddElement(run);
    } else {
      // 兜底：旧实现（可能无法换行）
      pgh.AddText(normalizeText(formatted));
    }
    content.Push(pgh);
  }

  // --- Core Actions ---------------------------------------------------------

  function actionGoTo(slideIndexRaw) {
    const slideIndex = safeNum(slideIndexRaw, 0);
    Asc.scope.__goto = { slideIndex };
    Asc.plugin.callCommand(function () {
      try {
        const p = Api.GetPresentation();
        const s = p.GetSlideByIndex(Asc.scope.__goto.slideIndex);
        if (!s) {
          log("GoToSlide: slide not found", Asc.scope.__goto.slideIndex);
          return;
        }
        s.Select();
      } catch (e) {
        log("GoToSlide error", e);
      }
    });
  }

  function actionSetText(slideIndexRaw, shapeIndexRaw, textRaw) {
    const slideIndex = safeNum(slideIndexRaw, 0);
    const shapeIndex = safeNum(shapeIndexRaw, 0);
    const text = String(textRaw ?? "");

    Asc.scope.__setText = { slideIndex, shapeIndex, text };
    Asc.plugin.callCommand(function () {
      try {
        log("SET TEXT CALLED", Asc.scope.__setText);
        const p = Api.GetPresentation();
        const s = p.GetSlideByIndex(Asc.scope.__setText.slideIndex);
        if (!s) {
          log("SetText: slide not found", Asc.scope.__setText.slideIndex);
          return;
        }
        const shapes = s.GetAllShapes();
        const shape =
          shapes && shapes.length > Asc.scope.__setText.shapeIndex
            ? shapes[Asc.scope.__setText.shapeIndex]
            : null;
        if (!shape) {
          log("SetText: shape not found", Asc.scope.__setText.slideIndex, Asc.scope.__setText.shapeIndex);
          return;
        }

        try {
          shape.Select();
        } catch (_) {}
        // 使用通用写入逻辑：一个 paragraph + 多行用 AddLineBreak
        const { textPr: refTextPr, paraPr: refParaPr } = extractTextStyleFromShape(shape);
        const slideW = p.GetWidth ? p.GetWidth() : 0;
        const w = shape.GetWidth ? shape.GetWidth() : 0;
        const widthRatio = slideW ? w / (slideW * 0.7) : 1;
        applyTextToShape(shape, Asc.scope.__setText.text, refTextPr, refParaPr, { widthRatio });
      } catch (e) {
        log("SetText error", e);
      }
    });
  }

  function actionHighlight(slideIndexRaw, shapeIndexRaw) {
    const slideIndex = safeNum(slideIndexRaw, 0);
    const shapeIndex = safeNum(shapeIndexRaw, 0);

    Asc.scope.__highlight = { slideIndex, shapeIndex, outline: true };
    Asc.plugin.callCommand(function () {
      try {
        const p = Api.GetPresentation();
        const s = p.GetSlideByIndex(Asc.scope.__highlight.slideIndex);
        if (!s) {
          log("Highlight: slide not found", Asc.scope.__highlight.slideIndex);
          return;
        }
        const shapes = s.GetAllShapes();
        const shape =
          shapes && shapes.length > Asc.scope.__highlight.shapeIndex
            ? shapes[Asc.scope.__highlight.shapeIndex]
            : null;
        if (!shape || !shape.SetLine) {
          log("Highlight: shape not found or no SetLine", Asc.scope.__highlight.slideIndex, Asc.scope.__highlight.shapeIndex);
          return;
        }
        const stroke = Api.CreateStroke(
          36000,
          Api.CreateSolidFill(Api.CreateRGBColor(GREEN.r, GREEN.g, GREEN.b)),
        );
        shape.SetLine(stroke);
      } catch (e) {
        log("Highlight error", e);
      }
    });

    // flash once: remove outline after a short delay
    setTimeout(function () {
      Asc.scope.__highlightOff = { slideIndex, shapeIndex, outline: false };
      Asc.plugin.callCommand(function () {
        try {
          const p = Api.GetPresentation();
          const s = p.GetSlideByIndex(Asc.scope.__highlightOff.slideIndex);
          if (!s) return;
          const shapes = s.GetAllShapes();
          const shape =
            shapes && shapes.length > Asc.scope.__highlightOff.shapeIndex
              ? shapes[Asc.scope.__highlightOff.shapeIndex]
              : null;
          if (!shape || !shape.SetLine) return;
          const stroke = Api.CreateStroke(
            0,
            Api.CreateSolidFill(Api.CreateRGBColor(0, 0, 0)),
          );
          shape.SetLine(stroke);
        } catch (e) {
          log("HighlightOff error", e);
        }
      });
    }, 800);
  }

  function actionAcceptSave() {
    Asc.plugin.callCommand(function () {
      try {
        Api.Save();
      } catch (e) {
        log("Accept/Save error", e);
      }
    });
  }

  function postToHost(payload) {
    try {
      // 宿主页面与编辑器 iframe 同源时可用；demo 场景用 "*" 即可
      window.parent && window.parent.postMessage(
        JSON.stringify({ type: "aiPatchBridge:event", payload }),
        "*",
      );
    } catch (e) {
      log("postToHost error", e);
    }
  }

  function actionReportCurrentSlide() {
    Asc.plugin.callCommand(function () {
      try {
        const p = Api.GetPresentation();
        const idx =
          p && typeof p.GetCurSlideIndex === "function" ? p.GetCurSlideIndex() : 0;
        Asc.scope.__curSlide = { idx: Number(idx) };
      } catch (e) {
        Asc.scope.__curSlide = { idx: 0 };
      }
    }, function () {
      try {
        const idx = (Asc.scope.__curSlide && Asc.scope.__curSlide.idx) || 0;
        postToHost({ event: "currentSlide", slideIndex: idx });
      } catch (e) {
        // ignore
      }
    });
  }

  function actionAddTextbox(slideIndexRaw, textRaw, positionRaw) {
    const slideIndex = safeNum(slideIndexRaw, 0);
    let text = String(textRaw ?? "");
    const position = String(positionRaw ?? "after_title");
    Asc.scope.__addTextbox = { slideIndex, text, position };

    Asc.plugin.callCommand(function () {
      try {
        log("ADD TEXTBOX CALLED", Asc.scope.__addTextbox);
        const p = Api.GetPresentation();
        const s = p.GetSlideByIndex(Asc.scope.__addTextbox.slideIndex);
        if (!s) {
          log("AddTextbox: slide not found", Asc.scope.__addTextbox.slideIndex);
          return;
        }

        const slideW = p.GetWidth ? p.GetWidth() : 0;
        const slideH = p.GetHeight ? p.GetHeight() : 0;

        const shapes = s.GetAllShapes ? s.GetAllShapes() : [];
        // 1) 新建 textbox 必须继承样式：
        //    - 正文：字体/字号/段落来自“最佳正文 textbox”
        //    - 颜色：强制使用白色（避免主题/模板差异导致看起来是黑色）
        const bestBody = pickBestBodyTextbox(shapes, slideW, slideH);
        let templateX = slideW ? slideW * 0.1 : 0;
        let templateY = slideH ? slideH * 0.3 : 0;
        let templateW = slideW ? slideW * 0.8 : 600;
        let templateH = slideH ? slideH * 0.25 : 200;
        let refTextPr = null;
        let refParaPr = null;
        let titleColor = null;
        if (bestBody && bestBody.shape) {
          templateX = bestBody.x;
          templateY = bestBody.y;
          templateW = bestBody.w;
          templateH = bestBody.h;
          const ref = extractTextStyleFromShape(bestBody.shape);
          refTextPr = ref.textPr;
          refParaPr = ref.paraPr;
        }
        // 颜色：直接使用纯白色，避免模板/主题差异导致正文变黑
        if (Api.CreateRGBColor) {
          try {
            titleColor = Api.CreateRGBColor(255, 255, 255);
          } catch (_) {
            titleColor = null;
          }
        }
        const widthRatio = slideW ? (templateW / (slideW * 0.7)) : 1;
        // 若宿主未提供文本，则使用文档级别自增占位文本“新文本框N”
        if (!Asc.scope.__addTextbox.text || String(Asc.scope.__addTextbox.text).trim() === "") {
          if (typeof Asc.scope.__textboxCounter !== "number") {
            Asc.scope.__textboxCounter = 0;
          }
          Asc.scope.__textboxCounter += 1;
          Asc.scope.__addTextbox.text = "新文本框" + String(Asc.scope.__textboxCounter);
        }
        // 2) 自动换行优化（不自动做 bullet；仅当用户文本自带 bullet 标记时原样保留）
        const rawText = String(Asc.scope.__addTextbox.text ?? "");
        const formatted = formatTextForSlideBody(rawText, { widthRatio });
        const lines = countLines(formatted);

        // 3) textbox 尺寸自适应：width = 正文 textbox 宽度；height = 行数 × 行高 × 1.4
        let fontSizePt = getFontSizeFromTextPr(refTextPr, 20);
        let neededH = estimateHeightEmu(lines, fontSizePt);

        // 5) 自动 fit：如可能 overflow（高度过高），fontSize-- 直到适配或最小值
        const minFont = 10;
        const maxH = slideH ? Math.min(templateH * 1.25, slideH * 0.75) : neededH;
        while (neededH > maxH && fontSizePt > minFont) {
          fontSizePt -= 1;
          neededH = estimateHeightEmu(lines, fontSizePt);
        }

        const x = templateX;
        const w = templateW;
        const h = Math.max(neededH, slideH ? slideH * 0.08 : neededH);
        // 位置：像人工排版一样“同一列向下堆叠”，并自动避让避免遮挡
        const margin = slideH ? slideH * 0.03 : 20000;
        const rects = collectShapeRects(shapes);
        // 默认放在“正文列”已有内容的最底部下方（避免多次新增互相覆盖）
        let y = findColumnBottomY(rects, x, w, templateY + templateH, margin) + margin;
        if (slideH && y + h > slideH * 0.92) {
          // 如果放不下，尝试放到正文块上方
          const up = Math.max(0, templateY - h - margin);
          if (up + h <= slideH * 0.92) y = up;
        }
        // 若模板太靠上（可能是标题），则回退到标题下方区域
        if (slideH && templateY < slideH * 0.18) {
          y = slideH * 0.22;
        }
        // 避让：按 y 方向先粗避让，再做矩形 overlap 精校验
        y = findNonOverlappingY(y, rects, slideH, margin);
        // 精校验：如果仍有重叠，就继续往下挪
        const pad = margin * 0.6;
        for (let iter = 0; iter < 24; iter++) {
          const me = { x, y, w, h };
          let hit = false;
          for (let i = 0; i < rects.length; i++) {
            if (rectsOverlap(me, rects[i], pad)) {
              y = rects[i].y + rects[i].h + pad;
              hit = true;
            }
          }
          if (!hit) break;
        }
        if (slideH && y + h > slideH * 0.95) {
          y = Math.max(0, slideH * 0.95 - h);
        }

        // 创建一个矩形 shape 作为 textbox
        const fill = Api.CreateNoFill ? Api.CreateNoFill() : Api.CreateSolidFill(Api.CreateRGBColor(255, 255, 255));
        // 新建正文 textbox 默认不做绿色描边（绿色只用于 highlight/preview），更像模板本身
        const stroke = Api.CreateStroke(
          0,
          Api.CreateSolidFill(Api.CreateRGBColor(0, 0, 0)),
        );
        const shp = Api.CreateShape("rect", w, h, fill, stroke);
        if (shp.SetPosition) shp.SetPosition(x, y);
        try {
          if (typeof shp.SetWidth === "function") shp.SetWidth(w);
          if (typeof shp.SetHeight === "function") shp.SetHeight(h);
        } catch (_) {}

        const dc = getShapeContent(shp);
        if (dc && dc.RemoveAllElements) {
          try {
            // 正常写入：一个 paragraph，多行用 AddLineBreak
            applyTextToShape(shp, rawText, refTextPr, refParaPr, { widthRatio }, fontSizePt, null);
          } catch (e) {
            log("AddTextbox write text error", e);
          }
        }

        if (s.AddObject) s.AddObject(shp);
        try { shp.Select && shp.Select(); } catch (_) {}
      } catch (e) {
        log("AddTextbox error", e);
      }
    });
  }

  function actionDeleteTextbox(slideIndexRaw, shapeIndexRaw) {
    const slideIndex = safeNum(slideIndexRaw, 0);
    const shapeIndex = safeNum(shapeIndexRaw, 0);
    Asc.scope.__deleteTextbox = { slideIndex, shapeIndex };
    Asc.plugin.callCommand(function () {
      try {
        const p = Api.GetPresentation();
        const s = p.GetSlideByIndex(Asc.scope.__deleteTextbox.slideIndex);
        if (!s) {
          log("DeleteTextbox: slide not found", Asc.scope.__deleteTextbox.slideIndex);
          return;
        }
        // 官方 Slide API：RemoveObject(nPos, nCount)
        // 注意：nPos 是“对象位置索引”，在本 demo 中我们用 shapeIndex 来对齐对象序列。
        if (typeof s.RemoveObject === "function") {
          const ok = s.RemoveObject(Asc.scope.__deleteTextbox.shapeIndex, 1);
          if (!ok) {
            log("DeleteTextbox: RemoveObject returned false", Asc.scope.__deleteTextbox.slideIndex, Asc.scope.__deleteTextbox.shapeIndex);
          }
          return;
        }
        // 兜底：如果没有 RemoveObject，再尝试 shape.Delete（不同版本兼容）
        const shapes = s.GetAllShapes ? s.GetAllShapes() : [];
        const shape =
          shapes && shapes.length > Asc.scope.__deleteTextbox.shapeIndex
            ? shapes[Asc.scope.__deleteTextbox.shapeIndex]
            : null;
        if (shape && typeof shape.Delete === "function") {
          shape.Delete();
        } else {
          log("DeleteTextbox: no supported delete API", Asc.scope.__deleteTextbox.slideIndex, Asc.scope.__deleteTextbox.shapeIndex);
        }
      } catch (e) {
        log("DeleteTextbox error", e);
      }
    });
  }

  // --- Command Router -------------------------------------------------------

  function handleCommand(message) {
    if (!message || typeof message !== "object") return;
    if (message.type !== "command") return;

    const action = String(message.action || "").toLowerCase();
    const payload = message.payload || {};

    if (action === "goto") {
      actionGoTo(payload.slideIndex);
      return;
    }

    if (action === "settext") {
      actionSetText(payload.slideIndex, payload.shapeIndex, payload.text);
      return;
    }

    if (action === "highlight") {
      actionHighlight(payload.slideIndex, payload.shapeIndex);
      return;
    }

    if (action === "preview") {
      // Preview = GoTo + Highlight + SetText
      actionGoTo(payload.slideIndex);
      actionHighlight(payload.slideIndex, payload.shapeIndex);
      actionSetText(payload.slideIndex, payload.shapeIndex, payload.text);
      return;
    }

    if (action === "accept") {
      actionAcceptSave();
      return;
    }

    if (action === "addtextbox") {
      actionAddTextbox(payload.slideIndex, payload.text, payload.position);
      return;
    }

    if (action === "deletetextbox") {
      actionDeleteTextbox(payload.slideIndex, payload.shapeIndex);
      return;
    }

    if (action === "getcurrentslide") {
      actionReportCurrentSlide();
      return;
    }
  }

  window.Asc = window.Asc || {};
  window.Asc.plugin = window.Asc.plugin || {};

  window.Asc.plugin.init = function () {
    log("init");
    // 轻量轮询：记录用户手动切页后的当前页（每 600ms ）
    // 只上报变化，避免刷屏
    let last = -1;
    setInterval(function () {
      Asc.plugin.callCommand(function () {
        try {
          const p = Api.GetPresentation();
          const idx = p && p.GetCurSlideIndex ? p.GetCurSlideIndex() : 0;
          Asc.scope.__curSlidePoll = { idx: Number(idx) };
        } catch (e) {
          Asc.scope.__curSlidePoll = { idx: 0 };
        }
      }, function () {
        const idx = (Asc.scope.__curSlidePoll && Asc.scope.__curSlidePoll.idx) || 0;
        if (idx !== last) {
          last = idx;
          postToHost({ event: "currentSlide", slideIndex: idx });
        }
      });
    }, 600);
  };

  // Receive messages from host page via postMessage bridge
  window.Asc.plugin.onExternalPluginMessage = function (data) {
    try {
      let msg = data;
      if (typeof msg === "string") {
        try {
          msg = JSON.parse(msg);
        } catch (e) {
          log("onExternalPluginMessage JSON parse error", e);
          return;
        }
      }
      // 兼容宿主两种封装格式：
      // 1) { type: "command", action: "...", payload: {...} }
      // 2) { type: "onExternalPluginMessage", data: { type: "command", action: "...", payload: {...} } }
      if (msg && msg.type === "onExternalPluginMessage" && msg.data && msg.data.type === "command") {
        handleCommand(msg.data);
      } else {
        handleCommand(msg);
      }
    } catch (e) {
      log("handleCommand error", e);
    }
  };

  // For debugging: allow manual test from browser console inside plugin
  window.__AI_PATCH_BRIDGE__ = {
    handleCommand,
    actionGoTo,
    actionSetText,
    actionHighlight,
    actionAddTextbox,
    actionDeleteTextbox,
  };
})();

