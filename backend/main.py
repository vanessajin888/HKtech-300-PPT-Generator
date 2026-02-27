from pathlib import Path
import uuid
from typing import List, Dict, Any
import json
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

from services.render import render_ppt_to_pngs
from services.parse import extract_text_objects
from services.suggest import (
    get_suggestion_provider,
    validate_suggestions,
    suggestions_to_dicts,
)
from services.apply import apply_suggestions_to_pptx

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
RENDERS_DIR = STORAGE_DIR / "renders"
OBJECTS_DIR = STORAGE_DIR / "objects"
OUTPUTS_DIR = STORAGE_DIR / "outputs"

for d in [UPLOAD_DIR, RENDERS_DIR, OBJECTS_DIR, OUTPUTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PPT Cursor-like Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (rendered images, outputs, etc.)
app.mount("/static", StaticFiles(directory=STORAGE_DIR), name="static")


def build_file_id() -> str:
    return uuid.uuid4().hex


@app.post("/upload")
async def upload_ppt(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only .pptx files are supported")

    file_id = build_file_id()
    upload_path = UPLOAD_DIR / f"{file_id}.pptx"

    content = await file.read()
    upload_path.write_bytes(content)

    render_dir = RENDERS_DIR / file_id
    render_dir.mkdir(parents=True, exist_ok=True)

    # Render to PNGs using LibreOffice
    slide_images = render_ppt_to_pngs(upload_path, render_dir)
    if not slide_images:
        raise HTTPException(status_code=500, detail="Failed to render PPTX to images")

    # Parse PPTX text objects (EMU bboxes, slide size)
    objects = extract_text_objects(upload_path)

    # Map EMU bbox to px based on rendered images
    slides_meta: List[Dict[str, Any]] = []
    slide_index_to_image_meta: Dict[int, Dict[str, Any]] = {}

    for slide_meta in slide_images:
        idx = slide_meta["slide_index"]
        rel_path = slide_meta["relative_path"]
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

    # Persist objects + slide info for later APIs（作为 version 0）
    objects_payload = {
        "file_id": file_id,
        "file_name": file.filename,
        "slide_count": len(slide_images),
        "slides": slides_meta,
        "objects": objects,
        # 版本信息：上传生成的原始 PPT 视为 v0
        "root_file_id": file_id,
        "parent_file_id": None,
        "version_index": 0,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    objects_path = OBJECTS_DIR / f"{file_id}.json"
    objects_path.write_text(
        json.dumps(objects_payload, ensure_ascii=False), encoding="utf-8"
    )

    image_urls = [s["image_url"] for s in slides_meta]

    return {
        "file_id": file_id,
        "file_name": file.filename,
        "page_count": len(slide_images),
        "image_urls": image_urls,
    }


@app.get("/objects")
def get_objects(file_id: str) -> Dict[str, Any]:
    objects_path = OBJECTS_DIR / f"{file_id}.json"
    if not objects_path.exists():
        raise HTTPException(status_code=404, detail="file_id not found")

    data = json.loads(objects_path.read_text(encoding="utf-8"))
    return data


@app.post("/suggest")
async def suggest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    body: { file_id, instruction }
    returns: { suggestions: [...] }
    """
    file_id = payload.get("file_id")
    instruction = payload.get("instruction") or ""
    if not file_id or not instruction:
        raise HTTPException(
            status_code=400, detail="file_id and instruction are required"
        )

    objects_path = OBJECTS_DIR / f"{file_id}.json"
    if not objects_path.exists():
        raise HTTPException(status_code=404, detail="file_id not found")

    data = json.loads(objects_path.read_text(encoding="utf-8"))
    objects = data.get("objects", [])

    # 根据环境变量选择使用 Mock LLM 或 DeepSeek
    provider = get_suggestion_provider()
    provider_name = type(provider).__name__
    raw_suggestions = provider.generate(instruction, objects)
    valid_suggestions = validate_suggestions(raw_suggestions, objects)

    result_dicts = suggestions_to_dicts(valid_suggestions)

    # Persist suggestions index for later /apply
    suggestions_index_path = OBJECTS_DIR / f"{file_id}_suggestions.json"
    suggestions_index_payload = {"file_id": file_id, "suggestions": result_dicts}
    suggestions_index_path.write_text(
        json.dumps(suggestions_index_payload, ensure_ascii=False), encoding="utf-8"
    )

    return {"provider": provider_name, "suggestions": result_dicts}


@app.post("/apply")
async def apply_changes(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    body: { file_id, accepted: [suggestion_id or {slide_index,shape_id,suggested}] }
    """
    file_id = payload.get("file_id")
    accepted = payload.get("accepted") or []

    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    try:
        result = apply_suggestions_to_pptx(
            base_ppt_path=UPLOAD_DIR / f"{file_id}.pptx",
            storage_dir=STORAGE_DIR,
            file_id=file_id,
            accepted=accepted,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Apply failed: {e}")

    return {
        "output_file_id": result["output_file_id"],
        "image_urls": result["image_urls"],
    }


@app.get("/download")
def download_ppt(file_id: str):
    """
    下载指定版本的 PPTX。

    - 优先从 outputs/ 读取（修改后的版本）
    - 若不存在则从 uploads/ 读取（原始 v0）
    - 下载文件名优先使用 objects/{file_id}.json 中记录的 file_name
    """
    # 先解析版本元数据，拿到逻辑上的文件名
    logical_filename = f"{file_id}.pptx"
    objects_path = OBJECTS_DIR / f"{file_id}.json"
    if objects_path.exists():
        try:
            meta = json.loads(objects_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("file_name"):
                logical_filename = str(meta["file_name"])
        except json.JSONDecodeError:
            pass

    # 决定实际物理路径：先找 outputs，再找 uploads
    output_path = OUTPUTS_DIR / f"{file_id}.pptx"
    if output_path.exists():
        actual_path = output_path
    else:
        upload_path = UPLOAD_DIR / f"{file_id}.pptx"
        if upload_path.exists():
            actual_path = upload_path
        else:
            raise HTTPException(status_code=404, detail="PPTX not found for given file_id")

    return FileResponse(
        path=actual_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=logical_filename,
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

