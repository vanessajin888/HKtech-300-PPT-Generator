from __future__ import annotations

from typing import Literal
from pydantic import BaseModel


PatchAction = Literal["edit", "add_textbox", "delete_textbox", "add_slide"]


class SlidePatch(BaseModel):
    id: str
    slide_index: int
    shape_index: int
    action: PatchAction
    before: str | None = None
    after: str
    description: str | None = None
    # 仅 add_textbox 使用：用于决定新增文本框的位置策略
    position: str | None = None
    # 可选扩展载荷：用于 add_slide 等结构化 patch
    payload: dict | None = None

