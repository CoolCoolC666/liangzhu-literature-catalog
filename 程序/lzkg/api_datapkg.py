# -*- coding: utf-8 -*-
"""数据包状态端点（谁都能读）。

★ 只报状态，**不做导入**：导入的动作是「把文件夹拖进 `数据包/`」，
  程序侧只需要**认出来** + **说清楚装齐了没有**。
  → 没有管理员端点（没有写操作，就不需要第二道锁）。
"""
from __future__ import annotations

from fastapi import APIRouter

from .db import connect
from . import datapkg

router = APIRouter(prefix="/api/datapackage", tags=["datapackage"])


@router.get("/status")
def datapackage_status():
    """数据包状态：装没装、齐不齐、解锁了哪些功能。"""
    conn = connect()
    try:
        return {"ok": True, "data": datapkg.status(conn)}
    finally:
        conn.close()
