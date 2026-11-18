from pathlib import Path
from urllib.parse import quote
import shutil
import uuid
import logging
from typing import List, Dict, Any
import json
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Request

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
import requests
from threading import Lock

from pptx import Presentation
from services.parse import (
    extract_text_objects,
    remove_shapes_from_pptx,
    clean_small_duplicate_paragraphs_in_pptx,
)
from models_patch import SlidePatch
from services_ai import handle_user_request, apply_patches_to_onlyoffice
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
# 显式加载 backend/.env（用于 BACKEND_PUBLIC_BASE_URL / DeepSeek 配置等）
load_dotenv(dotenv_path=str((BASE_DIR / ".env").resolve()), override=False)
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
OBJECTS_DIR = STORAGE_DIR / "objects"
OUTPUTS_DIR = STORAGE_DIR / "outputs"
HISTORY_DIR = OUTPUTS_DIR / "_history"

for d in [UPLOAD_DIR, OBJECTS_DIR, OUTPUTS_DIR, HISTORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 记录每个 file_id 最近一次 callback 成功落盘时间（用于前端等待同步）
_LAST_SAVED: Dict[str, str] = {}
_LAST_SAVED_LOCK = Lock()

app = FastAPI(title="PPT Cursor-like Demo")

app.add_middleware(
    CORSMiddleware,
    # Demo 场景（ngrok / 多设备）下允许任意来源拉取插件静态资源与下载文件
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (rendered images, outputs, etc.)
app.mount("/static", StaticFiles(directory=STORAGE_DIR), name="static")

# Serve ONLYOFFICE plugins (for DocumentServer iframe)
OO_PLUGINS_DIR = BASE_DIR / "oo_plugins"
OO_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/oo-plugins", StaticFiles(directory=OO_PLUGINS_DIR), name="oo-plugins")


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

    # Parse 先做一遍，得到要保留的 objects 和要删除的「同内容小字」形状索引
    objects, removed_shape_indices = extract_text_objects(upload_path)

    # 旧方案（PNG 渲染 / bbox_px / image_urls）已移除：
    # - ONLYOFFICE 负责渲染与编辑
    # - 我们只保留 objects 元数据用于 AI patch 定位与 python-pptx 写回
    #
    # 这里仍可做一次轻量清理（不依赖渲染）：删除重复小字 shape + 清理小段落
    cleaned_path = UPLOAD_DIR / f"{file_id}__cleaned.pptx"
    try:
        if removed_shape_indices:
            remove_shapes_from_pptx(upload_path, removed_shape_indices, cleaned_path)
            logger.info("removed %d duplicate small shape(s)", len(removed_shape_indices))
        else:
            shutil.copy2(upload_path, cleaned_path)
        clean_small_duplicate_paragraphs_in_pptx(cleaned_path, cleaned_path)
        # 清理版仅用于后续更准确的 objects 提取（可选）
        objects, _ = extract_text_objects(cleaned_path)
    except Exception as e:
        logger.warning("clean ppt failed, continue with original objects: %s", e)
    finally:
        try:
            if cleaned_path.exists():
                cleaned_path.unlink()
        except Exception:
            pass

    # slides_meta：仅保留 slide_index（不再包含 image_url/image_w/image_h）
    try:
        prs = Presentation(str(upload_path))
        slide_count = len(prs.slides)
    except Exception:
        slide_count = max([o.get("slide_index", 0) for o in objects], default=-1) + 1
    slides_meta: List[Dict[str, Any]] = [{"slide_index": i} for i in range(slide_count)]

    # Persist objects + slide info for later APIs（作为 version 0）
    objects_payload = {
        "file_id": file_id,
        "file_name": file.filename,
        "slide_count": slide_count,
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

    return {
        "file_id": file_id,
        "file_name": file.filename,
        "page_count": slide_count,
    }


@app.get("/file/{file_id}")
async def get_file_url(file_id: str) -> Dict[str, Any]:
    """
    ONLYOFFICE 使用的文件 URL：返回一个可下载该 PPTX 的直链。
    注意：DocumentServer 跑在 Docker 里，容器内访问宿主机需要用 host.docker.internal。
    """
    file_id = (file_id or "").strip().rstrip("/")
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")
    # 复用 /download 路径，让前端直接把这个 URL 配给 ONLYOFFICE。
    #
    # 本地开发：容器内访问宿主机用 host.docker.internal
    # 公网演示（ngrok）：设置 BACKEND_PUBLIC_BASE_URL，让外网与 DocumentServer 都能访问
    public_base = (os.getenv("BACKEND_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base:
        url = f"{public_base}/download?file_id={file_id}"
    else:
        url = f"http://host.docker.internal:8001/download?file_id={file_id}"
    return {"file_url": url}


@app.get("/objects")
def get_objects(file_id: str) -> Dict[str, Any]:
    objects_path = OBJECTS_DIR / f"{file_id}.json"
    if not objects_path.exists():
        raise HTTPException(status_code=404, detail="file_id not found")

    data = json.loads(objects_path.read_text(encoding="utf-8"))
    return data


@app.get("/download")
def download_ppt(file_id: str):
    """
    下载指定版本的 PPTX。

    - 优先从 outputs/ 读取（修改后的版本）
    - 若不存在则从 uploads/ 读取（原始 v0）
    - 下载文件名优先使用 objects/{file_id}.json 中记录的 file_name
    """
    file_id = (file_id or "").strip().rstrip("/")
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

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
        actual_path = output_path.resolve()
    else:
        upload_path = UPLOAD_DIR / f"{file_id}.pptx"
        if upload_path.exists():
            actual_path = upload_path.resolve()
        else:
            raise HTTPException(status_code=404, detail="PPTX not found for given file_id")

    file_size = actual_path.stat().st_size
    if file_size < 500:
        raise HTTPException(
            status_code=500,
            detail=f"File too small or corrupted ({file_size} bytes). Please re-upload.",
        )

    content = actual_path.read_bytes()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f"attachment; filename=\"presentation.pptx\"; filename*=UTF-8''{quote(logical_filename, safe='')}",
            "Content-Length": str(len(content)),
        },
    )


def _write_objects_meta(file_id: str, ppt_path: Path, file_name: str | None = None) -> Dict[str, Any]:
    """
    从 PPTX 重建 objects.json（用于 ONLYOFFICE 手动编辑回写后同步 AI 定位）。
    """
    objects, _ = extract_text_objects(ppt_path)
    prs = Presentation(str(ppt_path))
    slide_count = len(prs.slides)
    slides_meta = [{"slide_index": i} for i in range(slide_count)]
    payload = {
        "file_id": file_id,
        "file_name": file_name or f"{file_id}.pptx",
        "slide_count": slide_count,
        "slides": slides_meta,
        "objects": objects,
        "root_file_id": file_id,
        "parent_file_id": None,
        "version_index": 0,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    (OBJECTS_DIR / f"{file_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


@app.post("/onlyoffice/callback/{file_id}")
async def onlyoffice_callback(file_id: str, request: Request) -> Dict[str, Any]:
    """
    ONLYOFFICE DocumentServer 回调。
    当用户在编辑器内手动编辑并保存/关闭后，DocumentServer 会回调此接口并提供可下载的文件 URL。
    我们在这里：
    - 下载最新 PPTX 到 outputs/{file_id}.pptx
    - 保存上一版本快照到 outputs/_history/{file_id}/{ts}.pptx
    - 重建 objects.json，保证后续 AI patch 定位一致

    返回格式必须是：{ "error": 0 }
    """
    file_id = (file_id or "").strip().rstrip("/")
    if not file_id:
        return {"error": 1}

    # 1) 打印原始回调内容，便于排查
    print("CALLBACK HIT")
    body = await request.json()
    print(body)

    status = int(body.get("status", 0) or 0)
    url = str(body.get("url") or "").strip()

    # 1: editing, 2: ready for save, 4: closed w/o changes, 6/7: force save variants
    # 这里只在 status=2/6 且有 url 时真正写盘
    if status not in (2, 6):
        return {"error": 0}

    if not url:
        logger.warning("[ONLYOFFICE_CALLBACK] missing url status=%s body_keys=%s", status, list(body.keys()))
        return {"error": 0}

    out_path = OUTPUTS_DIR / f"{file_id}.pptx"
    hist_dir = HISTORY_DIR / file_id
    hist_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # snapshot current output (if exists)
    if out_path.exists():
        try:
            shutil.copy2(out_path, hist_dir / f"{ts}.pptx")
        except Exception as e:
            logger.warning("[ONLYOFFICE_CALLBACK] snapshot failed: %s", e)

    # download latest file from documentserver
    logger.info("[ONLYOFFICE_CALLBACK] status=%s url=%s", status, url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    print("DOWNLOADED OK", file_id)

    # reuse original filename if known
    logical_name = None
    obj_path = OBJECTS_DIR / f"{file_id}.json"
    if obj_path.exists():
        try:
            meta = json.loads(obj_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("file_name"):
                logical_name = str(meta["file_name"])
        except Exception:
            logical_name = None

    _write_objects_meta(file_id=file_id, ppt_path=out_path, file_name=logical_name)
    with _LAST_SAVED_LOCK:
        _LAST_SAVED[file_id] = datetime.utcnow().isoformat() + "Z"
    return {"error": 0}


@app.get("/onlyoffice/last-saved/{file_id}")
def onlyoffice_last_saved(file_id: str) -> Dict[str, Any]:
    file_id = (file_id or "").strip().rstrip("/")
    with _LAST_SAVED_LOCK:
        ts = _LAST_SAVED.get(file_id)
    return {"file_id": file_id, "last_saved_at": ts}


@app.post("/onlyoffice/force-save/{file_id}")
def onlyoffice_force_save(file_id: str, doc_key: str | None = None) -> Dict[str, Any]:
    """
    预留的 forcesave 接口：
    - doc_key 对应 ONLYOFFICE 的 document.key（例如 fileId_v0 / fileId_123456789）
    - 若未提供 doc_key，则回退为 file_id（兼容旧用法）
    """
    file_id = (file_id or "").strip().rstrip("/")
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    key = (doc_key or file_id).strip()
    if not key:
        raise HTTPException(status_code=400, detail="doc_key is empty")

    ds_base = (os.getenv("ONLYOFFICE_INTERNAL_BASE") or "http://localhost").rstrip("/")
    payload = {"c": "forcesave", "key": key, "userdata": f"host-force-save:{file_id}:{key}"}
    cmd_url = f"{ds_base}/command"
    try:
        r = requests.post(cmd_url, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json() if r.content else {}
        return {"status": "ok", "command_service": cmd_url, "result": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"forcesave failed: {e}")


@app.get("/versions/{file_id}")
def list_versions(file_id: str) -> Dict[str, Any]:
    """
    列出历史快照（用于回滚）。
    """
    file_id = (file_id or "").strip().rstrip("/")
    hist_dir = HISTORY_DIR / file_id
    items: List[Dict[str, Any]] = []
    if hist_dir.exists():
        for p in sorted(hist_dir.glob("*.pptx"), reverse=True):
            try:
                items.append(
                    {
                        "version_id": p.stem,
                        "size_bytes": p.stat().st_size,
                    }
                )
            except Exception:
                continue
    return {"file_id": file_id, "versions": items}


class RollbackRequest(BaseModel):
    file_id: str
    version_id: str


@app.post("/rollback")
def rollback(req: RollbackRequest) -> Dict[str, Any]:
    """
    回滚到某个历史快照：覆盖 outputs/{file_id}.pptx 并重建 objects.json。
    """
    file_id = (req.file_id or "").strip().rstrip("/")
    version_id = (req.version_id or "").strip()
    if not file_id or not version_id:
        raise HTTPException(status_code=400, detail="file_id and version_id are required")

    src = HISTORY_DIR / file_id / f"{version_id}.pptx"
    if not src.exists():
        raise HTTPException(status_code=404, detail="version not found")

    dst = OUTPUTS_DIR / f"{file_id}.pptx"
    shutil.copy2(src, dst)

    logical_name = None
    obj_path = OBJECTS_DIR / f"{file_id}.json"
    if obj_path.exists():
        try:
            meta = json.loads(obj_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("file_name"):
                logical_name = str(meta["file_name"])
        except Exception:
            logical_name = None

    _write_objects_meta(file_id=file_id, ppt_path=dst, file_name=logical_name)
    return {"status": "ok", "file_id": file_id, "version_id": version_id}


def _clear_storage_dirs() -> tuple[int, int]:
    """清空 storage 下 uploads、outputs、objects（以及遗留的 renders），返回 (删除文件数, 删除字节数)。"""
    total_count, total_bytes = 0, 0
    legacy_renders_dir = STORAGE_DIR / "renders"
    for dir_path in [UPLOAD_DIR, OUTPUTS_DIR, OBJECTS_DIR, legacy_renders_dir]:
        if not dir_path.exists():
            continue
        for f in dir_path.rglob("*"):
            if f.is_file():
                try:
                    total_bytes += f.stat().st_size
                    total_count += 1
                    f.unlink()
                except OSError as ex:
                    logger.warning("skip %s: %s", f, ex)
        if dir_path != OBJECTS_DIR:
            for sub in sorted(dir_path.iterdir(), reverse=True):
                if sub.is_dir() and not any(sub.iterdir()):
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
    # 若 renders 本身已空，顺手移除目录
    if legacy_renders_dir.exists() and legacy_renders_dir.is_dir() and not any(legacy_renders_dir.iterdir()):
        try:
            legacy_renders_dir.rmdir()
        except OSError:
            pass
    return total_count, total_bytes


@app.post("/clear-storage")
def clear_storage() -> Dict[str, Any]:
    """清空所有 storage（uploads、outputs、objects；以及遗留的 renders），释放磁盘。"""
    count, size = _clear_storage_dirs()
    logger.info("clear-storage: deleted %d files, %s MB", count, round(size / (1024 * 1024), 2))
    return {"deleted_files": count, "deleted_bytes": size}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "build": "oo-callback-forcesave-v1"}


class ScanDeckRequest(BaseModel):
    file_id: str
    instruction: str | None = None
    # 前端页码追踪：当 instruction 没有明确第X页、或用户说“这一页/当前页”时，用它来定位目标页
    current_slide_index: int | None = None


class ScanDeckResponse(BaseModel):
    patches: List[SlidePatch]


@app.post("/ai/scan-deck", response_model=ScanDeckResponse)
async def ai_scan_deck(req: ScanDeckRequest) -> ScanDeckResponse:
    patches = handle_user_request(
        file_id=req.file_id,
        instruction=req.instruction,
        current_slide_index=req.current_slide_index,
    )
    return ScanDeckResponse(patches=patches)


class PatchesRequest(BaseModel):
    file_id: str
    patches: List[SlidePatch]


class ApplyPatchesResponse(BaseModel):
    version_id: str


@app.post("/ai/apply-patches", response_model=ApplyPatchesResponse)
async def ai_apply_patches(req: PatchesRequest) -> ApplyPatchesResponse:
    version_id = apply_patches_to_onlyoffice(req.file_id, req.patches)
    return ApplyPatchesResponse(version_id=version_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)

