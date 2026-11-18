from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Protocol

import requests
from dotenv import load_dotenv

# 加载 backend/.env，本地存放 DEEPSEEK_API_KEY / USE_DEEPSEEK 等敏感配置
load_dotenv()


@dataclass
class Suggestion:
    id: str
    slide_index: int
    shape_id: str
    original: str
    suggested: str
    reason: str
    confidence: float | None = None
    # 默认是修改已有文本框；
    # - "add_textbox" 表示在对应 slide 新增一个文本框
    # - "delete_textbox" 表示删除一个已有文本框
    action: str = "edit"


class SuggestionProvider(Protocol):
    def generate(
        self, instruction: str, objects: List[Dict[str, Any]]
    ) -> List[Suggestion]:
        ...


class MockSuggestionProvider:
    """
    Mock LLM provider.

    简单规则：
    - 如果指令中包含 "2024" 且 "2026"，则尝试把文本中的 2024 替换为 2026
    - 否则，对含有较长文本的对象给出“更简洁”版本（截断到 50 字左右）
    """

    def generate(
        self, instruction: str, objects: List[Dict[str, Any]]
    ) -> List[Suggestion]:
        suggestions: List[Suggestion] = []
        lower_instr = instruction.lower()

        for obj in objects:
            text = obj.get("text", "") or ""
            slide_index = obj["slide_index"]
            shape_id = obj["shape_id"]

            if not text.strip():
                continue

            suggested = None
            reason = ""

            if "2024" in text and ("2026" in lower_instr or "update" in lower_instr):
                suggested = text.replace("2024", "2026")
                reason = "根据指令，将年份 2024 更新为 2026。"
            elif "简洁" in instruction or "shorter" in lower_instr or len(text) > 80:
                # 简单“更简洁”：截断
                limit = min(len(text), 80)
                suggested = text[:limit].rstrip()
                if len(text) > limit:
                    suggested += "..."
                reason = "根据指令，将该文本压缩得更简洁。"

            if not suggested or suggested == text:
                continue

            sug = Suggestion(
                id=uuid.uuid4().hex,
                slide_index=slide_index,
                shape_id=shape_id,
                original=text,
                suggested=suggested,
                reason=reason,
                confidence=0.8,
            )
            suggestions.append(sug)

        return suggestions


class DeepSeekSuggestionProvider:
    """
    使用 DeepSeek 真实 LLM 生成建议。

    说明：
    - API Key 从环境变量 / backend/.env 中读取：DEEPSEEK_API_KEY
    - 如需启用，在 backend/.env 中设置 USE_DEEPSEEK=true
    - 这里假设 DeepSeek 提供 OpenAI Chat Completions 兼容接口，
      如果实际接口不同，可根据官方文档调整 endpoint 和 payload。
    """

    def __init__(self) -> None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Please configure it in backend/.env."
            )
        self.api_key = api_key
        # 默认使用官方 OpenAI 兼容路径，如有变化可在此修改
        self.api_base = os.getenv(
            "DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"
        )
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    def generate(
        self, instruction: str, objects: List[Dict[str, Any]]
    ) -> List[Suggestion]:
        print(f"[DEEPSEEK] called objects={len(objects)} instruction_len={len(instruction or '')}")
        # 为了降低 prompt 长度，只把必要字段发给 LLM
        minimal_objects = [
            {
                "slide_index": o["slide_index"],
                "shape_id": o["shape_id"],
                "text": o.get("text", ""),
            }
            for o in objects
        ]

        system_prompt = (
            "你是一个专门帮用户修改 PPT 文本内容的编辑助手。\n"
            "用户会给你一条自然语言指令（例如：更新年份、改措辞、更简洁、统一语气等），"
            "以及每个文本框的原始内容（仅包含 slide_index / shape_id / text）。\n\n"
            "你的目标：\n"
            "1. 在**尽量保留原意**的前提下，根据指令给出更好的文本版本，可以包含：\n"
            "   - 年份或数字的更新（例如 2024 → 2026）\n"
            "   - 语气和措辞的优化（更正式、更清晰、更有说服力等）\n"
            "   - 要点合并或精简，使要点更简洁、有结构（但不要随意丢失关键信息）\n"
            "2. 可以在某些页面上**新增一个文本框**，例如添加总结性一句话或新的要点，但请保持数量克制。\n"
            "3. 可以根据指令**删除某个文本框**（例如“删掉这一页右下角的免责声明”）。\n"
            "4. 每条建议只针对一个文本框（shape_id），不要跨文本框合并；新增文本框时只绑定 slide_index。\n"
            "5. 如果某个文本不需要修改，可以不返回该条。\n\n"
            "输出要求（非常重要）：\n"
            "- **只输出 JSON 数组本身**，不要任何解释性文字，不要 Markdown。\n"
            "- 数组中的每个元素必须严格符合下面的结构：\n"
            "[\n"
            "  {\n"
            '    "slide_index": <int>,\n'
            '    "shape_id": "<string>",\n'
            '    "original": "<原始文本，如果是新增文本框则可以是空字符串>",\n'
            '    "suggested": "<根据指令优化后的文本 或 新增文本框的内容>",\n'
            '    "reason": "<1~2 句中文，总结你对这段文本做了什么修改>",\n'
            '    "confidence": <0-1 之间的小数，可选>,\n'
            '    "action": "<可选，\"edit\"、\"add_textbox\" 或 \"delete_textbox\">"\n'
            "  }, ...\n"
            "]\n"
            "请务必保证：\n"
            "- 所有 slide_index / shape_id 都来自输入 objects，不要编造新的。\n"
            "- 当 action 缺省或为 \"edit\" / \"delete_textbox\" 时：original 字段必须与对应对象的 text 高度一致（包括主要内容）。\n"
            "- 当 action 为 \"add_textbox\" 时：shape_id 可以写任意占位字符串，original 可以留空，suggested 为新增文本框内容。\n"
        )

        user_prompt = {
            "instruction": instruction,
            "objects": minimal_objects,
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_prompt, ensure_ascii=False),
                },
            ],
            "temperature": 0.3,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # 解析 LLM 返回的 JSON 内容
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        print(f"[DEEPSEEK] response_content_len={len(content)}")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # 如果 LLM 返回的不是纯 JSON，则回退为空列表，交给上层用 Mock 或报错
            print("[DEEPSEEK] json_parse_failed -> []")
            return []

        suggestions: List[Suggestion] = []
        for item in parsed:
            try:
                sug = Suggestion(
                    id=uuid.uuid4().hex,
                    slide_index=int(item["slide_index"]),
                    shape_id=str(item["shape_id"]),
                    original=str(item["original"]),
                    suggested=str(item["suggested"]),
                    reason=str(item.get("reason", "")),
                    confidence=float(item.get("confidence"))
                    if item.get("confidence") is not None
                    else None,
                    action=str(item.get("action", "edit")),
                )
            except Exception:
                continue
            suggestions.append(sug)

        print(f"[DEEPSEEK] parsed_suggestions={len(suggestions)}")
        return suggestions


def validate_suggestions(
    suggestions: List[Suggestion], objects: List[Dict[str, Any]]
) -> List[Suggestion]:
    """
    - shape_id 必须存在
    - original 必须与对象 text 高度一致（这里要求相等或差异很小）
    - suggested 长度限制适度放宽：允许在 [0.3, 2.0] 倍 original 之间
    """
    objects_by_key: Dict[tuple[int, str], Dict[str, Any]] = {
        (o["slide_index"], o["shape_id"]): o for o in objects
    }
    slide_indices = {o["slide_index"] for o in objects}

    valid: List[Suggestion] = []
    for s in suggestions:
        action = (s.action or "edit").lower()

        # 新增文本框：只校验 slide_index 合理 + 文本非空
        if action == "add_textbox":
            suggested = (s.suggested or "").strip()
            if not suggested:
                continue
            if s.slide_index not in slide_indices:
                continue
            valid.append(s)
            continue

        # 其余（edit / delete_textbox）都需要匹配已有对象
        key = (s.slide_index, s.shape_id)
        obj = objects_by_key.get(key)
        if not obj:
            continue

        text = (obj.get("text", "") or "").strip()
        original = (s.original or "").strip()
        suggested = (s.suggested or "").strip()

        # original 必须非空
        if not original:
            continue

        # 要求 original 与对象文本高度一致：
        # - 允许首尾空白差异
        # - 为了兼容少量标点差异，放宽为“长度相近且有较大部分相同”
        if original != text:
            # 长度差距过大直接丢弃
            if not text or abs(len(original) - len(text)) > max(len(text), len(original)) * 0.2:
                continue
            # 简单 token 交集检查
            orig_tokens = set(original.replace("\n", " ").split())
            text_tokens = set(text.replace("\n", " ").split())
            if not orig_tokens or not text_tokens:
                continue
            overlap = len(orig_tokens & text_tokens) / max(len(orig_tokens), 1)
            if overlap < 0.5:
                continue

        # delete_textbox：允许 suggested 为空（会被当作“清空文本框”处理）
        if action == "delete_textbox":
            valid.append(s)
            continue

        # edit：suggested 必须存在且长度在 0.3x ~ 2.0x 之间
        if not suggested:
            continue
        min_len = int(len(original) * 0.3)
        max_len = int(len(original) * 2.0)
        if len(original) > 0:
            if len(suggested) < max(min_len, 1) or len(suggested) > max_len:
                continue

        valid.append(s)

    return valid


def suggestions_to_dicts(suggestions: List[Suggestion]) -> List[Dict[str, Any]]:
    return [asdict(s) for s in suggestions]


def get_suggestion_provider() -> SuggestionProvider:
    """
    根据环境变量决定使用 Mock 还是 DeepSeek。

    - USE_DEEPSEEK=true 且 DEEPSEEK_API_KEY 有效时，使用 DeepSeekSuggestionProvider
    - 否则使用 MockSuggestionProvider

    注意：如果 USE_DEEPSEEK=true 但 DeepSeek 初始化失败，会直接抛出异常，
    这样你能在日志中清楚看到是 DeepSeek 配置问题，而不是静默退回 Mock。
    """
    # 强制模式：确保每次都走 DeepSeek（不允许静默回退）
    force_deepseek = os.getenv("FORCE_DEEPSEEK", "false").lower() == "true"
    use_deepseek = os.getenv("USE_DEEPSEEK", "false").lower() == "true"

    if force_deepseek or use_deepseek:
        provider = DeepSeekSuggestionProvider()
        print(
            "[SUGGEST] provider=deepseek "
            f"model={getattr(provider, 'model', '')} base={getattr(provider, 'api_base', '')}"
        )
        return provider

    print("[SUGGEST] provider=mock")
    return MockSuggestionProvider()

