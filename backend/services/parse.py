from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any

from pptx import Presentation
from pptx.enum.text import PP_ALIGN


def extract_text_objects(ppt_path: Path) -> List[Dict[str, Any]]:
    """
    Parse PPTX and extract text-box-like shapes with EMU bounding boxes.

    Returns list of:
    {
        "slide_index": int,
        "shape_id": str,
        "text": str,
        "bbox_emu": {"x": int, "y": int, "w": int, "h": int},
        "slide_size_emu": {"width": int, "height": int},
    }
    """
    ppt_path = Path(ppt_path)
    prs = Presentation(str(ppt_path))

    slide_w = prs.slide_width
    slide_h = prs.slide_height

    objects: List[Dict[str, Any]] = []

    for slide_idx, slide in enumerate(prs.slides):
        for shape_idx, shape in enumerate(slide.shapes):
            # Only care about shapes with text (title/body/textbox)
            if not hasattr(shape, "has_text_frame") or not shape.has_text_frame:
                continue

            text = shape.text or ""
            if not text.strip():
                continue

            # EMU positions/sizes
            left = int(shape.left)
            top = int(shape.top)
            width = int(shape.width)
            height = int(shape.height)

            # Alignment & font size (for enhanced rendering on frontend)
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
                            # python-pptx exposes point size via .pt
                            font_size_pt = float(run_font.size.pt)
            except Exception:
                # Best-effort; missing alignment/font info should not break parsing
                pass

            shape_id = f"s{slide_idx}_sh{shape_idx}"

            obj = {
                "slide_index": slide_idx,
                "shape_id": shape_id,
                "text": text,
                "bbox_emu": {"x": left, "y": top, "w": width, "h": height},
                "slide_size_emu": {"width": int(slide_w), "height": int(slide_h)},
                "alignment": alignment_name,
                "font_size_pt": font_size_pt,
            }
            objects.append(obj)

    return objects

