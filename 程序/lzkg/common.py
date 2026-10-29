# -*- coding: utf-8 -*-
"""新端点共用的 HTTP 小工具（响应信封 / RFC 5987 附件头 / 时间）。

★ 统一响应信封（接口契约 v2 §0）：
    成功 → {"ok": true, "data": {...}}
    失败 → {"ok": false, "error": "人话说明"}（HTTP 状态码仍按语义给）
旧端点（/api/overview 等）保持原样不动 —— **不改既有行为**，只约束新端点。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

CST = timezone(timedelta(hours=8))


def now_str() -> str:
    """本地时间字符串：契约要求 "YYYY-MM-DD HH:MM:SS"。"""
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def ok(data=None, **extra) -> dict:
    """成功信封。"""
    body = {"ok": True, "data": data if data is not None else {}}
    if extra:
        body["data"].update(extra) if isinstance(body["data"], dict) else None
    return body


def attachment_headers(cn_name: str, ascii_name: str) -> dict:
    """RFC 5987 附件头。

    ⚠️ HTTP 头只能放 latin-1 字符，中文文件名直接写进去会抛
    `UnicodeEncodeError: 'latin-1' codec can't encode characters`。
    """
    return {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cn_name)}"
    }
