from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Dict, Any
import uuid
from datetime import datetime

from pptx import Presentation

from .render import render_ppt_to_pngs
from .parse import extract_text_objects


def _load_suggestions_index(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in data.get("suggestions", [])}


def _resolve_accepted_items(
    accepted: List[Any], suggestions_index: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    accepted 元素可以是：
    - string: suggestion_id
    - object: { slide_index, shape_id, suggested }
    """
    resolved: List[Dict[str, Any]] = []
    for item in accepted:
        if isinstance(item, str):
            sug = suggestions_index.get(item)
            if not sug:
                continue
            resolved.append(
                {
                    "slide_index": sug["slide_index"],
                    "shape_id": sug["shape_id"],
                    "suggested": sug["suggested"],
                }
            )
        elif isinstance(item, dict):
            if (
                "slide_index" in item
                and "shape_id" in item
                and "suggested" in item
            ):
                resolved.append(
                    {
                        "slide_index": int(item["slide_index"]),
                        "shape_id": str(item["shape_id"]),
                        "suggested": str(item["suggested"]),
                    }
                )
    return resolved


def apply_suggestions_to_pptx(
    base_ppt_path: Path,
    storage_dir: Path,
    file_id: str,
    accepted: List[Any],
) -> Dict[str, Any]:
    """
    Apply accepted suggestions to a PPTX file and re-render.

    Returns:
    {
      "output_file_id": str,
      "slides": [...],
      "objects": [...],
      "image_urls": [...]
    }
    """
    base_ppt_path = base_ppt_path.resolve()
    storage_dir = storage_dir.resolve()

    uploads_dir = storage_dir / "uploads"
    renders_dir = storage_dir / "renders"
    objects_dir = storage_dir / "objects"
    outputs_dir = storage_dir / "outputs"

    outputs_dir.mkdir(parents=True, exist_ok=True)
    renders_dir.mkdir(parents=True, exist_ok=True)

    # 当前版本的 objects 元信息（用于派生版本号与 root_file_id）
    current_objects_meta: Dict[str, Any] = {}
    current_objects_path = objects_dir / f"{file_id}.json"
    if current_objects_path.exists():
        try:
            current_objects_meta = json.loads(
                current_objects_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            current_objects_meta = {}

    root_file_id = current_objects_meta.get("root_file_id") or file_id
    try:
        base_version_index = int(current_objects_meta.get("version_index", 0))
    except (TypeError, ValueError):
        base_version_index = 0
    new_version_index = base_version_index + 1

    suggestions_index_path = objects_dir / f"{file_id}_suggestions.json"
    suggestions_index = _load_suggestions_index(suggestions_index_path)
    resolved = _resolve_accepted_items(accepted, suggestions_index)

    if not resolved:
        raise ValueError("No valid accepted suggestions provided.")

    # Load base presentation (prefer outputs/file_id if exists, else uploads)
    candidate_outputs = outputs_dir / f"{file_id}.pptx"
    if candidate_outputs.exists():
        prs_path = candidate_outputs
    else:
        prs_path = uploads_dir / f"{file_id}.pptx"

    if not prs_path.exists():
        raise FileNotFoundError(f"Base PPTX not found for file_id={file_id}")

    prs = Presentation(str(prs_path))

    # Build a quick lookup for accepted suggestions by (slide_index, shape_id)
    accepted_map: Dict[tuple[int, str], str] = {}
    for item in resolved:
        key = (int(item["slide_index"]), str(item["shape_id"]))
        accepted_map[key] = str(item["suggested"])

    # Apply changes
    for slide_index, slide in enumerate(prs.slides):
        for shape_index, shape in enumerate(slide.shapes):
            shape_id = f"s{slide_index}_sh{shape_index}"
            key = (slide_index, shape_id)
            if key not in accepted_map:
                continue

            new_text = accepted_map[key]
            if hasattr(shape, "text_frame") and shape.has_text_frame:
                # Replace entire text content
                shape.text = new_text

    # Save a new output PPTX，作为同一 root_file_id 的下一个版本
    # 生成对用户友好的文件名：原名 + v1/v2/...（v0 保持原名）
    original_name = current_objects_meta.get("file_name") or f"{root_file_id}.pptx"
    stem, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".pptx"
    display_name = f"{stem}_v{new_version_index}{ext}"

    output_file_id = uuid.uuid4().hex
    output_ppt_path = outputs_dir / f"{output_file_id}.pptx"
    prs.save(str(output_ppt_path))

    # Re-render PNGs for the updated PPTX
    output_render_dir = renders_dir / output_file_id
    output_render_dir.mkdir(parents=True, exist_ok=True)
    slide_images = render_ppt_to_pngs(output_ppt_path, output_render_dir)

    # Re-parse objects & compute bbox_px
    objects = extract_text_objects(output_ppt_path)
    slides_meta = []
    slide_index_to_image_meta: Dict[int, Dict[str, Any]] = {}

    for slide_meta in slide_images:
        idx = slide_meta["slide_index"]
        # Relative path from storage root
        rel_path = slide_meta["path"].relative_to(storage_dir)
        slides_meta.append(
            {
                "slide_index": idx,
                "image_url": f"/static/{rel_path.as_posix()}",
                "image_w": slide_meta["width"],
                "image_h": slide_meta["height"],
            }
        )
        slide_index_to_image_meta[idx] = slide_meta

    for obj in objects:
        slide_idx = obj["slide_index"]
        slide_meta = slide_index_to_image_meta.get(slide_idx)
        if not slide_meta:
            continue

        slide_w_emu = obj["slide_size_emu"]["width"]
        slide_h_emu = obj["slide_size_emu"]["height"]
        bbox_emu = obj["bbox_emu"]

        img_w = slide_meta["width"]
        img_h = slide_meta["height"]

        x_px = bbox_emu["x"] / slide_w_emu * img_w
        y_px = bbox_emu["y"] / slide_h_emu * img_h
        w_px = bbox_emu["w"] / slide_w_emu * img_w
        h_px = bbox_emu["h"] / slide_h_emu * img_h

        obj["bbox_px"] = {
            "x": x_px,
            "y": y_px,
            "w": w_px,
            "h": h_px,
        }

    # Persist new objects json，带上版本链路信息
    new_objects_payload = {
        "file_id": output_file_id,
        "file_name": display_name,
        "slide_count": len(slide_images),
        "slides": slides_meta,
        "objects": objects,
        "root_file_id": root_file_id,
        "parent_file_id": file_id,
        "version_index": new_version_index,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    objects_path = objects_dir / f"{output_file_id}.json"
    objects_path.write_text(
        json.dumps(new_objects_payload, ensure_ascii=False), encoding="utf-8"
    )

    image_urls = [s["image_url"] for s in slides_meta]

    return {
        "output_file_id": output_file_id,
        "slides": slides_meta,
        "objects": objects,
        "image_urls": image_urls,
    }

