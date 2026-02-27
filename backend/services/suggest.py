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
            "你是一个 PPT 文本编辑助手。"
            "根据用户的自然语言指令，对给定的文本对象生成修改建议。"
            "严格按下面的 JSON 数组格式返回，不要输出多余内容：\n"
            "[\n"
            '  {\n'
            '    "slide_index": <int>,\n'
            '    "shape_id": "<string>",\n'
            '    "original": "<原始文本>",\n'
            '    "suggested": "<建议后的文本>",\n'
            '    "reason": "<简短原因>",\n'
            '    "confidence": <0-1 之间的小数，可选>\n'
            "  }, ...\n"
            "]"
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

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # 如果 LLM 返回的不是纯 JSON，则回退为空列表，交给上层用 Mock 或报错
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
                )
            except Exception:
                continue
            suggestions.append(sug)

        return suggestions


def validate_suggestions(
    suggestions: List[Suggestion], objects: List[Dict[str, Any]]
) -> List[Suggestion]:
    """
    - shape_id 必须存在
    - original 必须与对象 text 有匹配（包含或高相似度，这里用包含/相等简化）
    - suggested 长度限制 <= 1.3 * original
    """
    objects_by_key: Dict[tuple[int, str], Dict[str, Any]] = {
        (o["slide_index"], o["shape_id"]): o for o in objects
    }

    valid: List[Suggestion] = []
    for s in suggestions:
        key = (s.slide_index, s.shape_id)
        obj = objects_by_key.get(key)
        if not obj:
            continue

        text = obj.get("text", "") or ""
        original = s.original or ""
        suggested = s.suggested or ""

        if not original.strip():
            continue
        if original not in text and text not in original:
            continue

        max_len = int(len(original) * 1.3) if original else 0
        if max_len and len(suggested) > max_len:
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
    use_deepseek = os.getenv("USE_DEEPSEEK", "false").lower() == "true"
    if use_deepseek:
        return DeepSeekSuggestionProvider()
    return MockSuggestionProvider()

