from __future__ import annotations

from typing import List, Dict, Any
from pathlib import Path
import json
import os
import uuid
import re

from models_patch import SlidePatch
from services.suggest import (
    get_suggestion_provider,
    validate_suggestions,
    suggestions_to_dicts,
)
from services.manual_edit import apply_manual_edits_batch
import requests


def _get_objects_path(file_id: str) -> Path:
    backend_dir = Path(__file__).resolve().parent
    objects_dir = backend_dir / "storage" / "objects"
    return (objects_dir / f"{file_id}.json").resolve()


def _get_storage_dir() -> Path:
    return Path(__file__).resolve().parent / "storage"


GENERATE_SLIDE_KEYWORDS = [
    "做一页",
    "生成一页",
    "新建一页",
    "新增一页",
    "加一页",
    "加入一页",
    "插入一页",
    "新页面",
    "新的页面",
    "新的一页",
    "make one slide",
    "generate slide",
    "new slide",
]


def _extract_requested_slide_index(user_input: str) -> int | None:
    """
    从用户输入里提取“指定页码”，转成 0-based slide_index。
    支持：
    - 第1页 / 第 1 页 / 第1張 / 第1张
    - 首页 / 第一页
    - page 1 / slide 1
    """
    s = (user_input or "").strip()
    if not s:
        return None
    lower = s.lower()
    if "首页" in s or "第一页" in s or "first page" in lower:
        return 0
    m = re.search(r"第\s*(\d+)\s*[页張张]", s)
    if m:
        try:
            n = int(m.group(1))
            return max(n - 1, 0)
        except Exception:
            return None
    m = re.search(r"\b(page|slide)\s*(\d+)\b", lower)
    if m:
        try:
            n = int(m.group(2))
            return max(n - 1, 0)
        except Exception:
            return None
    return None


def _extract_insert_index_for_new_slide(user_input: str, slide_count: int) -> int | None:
    """
    解析“新增一页插到哪里”：
    - “在第1页和第2页之间” -> insert_index = 1（0-based，插入到第2页之前）
    - “在第1页后/之后/后面新增一页” -> insert_index = 1
    - “插入到第2页前” -> insert_index = 1

    返回值含义：0-based 插入位置（相当于 list.insert(index, new_slide)）
    """
    text = (user_input or "").strip()
    if not text:
        return None
    lower = text.lower()

    def _cn_num_to_int(s: str) -> int | None:
        s = (s or "").strip()
        if not s:
            return None
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return None
        # 简易中文数字（支持 1-99 常见表达：一二三…十、十一、二十、二十三）
        m0 = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if s in m0:
            return m0[s]
        if s == "十":
            return 10
        if "十" in s:
            parts = s.split("十")
            if len(parts) != 2:
                return None
            left = parts[0].strip()
            right = parts[1].strip()
            tens = m0.get(left, None) if left else 1
            ones = m0.get(right, None) if right else 0
            if tens is None or ones is None:
                return None
            return tens * 10 + ones
        return None

    def _parse_page_num(token: str) -> int | None:
        # token 可能是 “1”/“01”/“一”/“第一”
        t = (token or "").strip()
        if not t:
            return None
        t = re.sub(r"^第", "", t)
        t = re.sub(r"[页張张]$", "", t)
        return _cn_num_to_int(t)

    # between: 第A页和第B页之间 / 在第一和第二页之间 / between page A and page B
    m = re.search(r"第?\s*([0-9一二两三四五六七八九十]+)\s*页?\s*和\s*第?\s*([0-9一二两三四五六七八九十]+)\s*页?\s*之?\s*间", text)
    if m:
        a = _parse_page_num(m.group(1))
        b = _parse_page_num(m.group(2))
        if a is not None and b is not None:
            hi = max(a, b)
            # between page a and b -> insert before page hi (1-based) => index = hi-1
            idx = max(hi - 1, 0)
            return max(0, min(idx, slide_count))
    m = re.search(r"\bbetween\s*(page|slide)\s*(\d+)\s*and\s*(page|slide)\s*(\d+)\b", lower)
    if m:
        try:
            a = int(m.group(2))
            b = int(m.group(4))
            hi = max(a, b)
            idx = max(hi - 1, 0)
            return max(0, min(idx, slide_count))
        except Exception:
            pass

    # after: 在第N页后/之后/后面
    m = re.search(r"第?\s*([0-9一二两三四五六七八九十]+)\s*页?\s*(后|之后|后面)", text)
    if not m:
        m = re.search(r"\bafter\s*(page|slide)\s*(\d+)\b", lower)
    if m:
        if m.re.pattern.startswith("第") or "一二三四五六七八九十" in m.re.pattern:
            n = _parse_page_num(m.group(1))
            if n is None:
                return None
            idx = max(n, 1)
            return max(0, min(idx, slide_count))
        try:
            n = int(m.group(2))
            idx = max(n, 1)
            return max(0, min(idx, slide_count))
        except Exception:
            return None

    # before: 在第N页前/之前
    m = re.search(r"第?\s*([0-9一二两三四五六七八九十]+)\s*页?\s*(前|之前)", text)
    if not m:
        m = re.search(r"\bbefore\s*(page|slide)\s*(\d+)\b", lower)
    if m:
        if m.re.pattern.startswith("第") or "一二三四五六七八九十" in m.re.pattern:
            n = _parse_page_num(m.group(1))
            if n is None:
                return None
            idx = max(n - 1, 0)
            return max(0, min(idx, slide_count))
        try:
            n = int(m.group(2))
            idx = max(n - 1, 0)
            return max(0, min(idx, slide_count))
        except Exception:
            return None

    return None


THIS_PAGE_KEYWORDS = ["这一页", "这页", "本页", "当前页", "这一张", "这张"]


def _resolve_target_slide_index(user_input: str, current_slide_index: int | None) -> int | None:
    """
    解析目标页：
    - 如果用户写了第X页/page X：用解析结果
    - 如果用户说“这一页/当前页”：用 current_slide_index
    - 如果没写页码：也用 current_slide_index（用于“默认修改当前正在看的页”）
    """
    explicit = _extract_requested_slide_index(user_input)
    if explicit is not None:
        return explicit
    text = (user_input or "").strip()
    lower = text.lower()
    if any(k in text for k in THIS_PAGE_KEYWORDS) or "this page" in lower:
        return current_slide_index
    return current_slide_index


def detect_intent(user_input: str) -> str:
    """
    简单可靠的 intent router，不使用模型判断。
    returns: "edit" | "generate_slide"
    """
    text = (user_input or "").strip()
    lower = text.lower()
    # 更稳的规则：优先用正则捕捉“新增一页/新页面”等表达
    # 命中即视为 generate_slide，避免回退到 edit（导致只生成文本框 patch）
    if re.search(r"(加|新增|新建|生成|做|加入|插入)\s*一\s*页", text) or re.search(
        r"(新|新的)\s*(页面|一页)", text
    ):
        print("[GENERATE_SLIDE_INTENT] regex_match")
        return "generate_slide"
    if "slide" in lower and ("new" in lower or "generate" in lower or "make" in lower):
        print("[GENERATE_SLIDE_INTENT] en_regex_match")
        return "generate_slide"
    for kw in GENERATE_SLIDE_KEYWORDS:
        if kw.lower() in lower:
            print(f"[GENERATE_SLIDE_INTENT] kw={kw}")
            return "generate_slide"
    print("[INTENT] edit")
    return "edit"


def _deepseek_generate_single_slide(user_input: str, deck_context: str) -> Dict[str, Any]:
    """
    生成一页 PPT 的结构化内容：
    {
      "title": "",
      "bullets": []
    }
    """
    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    api_base = (os.getenv("DEEPSEEK_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    system_prompt = (
        "You are generating ONE presentation slide.\n\n"
        "Rules:\n\n"
        "1. Always produce structured content.\n"
        "2. Use professional presentation language.\n"
        "3. Keep concise.\n\n"
        "Return JSON:\n\n"
        '{\n  "title":"",\n  "bullets":[]\n}\n'
    )

    user_prompt = (
        f"User request: {user_input}\n\n"
        "Deck context:\n\n"
        f"{deck_context}\n\n"
        "Task:\n\n"
        "Generate ONE slide only.\n\n"
        "Return JSON only."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print(f"[GENERATE_SLIDE] deepseek_call base={api_base} model={model}")
    resp = requests.post(
        f"{api_base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    print(f"[GENERATE_SLIDE] deepseek_content_len={len(content)}")

    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek generate_single_slide must return a JSON object")
    return parsed


def _fallback_single_slide() -> Dict[str, Any]:
    return {
        "title": "Key Insights",
        "bullets": ["Key trend", "Market growth", "Strategic impact"],
    }


def generate_single_slide_flow(user_input: str, objects: List[Dict[str, Any]]) -> List[SlidePatch]:
    """
    从零生成“一页新 PPT”：
    - 不依赖已有文本框
    - DeepSeek 产出 title+bullets
    - 转成一个可执行 patch（add_slide），用户 Accept 一次即可新增一页并写入 title/body
    """
    deck_context = _build_slides_context(objects)
    last_slide_index = 0
    try:
        last_slide_index = max(int(o.get("slide_index", 0) or 0) for o in objects) if objects else 0
    except Exception:
        last_slide_index = 0
    slide_count = int(last_slide_index) + 1 if objects else 0
    insert_index = _extract_insert_index_for_new_slide(user_input, slide_count)
    if insert_index is None:
        # 默认：追加到最后
        insert_index = slide_count
    new_slide_index = int(insert_index)

    title = ""
    bullets: List[str] = []
    try:
        result = _deepseek_generate_single_slide(user_input, deck_context)
        title = str(result.get("title") or "").strip()
        raw_bullets = result.get("bullets")
        if not isinstance(raw_bullets, list):
            raw_bullets = []
        bullets = [str(x).strip() for x in raw_bullets if str(x).strip()]
        if not title or not bullets:
            raise ValueError("invalid slide JSON: title/bullets required")
    except Exception as e:
        print(f"[GENERATE_SLIDE] deepseek_failed -> fallback: {e}")
        fb = _fallback_single_slide()
        title = fb["title"]
        bullets = fb["bullets"]

    after_text = title + "\n\n" + "\n".join([f"• {b}" for b in bullets])
    patch = SlidePatch(
        id=uuid.uuid4().hex,
        slide_index=new_slide_index,
        shape_index=0,
        action="add_slide",
        before="",
        after=after_text,
        description="生成一页新 PPT（title + bullets）",
        payload={"title": title, "bullets": bullets, "insert_index": int(insert_index)},
    )
    print(f"[GENERATE_PATCH] action=add_slide slide_index={patch.slide_index} title_len={len(title)} bullets={len(bullets)}")
    return [patch]


def _build_slides_context(objects: List[Dict[str, Any]], max_chars_per_slide: int = 240) -> str:
    """
    将 objects 聚合成轻量 slides summary，供新增页面生成使用。
    """
    slide_to_texts: Dict[int, List[str]] = {}
    for o in objects:
        try:
            sidx = int(o.get("slide_index"))
        except Exception:
            continue
        t = (o.get("text") or "").strip()
        if not t:
            continue
        slide_to_texts.setdefault(sidx, []).append(t)

    if not slide_to_texts:
        return "(no slide text extracted)"

    lines: List[str] = []
    for sidx in sorted(slide_to_texts.keys()):
        joined = " / ".join(slide_to_texts[sidx])
        if len(joined) > max_chars_per_slide:
            joined = joined[: max_chars_per_slide - 3].rstrip() + "..."
        lines.append(f"- Slide {sidx + 1}: {joined}")
    return "\n".join(lines)


def handle_user_request(
    file_id: str, instruction: str | None = None, current_slide_index: int | None = None
) -> List[SlidePatch]:
    """
    按意图分流到新增页面或文本修改建议。
    """
    objects_path = _get_objects_path(file_id)
    if not objects_path.exists():
        raise FileNotFoundError(f"objects json not found for file_id={file_id}")

    meta: Dict[str, Any] = json.loads(objects_path.read_text(encoding="utf-8"))
    objects: List[Dict[str, Any]] = meta.get("objects", [])
    user_input = (instruction or "").strip()
    if not user_input:
        user_input = "请通读整套 PPT，基于每页的文本内容，提出可以改进文案的建议，并给出修改后的文本。"

    intent = detect_intent(user_input)
    if intent == "generate_slide":
        return generate_single_slide_flow(user_input, objects)
    return scan_deck_for_patches(file_id, user_input, current_slide_index)


def scan_deck_for_patches(
    file_id: str, instruction: str | None = None, current_slide_index: int | None = None
) -> List[SlidePatch]:
    """
    使用 Provider（Mock / DeepSeek）扫描整套 PPT，
    并将建议转换为 SlidePatch 列表，供聊天面板与 ONLYOFFICE 使用。
    """
    objects_path = _get_objects_path(file_id)
    if not objects_path.exists():
        raise FileNotFoundError(f"objects json not found for file_id={file_id}")

    meta: Dict[str, Any] = json.loads(objects_path.read_text(encoding="utf-8"))
    objects: List[Dict[str, Any]] = meta.get("objects", [])

    instr = (instruction or "").strip()
    if not instr:
        instr = "请通读整套 PPT，基于每页的文本内容，提出可以改进文案的建议，并给出修改后的文本。"

    # 若用户指定某一页/这一页/或未指定但有 current，则只返回目标页的 patches
    target_slide = _resolve_target_slide_index(instr, current_slide_index)
    if target_slide is not None:
        print(f"[SCAN] target_slide={target_slide}")

    provider = get_suggestion_provider()
    raw_suggestions = provider.generate(instr, objects)
    print(f"[SCAN] raw_suggestions={len(raw_suggestions)}")
    valid_suggestions = validate_suggestions(raw_suggestions, objects)
    print(f"[SCAN] valid_suggestions={len(valid_suggestions)}")

    # 复用 suggestions_to_dicts 只是为了保持结构一致；真正返回的是 SlidePatch
    dicts = suggestions_to_dicts(valid_suggestions)

    patches: List[SlidePatch] = []
    for item in dicts:
        try:
            slide_index = int(item["slide_index"])
        except Exception:
            continue

        if target_slide is not None and slide_index != int(target_slide):
            continue

        action = str(item.get("action", "edit") or "edit").lower()

        # add_textbox：生成真正的“新增文本框” patch（不再降级为 edit/追加）
        if action == "add_textbox":
            # 找到该页的第一个有文本的对象，作为“锚点”
            anchor_idx = None
            for i, o in enumerate(objects):
                if o.get("slide_index") != slide_index:
                    continue
                text = (o.get("text") or "").strip()
                if text:
                    anchor_idx = i
                    break
            extra = str(item.get("suggested") or "").strip()
            if not extra:
                continue

            patches.append(
                SlidePatch(
                    id=str(item.get("id") or f"p_{len(patches)}"),
                    slide_index=slide_index,
                    # shape_index 仅用于“定位到该页”；真正新增不依赖现有 shape
                    shape_index=anchor_idx if anchor_idx is not None else 0,
                    action="add_textbox",
                    before="",
                    after=extra,
                    description=item.get("reason") or "在该页新增一个文本框。",
                    position=str(item.get("position") or "after_title"),
                )
            )
            continue

        # 默认路径：edit / delete_textbox 等都按 shape_id 精确匹配
        try:
            shape_id = str(item["shape_id"])
            shape_index = next(
                i
                for i, o in enumerate(objects)
                if o.get("slide_index") == slide_index and o.get("shape_id") == shape_id
            )
        except StopIteration:
            continue
        except Exception:
            continue

        if action == "delete_textbox":
            patches.append(
                SlidePatch(
                    id=str(item.get("id") or f"p_{len(patches)}"),
                    slide_index=slide_index,
                    shape_index=shape_index,
                    action="delete_textbox",
                    before=item.get("original") or "",
                    after="",
                    description=item.get("reason") or "删除该文本框。",
                )
            )
        else:
            patches.append(
                SlidePatch(
                    id=str(item.get("id") or f"p_{len(patches)}"),
                    slide_index=slide_index,
                    shape_index=shape_index,
                    action="edit",
                    before=item.get("original") or "",
                    after=item.get("suggested") or "",
                    description=item.get("reason") or "",
                )
            )

    return patches


def apply_patches_to_onlyoffice(file_id: str, patches: List[SlidePatch]) -> str:
    """
    将 SlidePatch 列表写回到 PPTX：
    - 基于 objects/{file_id}.json 找到每个 patch 对应的 shape_id
    - 复用 manual_edit.apply_manual_edits_batch 做批量文本编辑
    - PPTX 实际写回到 storage/outputs/{file_id}.pptx
    - /download 接口会优先使用 outputs/{file_id}.pptx，因此 ONLYOFFICE refresh 后能看到最新内容
    """
    objects_path = _get_objects_path(file_id)
    if not objects_path.exists():
        raise FileNotFoundError(f"objects json not found for file_id={file_id}")

    meta: Dict[str, Any] = json.loads(objects_path.read_text(encoding="utf-8"))
    objects: List[Dict[str, Any]] = meta.get("objects", [])

    # 将 SlidePatch 转为 manual_edit 批量接口需要的结构
    batch_patches: List[Dict[str, Any]] = []
    for p in patches:
        action = (p.action or "edit").lower()

        # add_slide：新增一页 + 写入 title/body（不依赖 shape_id）
        if action == "add_slide":
            payload = p.payload if isinstance(p.payload, dict) else {}
            title = str(payload.get("title") or "").strip()
            bullets = payload.get("bullets")
            insert_index = payload.get("insert_index")
            if not isinstance(bullets, list):
                bullets = []
            bullets = [str(x).strip() for x in bullets if str(x).strip()]
            batch_patches.append(
                {
                    "slide_index": int(p.slide_index),
                    "shape_id": "",
                    "action": "add-slide",
                    "payload": {
                        "title": title,
                        "bullets": bullets,
                        "insert_index": int(insert_index) if isinstance(insert_index, (int, float, str)) and str(insert_index).strip().isdigit() else None,
                    },
                }
            )
            continue

        # add_textbox：真正新增 textbox，不依赖 shape_id
        if action == "add_textbox":
            batch_patches.append(
                {
                    "slide_index": int(p.slide_index),
                    "shape_id": "",  # add-textbox 不需要 shape_id
                    "action": "add-textbox",
                    "payload": {"text": p.after, "position": p.position or "after_title"},
                }
            )
            continue

        # delete_textbox：删除指定 shape
        if action == "delete_textbox":
            try:
                obj = objects[p.shape_index]
            except (IndexError, TypeError):
                continue
            shape_id = str(obj.get("shape_id") or "")
            if not shape_id:
                continue
            batch_patches.append(
                {
                    "slide_index": int(p.slide_index),
                    "shape_id": shape_id,
                    "action": "delete",
                    "payload": {},
                }
            )
            continue

        # edit：修改已有 shape 文本
        try:
            obj = objects[p.shape_index]
        except (IndexError, TypeError):
            continue
        shape_id = str(obj.get("shape_id") or "")
        if not shape_id:
            continue
        batch_patches.append(
            {
                "slide_index": int(p.slide_index),
                "shape_id": shape_id,
                "action": "edit-text",
                "payload": {"text": p.after},
            }
        )

    if not batch_patches:
        # 没有可用的 patch，直接返回当前 file_id 作为“版本号”
        return file_id

    storage_dir = _get_storage_dir()
    apply_manual_edits_batch(
        storage_dir=storage_dir,
        version_id=str(file_id),
        patches=batch_patches,
    )

    # manual_edit 采取“原地更新”策略：版本号不变，前端仍然用 file_id 即可
    return str(file_id)

