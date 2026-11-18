"""
AI Patch 写回：修改文本、增删文本框、新增页面。
ONLYOFFICE 负责手动编辑和渲染，本模块负责批量写回 PPTX。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from time import perf_counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Set

from pptx import Presentation
from pptx.util import Emu

logger = logging.getLogger(__name__)
LOG_TIMINGS = os.environ.get("LOG_TIMINGS", "").strip().lower() in ("1", "true", "yes")


def _move_slide(prs: Presentation, from_index: int, to_index: int) -> None:
    """
    python-pptx 不提供官方 insert/move API，这里通过底层 _sldIdLst 重排来移动 slide。
    """
    try:
        sldIdLst = prs.slides._sldIdLst  # pylint: disable=protected-access
        order = list(sldIdLst)
        if from_index < 0 or from_index >= len(order):
            return
        to_index = max(0, min(int(to_index), len(order) - 1))
        el = order.pop(int(from_index))
        order.insert(int(to_index), el)
        for child in list(sldIdLst):
            sldIdLst.remove(child)
        for child in order:
            sldIdLst.append(child)
    except Exception:
        return


def _parse_shape_id(shape_id: str) -> Optional[Tuple[int, str, int]]:
    """
    兼容两种 shape_id：
    - 旧：s0_sh1 -> (slide_index, "index", shape_index)
    - 新：s0_id123 -> (slide_index, "xml", shape_xml_id)
    """
    s = str(shape_id)
    m = re.match(r"s(\d+)_sh(\d+)$", s)
    if m:
        return int(m.group(1)), "index", int(m.group(2))
    m = re.match(r"s(\d+)_id(\d+)$", s)
    if m:
        return int(m.group(1)), "xml", int(m.group(2))
    return None


def _find_shape_in_slide(slide, kind: str, value: int):
    """在 python-pptx slide.shapes 中按索引或 xml shape_id 找 shape。"""
    if kind == "index":
        if 0 <= value < len(slide.shapes):
            return slide.shapes[value]
        return None
    # kind == "xml"
    for shp in slide.shapes:
        try:
            if int(getattr(shp, "shape_id", -1)) == int(value):
                return shp
        except Exception:
            continue
    return None


def _pick_reference_text_shape_for_style(slide, slide_h_emu: int):
    """
    选择一个“参考文本框”来继承字体/颜色等样式：
    - 优先不在顶部（> 25% 高度）的文本框（更像正文）
    - 其次任意有文本框的 shape
    """
    candidates = []
    for shp in slide.shapes:
        try:
            if not getattr(shp, "has_text_frame", False):
                continue
            tf = shp.text_frame
            if not tf:
                continue
            text = (shp.text or "").strip()
            if not text:
                continue
            top = int(getattr(shp, "top", 0))
            area = int(getattr(shp, "width", 0)) * int(getattr(shp, "height", 0))
            candidates.append((top, area, shp))
        except Exception:
            continue

    if not candidates:
        return None

    # 正文候选：top > 25% 高度
    body = [c for c in candidates if c[0] > int(slide_h_emu * 0.25)]
    if body:
        # 选面积最大的
        return sorted(body, key=lambda x: x[1], reverse=True)[0][2]
    # 否则也选面积最大的
    return sorted(candidates, key=lambda x: x[1], reverse=True)[0][2]


def _extract_font_style(shp):
    """
    从 python-pptx shape 里尽量提取字体样式（name/size/color）。
    返回 dict: {name, size, rgb}
    """
    try:
        tf = shp.text_frame
        if not tf or not tf.paragraphs:
            return {}
        p0 = tf.paragraphs[0]
        # 先从 run 拿（最可靠）
        if p0.runs:
            r0 = p0.runs[0]
            f = r0.font
        else:
            f = p0.font
        style = {}
        if getattr(f, "name", None):
            style["name"] = f.name
        if getattr(f, "size", None):
            style["size"] = f.size  # Length
        try:
            if f.color and f.color.rgb:
                style["rgb"] = f.color.rgb  # RGBColor
        except Exception:
            pass
        return style
    except Exception:
        return {}


def _apply_font_style_to_textbox(tb, style: Dict[str, Any]):
    """
    把提取到的样式应用到新文本框的所有 runs。
    """
    try:
        tf = tb.text_frame
        if not tf:
            return
        for p in tf.paragraphs:
            for r in p.runs:
                f = r.font
                if style.get("name"):
                    f.name = style["name"]
                if style.get("size"):
                    f.size = style["size"]
                if style.get("rgb"):
                    try:
                        f.color.rgb = style["rgb"]
                    except Exception:
                        pass
    except Exception:
        return


def apply_manual_edits_batch(
    storage_dir: Path,
    version_id: str,
    patches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    AI 修改批量写回：一次打开和保存 PPTX，同时更新对象元数据。

    patches: [{slide_index, shape_id, action, payload, timestamp?}, ...]
    返回:
      {
        "output_file_id": version_id,
        "results": [
          { "slide_index", "updated_slide", "updated_objects", "changed_shape_ids" },
          ...
        ]
      }
    """
    t0 = perf_counter()
    storage_dir = storage_dir.resolve()
    uploads_dir = storage_dir / "uploads"
    objects_dir = storage_dir / "objects"
    outputs_dir = storage_dir / "outputs"

    version_id = (version_id or "").strip()
    if not version_id:
        raise ValueError("version_id is required")

    ppt_path = outputs_dir / f"{version_id}.pptx"
    if not ppt_path.exists():
        ppt_path = uploads_dir / f"{version_id}.pptx"
    if not ppt_path.exists():
        raise FileNotFoundError(f"PPTX not found for version_id={version_id}")

    objects_path = objects_dir / f"{version_id}.json"
    current_objects_meta: Dict[str, Any] = {}
    t_read_meta = perf_counter()
    if objects_path.exists():
        try:
            current_objects_meta = json.loads(objects_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current_objects_meta = {}
    t_read_meta_done = perf_counter()

    # 原地写回：若在 uploads 中，先拷贝到 outputs
    t_prepare = perf_counter()
    if ppt_path.parent == uploads_dir:
        out_inplace = outputs_dir / f"{version_id}.pptx"
        shutil.copy2(ppt_path, out_inplace)
        ppt_path = out_inplace
    t_prepare_done = perf_counter()

    t_open = perf_counter()
    prs = Presentation(str(ppt_path))
    t_open_done = perf_counter()
    slide_w_emu = int(prs.slide_width)
    slide_h_emu = int(prs.slide_height)

    affected_slides: Set[int] = set()
    changed_shape_ids_by_slide: Dict[int, Set[str]] = {}

    # 执行所有 edits（不渲染/不 parse）
    t_apply = perf_counter()
    for p in patches:
        if not isinstance(p, dict):
            continue
        try:
            si = int(p.get("slide_index"))
        except (TypeError, ValueError):
            continue
        shape_id = str(p.get("shape_id") or "")
        action = str(p.get("action") or "")
        payload = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        action_lower = action.lower()

        if action_lower in ("add-slide", "add_slide"):
            # 新增页面，支持通过 insert_index 指定位置
            title = payload.get("title", "")
            bullets = payload.get("bullets", [])
            insert_index = payload.get("insert_index")
            if not isinstance(title, str):
                title = str(title)
            if not isinstance(bullets, list):
                bullets = []
            bullets = [str(x).strip() for x in bullets if str(x).strip()]
            if not title.strip():
                title = "Key Insights"
            if not bullets:
                bullets = ["Key trend", "Market growth", "Strategic impact"]

            # 选择一个尽量“空白”的 layout
            try:
                blank_layout = prs.slide_layouts[6]  # 通常是 blank
            except Exception:
                blank_layout = prs.slide_layouts[0]

            new_slide = prs.slides.add_slide(blank_layout)
            new_si = len(prs.slides) - 1
            # 允许插入到任意位置：insert_index = 0-based（相当于 insert 到该 index）
            try:
                if insert_index is None:
                    target_index = new_si
                else:
                    target_index = int(insert_index)
                    target_index = max(0, min(target_index, new_si))
            except Exception:
                target_index = new_si
            if target_index != new_si:
                _move_slide(prs, new_si, target_index)
                new_si = int(target_index)

            # 尝试从“上一页”继承样式：title 用标题区最大框，body 用正文候选
            ref_title_shape = None
            ref_body_shape = None
            if new_si - 1 >= 0:
                prev_slide = prs.slides[new_si - 1]
                # title: top 25% 内面积最大且有文本
                best_area = -1
                for shp in prev_slide.shapes:
                    try:
                        if not getattr(shp, "has_text_frame", False):
                            continue
                        t = (shp.text or "").strip()
                        if not t:
                            continue
                        top = int(getattr(shp, "top", 0))
                        w = int(getattr(shp, "width", 0))
                        h = int(getattr(shp, "height", 0))
                        if top > int(slide_h_emu * 0.25):
                            continue
                        area = w * h
                        if area > best_area:
                            best_area = area
                            ref_title_shape = shp
                    except Exception:
                        continue
                # body: 复用现有 helper（正文候选）
                try:
                    ref_body_shape = _pick_reference_text_shape_for_style(prev_slide, slide_h_emu)
                except Exception:
                    ref_body_shape = None

            title_style = _extract_font_style(ref_title_shape) if ref_title_shape is not None else {}
            body_style = _extract_font_style(ref_body_shape) if ref_body_shape is not None else {}

            # 标题框 + 正文框的默认版式（EMU）
            title_left = int(slide_w_emu * 0.08)
            title_top = int(slide_h_emu * 0.08)
            title_w = int(slide_w_emu * 0.84)
            title_h = int(slide_h_emu * 0.14)

            body_left = int(slide_w_emu * 0.10)
            body_top = int(slide_h_emu * 0.26)
            body_w = int(slide_w_emu * 0.80)
            body_h = int(slide_h_emu * 0.58)

            tb_title = new_slide.shapes.add_textbox(
                Emu(title_left), Emu(title_top), Emu(title_w), Emu(title_h)
            )
            try:
                tb_title.text_frame.text = title.strip()
            except Exception:
                tb_title.text = title.strip()
            _apply_font_style_to_textbox(tb_title, title_style)

            body_text = "\n".join([f"• {b}" for b in bullets])
            tb_body = new_slide.shapes.add_textbox(
                Emu(body_left), Emu(body_top), Emu(body_w), Emu(body_h)
            )
            try:
                tb_body.text_frame.text = body_text
            except Exception:
                tb_body.text = body_text
            _apply_font_style_to_textbox(tb_body, body_style)

            # 更新 objects.json：插入 slide + 追加 2 个 object
            slides_meta = current_objects_meta.get("slides")
            if not isinstance(slides_meta, list):
                slides_meta = []
            # 先整体右移 >= new_si 的页码
            shifted_slides_meta: List[Dict[str, Any]] = []
            for s in slides_meta:
                try:
                    idx0 = int(s.get("slide_index"))
                except Exception:
                    shifted_slides_meta.append(s)
                    continue
                if idx0 >= new_si:
                    s = dict(s)
                    s["slide_index"] = idx0 + 1
                shifted_slides_meta.append(s)
            shifted_slides_meta.append({"slide_index": new_si})
            shifted_slides_meta.sort(key=lambda x: int(x.get("slide_index", 0) or 0))
            current_objects_meta["slides"] = shifted_slides_meta

            objs_meta = current_objects_meta.get("objects")
            if not isinstance(objs_meta, list):
                objs_meta = []
            # 先整体右移 >= new_si 的对象页码
            shifted_objs_meta: List[Dict[str, Any]] = []
            for o in objs_meta:
                try:
                    idx0 = int(o.get("slide_index"))
                except Exception:
                    shifted_objs_meta.append(o)
                    continue
                if idx0 >= new_si:
                    o = dict(o)
                    o["slide_index"] = idx0 + 1
                    # shape_id 里也包含 slide index（s{n}_...），一并更新，避免后续定位混乱
                    sid = str(o.get("shape_id") or "")
                    m = re.match(r"s(\d+)_(.+)$", sid)
                    if m:
                        try:
                            old = int(m.group(1))
                            if old == idx0:
                                o["shape_id"] = f"s{idx0+1}_{m.group(2)}"
                        except Exception:
                            pass
                shifted_objs_meta.append(o)

            title_shape_id = f"s{new_si}_id{int(getattr(tb_title, 'shape_id', 0))}"
            body_shape_id = f"s{new_si}_id{int(getattr(tb_body, 'shape_id', 0))}"
            shifted_objs_meta.append(
                {
                    "slide_index": new_si,
                    "shape_id": title_shape_id,
                    "text": title.strip(),
                    "bbox_emu": {"x": title_left, "y": title_top, "w": title_w, "h": title_h},
                    "slide_size_emu": {"width": slide_w_emu, "height": slide_h_emu},
                    "alignment": "LEFT",
                    "font_size_pt": float(title_style["size"].pt) if title_style.get("size") is not None else None,
                }
            )
            shifted_objs_meta.append(
                {
                    "slide_index": new_si,
                    "shape_id": body_shape_id,
                    "text": body_text,
                    "bbox_emu": {"x": body_left, "y": body_top, "w": body_w, "h": body_h},
                    "slide_size_emu": {"width": slide_w_emu, "height": slide_h_emu},
                    "alignment": "LEFT",
                    "font_size_pt": float(body_style["size"].pt) if body_style.get("size") is not None else None,
                }
            )
            current_objects_meta["objects"] = shifted_objs_meta

            affected_slides.add(new_si)
            changed_shape_ids_by_slide.setdefault(new_si, set()).update({title_shape_id, body_shape_id})
            continue

        if action_lower in ("add-textbox", "add_textbox", "add"):
            if si < 0 or si >= len(prs.slides):
                continue
            slide = prs.slides[si]

            text = payload.get("text", "")
            if not isinstance(text, str):
                text = str(text)
            if not text.strip():
                prefix = "新文本框"
                max_n = 0
                for obj in current_objects_meta.get("objects") or []:
                    if obj.get("slide_index") != si:
                        continue
                    t = str(obj.get("text") or "")
                    if t.startswith(prefix):
                        tail = t[len(prefix) :].strip()
                        try:
                            n = int(tail)
                        except (TypeError, ValueError):
                            continue
                        if n > max_n:
                            max_n = n
                text = f"{prefix}{max_n + 1}"
            position = str(payload.get("position") or "after_title")

            # 根据标题位置安排新增文本框
            title_bottom = int(slide_h_emu * 0.18)
            title_width = int(slide_w_emu * 0.8)
            for shp in slide.shapes:
                try:
                    top = int(getattr(shp, "top", 0))
                    w = int(getattr(shp, "width", 0))
                    h = int(getattr(shp, "height", 0))
                except Exception:
                    continue
                if top <= int(slide_h_emu * 0.25) and w * h > title_width * title_bottom:
                    title_bottom = top + h
                    title_width = w
            if position != "after_title":
                position = "after_title"
            margin = int(slide_h_emu * 0.03)
            left = int(slide_w_emu * 0.1)
            top = min(title_bottom + margin, int(slide_h_emu * 0.75))
            width = int(slide_w_emu * 0.8)
            height = int(slide_h_emu * 0.18)
            if top + height > slide_h_emu:
                top = max(int(slide_h_emu * 0.6), 0)

            tb = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
            try:
                tb.text_frame.text = text
            except Exception:
                tb.text = text
            new_shape_id = f"s{si}_id{int(getattr(tb, 'shape_id', 0))}"

            # 样式继承（batch）：同样从当前页取参考文本框
            ref = _pick_reference_text_shape_for_style(slide, slide_h_emu)
            style = _extract_font_style(ref) if ref is not None else {}
            _apply_font_style_to_textbox(tb, style)

            # 更新 objects.json：追加 object（batch 模式也必须更新 meta）
            bbox_emu = {"x": left, "y": top, "w": width, "h": height}
            objs_meta = current_objects_meta.get("objects")
            if not isinstance(objs_meta, list):
                objs_meta = []
            objs_meta.append(
                {
                    "slide_index": si,
                    "shape_id": new_shape_id,
                    "text": text,
                    "bbox_emu": bbox_emu,
                    "slide_size_emu": {"width": slide_w_emu, "height": slide_h_emu},
                    "alignment": "LEFT",
                    "font_size_pt": float(style["size"].pt) if style.get("size") is not None else None,
                }
            )
            current_objects_meta["objects"] = objs_meta

            affected_slides.add(si)
            changed_shape_ids_by_slide.setdefault(si, set()).add(new_shape_id)
            continue

        parsed = _parse_shape_id(shape_id)
        if not parsed:
            continue
        shape_slide, kind, shape_key = parsed
        if shape_slide != si:
            continue
        if si < 0 or si >= len(prs.slides):
            continue
        slide = prs.slides[si]
        shape = _find_shape_in_slide(slide, kind, shape_key)
        if shape is None:
            continue

        if action_lower in ("edit-text", "edit_text", "edit"):
            if not getattr(shape, "has_text_frame", False):
                continue
            text = payload.get("text", "")
            if not isinstance(text, str):
                text = str(text)
            shape.text = text
        elif action_lower in ("delete", "delete-textbox", "delete_textbox"):
            parent = shape._element.getparent()
            if parent is not None:
                parent.remove(shape._element)
        else:
            continue

        affected_slides.add(si)
        changed_shape_ids_by_slide.setdefault(si, set()).add(shape_id)
    t_apply_done = perf_counter()

    # 保存一次
    t_save = perf_counter()
    prs.save(str(ppt_path))
    t_save_done = perf_counter()

    prev_slides = current_objects_meta.get("slides") or []
    prev_objects = current_objects_meta.get("objects") or []
    results: List[Dict[str, Any]] = []

    # 快速模式：直接基于 patch payload 更新 objects.json（不做同步渲染/解析）
    t_update_json = perf_counter()
    objects_all = prev_objects
    for p in patches:
        if not isinstance(p, dict):
            continue
        try:
            si = int(p.get("slide_index"))
        except (TypeError, ValueError):
            continue
        shape_id = str(p.get("shape_id") or "")
        action = str(p.get("action") or "").lower()
        payload = p.get("payload") if isinstance(p.get("payload"), dict) else {}

        next_objs: List[Dict[str, Any]] = []
        for o in objects_all:
            if o.get("slide_index") == si and o.get("shape_id") == shape_id:
                if action in ("delete", "delete-textbox", "delete_textbox"):
                    continue
                if action in ("edit-text", "edit_text", "edit"):
                    o["text"] = str(payload.get("text", ""))
            next_objs.append(o)
        objects_all = next_objs

    # 不再渲染 PNG：slides_meta 仅沿用旧值（或为空）
    slides_meta = list(prev_slides)
    for si in sorted(affected_slides):
        updated_slide = next(
            (s for s in slides_meta if s.get("slide_index") == si),
            next((s for s in prev_slides if s.get("slide_index") == si), {"slide_index": si}),
        )
        updated_objects = [o for o in objects_all if o.get("slide_index") == si]
        results.append(
            {
                "slide_index": si,
                "updated_slide": updated_slide,
                "updated_objects": updated_objects,
                "changed_shape_ids": sorted(changed_shape_ids_by_slide.get(si, set())),
            }
        )

    current_objects_meta["slides"] = slides_meta
    current_objects_meta["objects"] = objects_all
    current_objects_meta["file_id"] = version_id
    # 手动编辑不增加版本号/链路
    current_objects_meta["created_at"] = datetime.utcnow().isoformat() + "Z"

    objects_path.write_text(json.dumps(current_objects_meta, ensure_ascii=False), encoding="utf-8")
    t_update_json_done = perf_counter()

    if LOG_TIMINGS:
        msg = (
            f"[TIMING] manual_edit(batch) version_id={version_id} patches={len(patches)} slides={len(affected_slides)} "
            f"total={(perf_counter()-t0)*1000:.1f}ms "
            f"read_meta={(t_read_meta_done-t_read_meta)*1000:.1f}ms "
            f"prepare={(t_prepare_done-t_prepare)*1000:.1f}ms "
            f"open={(t_open_done-t_open)*1000:.1f}ms "
            f"apply={(t_apply_done-t_apply)*1000:.1f}ms "
            f"save={(t_save_done-t_save)*1000:.1f}ms "
            f"update_json={(t_update_json_done-t_update_json)*1000:.1f}ms"
        )
        print(msg)

    return {"output_file_id": version_id, "results": results}
