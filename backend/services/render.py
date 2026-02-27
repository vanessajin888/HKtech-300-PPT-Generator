from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from PIL import Image


def render_ppt_to_pngs(ppt_path: Path, output_dir: Path) -> List[Dict[str, Any]]:
    """
    Use LibreOffice (soffice) in headless mode to convert PPTX to PNG slides.

    Returns a list of metadata dicts:
    {
        "slide_index": int,  # 0-based
        "path": Path,
        "relative_path": Path,  # relative to backend/storage
        "width": int,
        "height": int,
    }
    """
    ppt_path = ppt_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # LibreOffice command
    # 优先使用环境变量 SOFFICE_PATH（便于公司环境配置），否则回退到默认安装路径
    soffice_cmd = os.environ.get(
        "SOFFICE_PATH", r"C:\Program Files\LibreOffice\program\soffice.exe"
    )

    cmd = [
        soffice_cmd,
        "--headless",
        "--convert-to",
        "png",
        "--outdir",
        str(output_dir),
        str(ppt_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "LibreOffice 'soffice' command not found. "
            "Please ensure LibreOffice is installed and 'soffice' is in PATH."
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed (code={result.returncode}). "
            f"stdout={result.stdout} stderr={result.stderr}"
        )

    # LibreOffice typically outputs files like: <name>.png, <name>_1.png, <name>_2.png ...
    stem = ppt_path.stem
    png_files = sorted(output_dir.glob(f"{stem}*.png"))

    slides: List[Dict[str, Any]] = []
    for idx, img_path in enumerate(sorted(png_files)):
        with Image.open(img_path) as im:
            width, height = im.size

        slides.append(
            {
                "slide_index": idx,
                "path": img_path,
                # relative path from storage root will be filled by caller
                "relative_path": img_path.relative_to(output_dir.parent.parent),
                "width": width,
                "height": height,
            }
        )

    return slides

