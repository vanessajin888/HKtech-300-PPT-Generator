from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Tuple

from pptx import Presentation
from pptx.enum.text import PP_ALIGN


def _normalize_text_for_dedup(t: str) -> str:
    """用于同页去重：统一空白与换行后比较。"""
    return " ".join((t or "").strip().split())


def extract_text_objects(ppt_path: Path) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
    """
    Parse PPTX and extract text-box-like shapes with EMU bounding boxes.
    同页内相同内容的形状只保留一个（靠近中心的）；其余视为「同内容小字」并返回其索引以便从 PPT 中删除。
    返回 (objects, removed_shape_indices)，removed_shape_indices 为 [(slide_index, shape_index), ...]。
    """
    ppt_path = Path(ppt_path)
    prs = Presentation(str(ppt_path))

    slide_w = prs.slide_width
    slide_h = prs.slide_height

    objects: List[Dict[str, Any]] = []
    removed_shape_indices: List[Tuple[int, int]] = []

    for slide_idx, slide in enumerate(prs.slides):
        slide_objs: List[Dict[str, Any]] = []
        for shape_idx, shape in enumerate(slide.shapes):
            if not hasattr(shape, "has_text_frame") or not shape.has_text_frame:
                continue

            text = shape.text or ""
            if not text.strip():
                continue

            left = int(shape.left)
            top = int(shape.top)
            width = int(shape.width)
            height = int(shape.height)
            slide_area = int(slide_w) * int(slide_h)
            if width * height < slide_area * 0.0001:
                continue

            alignment_name: str = "LEFT"
            font_size_pt: float | None = None
            try:
                if shape.text_frame.paragraphs:
                    para = shape.text_frame.paragraphs[0]
                    if para.alignment is not None:
                        if isinstance(para.alignment, PP_ALIGN):
                            alignment_name = para.alignment.name
                        else:
                            alignment_name = str(para.alignment)
                    if para.runs:
                        run_font = para.runs[0].font
                        if run_font.size is not None:
                            font_size_pt = float(run_font.size.pt)
            except Exception:
                pass

            # 使用 shape.shape_id（XML 内部稳定 id），避免删除/重排导致索引漂移
            # 旧格式为 s{slide_idx}_sh{shape_idx}
            shape_xml_id = getattr(shape, "shape_id", None)
            if shape_xml_id is None:
                shape_id = f"s{slide_idx}_sh{shape_idx}"
            else:
                shape_id = f"s{slide_idx}_id{int(shape_xml_id)}"
            area_emu = width * height
            cx = left + width // 2
            cy = top + height // 2
            center_dist_sq = (cx - slide_w // 2) ** 2 + (cy - slide_h // 2) ** 2

            obj = {
                "slide_index": slide_idx,
                "shape_id": shape_id,
                "text": text,
                "bbox_emu": {"x": left, "y": top, "w": width, "h": height},
                "slide_size_emu": {"width": int(slide_w), "height": int(slide_h)},
                "alignment": alignment_name,
                "font_size_pt": font_size_pt,
                "_area_emu": area_emu,
                "_center_dist_sq": center_dist_sq,
                "_shape_index": shape_idx,
            }
            slide_objs.append(obj)

        key_to_best: Dict[str, Dict[str, Any]] = {}
        for obj in slide_objs:
            key = _normalize_text_for_dedup(obj["text"])
            cur = key_to_best.get(key)
            if cur is None:
                key_to_best[key] = obj
                continue
            if obj["_center_dist_sq"] < cur["_center_dist_sq"]:
                key_to_best[key] = obj

        kept_ids = {id(o) for o in key_to_best.values()}
        for o in slide_objs:
            if id(o) not in kept_ids:
                removed_shape_indices.append((slide_idx, o["_shape_index"]))

        for obj in key_to_best.values():
            del obj["_area_emu"]
            del obj["_center_dist_sq"]
            del obj["_shape_index"]
            objects.append(obj)

    return objects, removed_shape_indices


def _get_para_font_size_pt(para) -> float | None:
    """获取段落首 run 的字号（pt），若无则返回 None。"""
    try:
        if para.runs:
            run = para.runs[0]
            if run.font.size is not None:
                return float(run.font.size.pt)
    except Exception:
        pass
    return None


def _get_run_font_size_pt(run) -> float:
    """获取 run 的字号（pt），若无则返回 0。"""
    try:
        if run.font.size is not None:
            return float(run.font.size.pt)
    except Exception:
        pass
    return 0.0


def clean_small_duplicate_paragraphs_in_pptx(ppt_path: Path, out_path: Path) -> None:
    """
    同一 shape 内：段落级去重 + 同一段落内 run 级去重。
    （不设置 showMasterSp：会同时隐藏背景）
    """
    prs = Presentation(str(ppt_path))
    for slide in prs.slides:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
                continue
            _clean_shape_paragraphs(shape)
            _clean_shape_runs(shape)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))


def _clean_shape_paragraphs(shape) -> None:
    """同一 shape 内：同内容段落只保留字号最大的。"""
    paras = list(shape.text_frame.paragraphs)
    if len(paras) < 2:
        return
    items: List[Tuple[int, str, float]] = []
    for i, p in enumerate(paras):
        t = (p.text or "").strip()
        if not t:
            continue
        key = _normalize_text_for_dedup(t)
        size = _get_para_font_size_pt(p)
        items.append((i, key, size if size is not None else 0.0))
    by_key: Dict[str, List[Tuple[int, float]]] = {}
    for idx, key, size in items:
        by_key.setdefault(key, []).append((idx, size))
    to_remove = set()
    for key, lst in by_key.items():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: -x[1])
        for idx, _ in lst[1:]:
            to_remove.add(idx)
    for idx in sorted(to_remove, reverse=True):
        try:
            para = paras[idx]
            parent = para._p.getparent()
            if parent is not None:
                parent.remove(para._p)
        except Exception:
            pass


def _clean_shape_runs(shape) -> None:
    """同一段落内：同内容 run 只保留字号最大的（文本框内部小字）。"""
    for para in shape.text_frame.paragraphs:
        runs = list(para.runs)
        if len(runs) < 2:
            continue
        items: List[Tuple[int, str, float]] = []
        for i, r in enumerate(runs):
            t = (r.text or "").strip()
            if not t:
                continue
            key = _normalize_text_for_dedup(t)
            size = _get_run_font_size_pt(r)
            items.append((i, key, size))
        by_key: Dict[str, List[Tuple[int, float]]] = {}
        for idx, key, size in items:
            by_key.setdefault(key, []).append((idx, size))
        to_remove = set()
        for key, lst in by_key.items():
            if len(lst) < 2:
                continue
            lst.sort(key=lambda x: -x[1])
            for idx, _ in lst[1:]:
                to_remove.add(idx)
        for idx in sorted(to_remove, reverse=True):
            try:
                run = runs[idx]
                parent = run._r.getparent()
                if parent is not None:
                    parent.remove(run._r)
            except Exception:
                pass


def remove_shapes_from_pptx(
    ppt_path: Path, removed: List[Tuple[int, int]], out_path: Path
) -> None:
    """
    从 PPTX 中删除指定形状并另存。removed = [(slide_index, shape_index), ...]；
    按 slide 分组后每页内按 shape_index 降序删除，避免索引错位。
    """
    prs = Presentation(str(ppt_path))
    by_slide: Dict[int, List[int]] = {}
    for si, shi in removed:
        by_slide.setdefault(si, []).append(shi)
    for si in by_slide:
        indices = sorted(by_slide[si], reverse=True)
        slide = prs.slides[si]
        for idx in indices:
            if idx < len(slide.shapes):
                sp = slide.shapes[idx].element
                parent = sp.getparent()
                if parent is not None:
                    parent.remove(sp)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))

