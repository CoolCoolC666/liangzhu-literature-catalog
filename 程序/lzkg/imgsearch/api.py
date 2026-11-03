# -*- coding: utf-8 -*-
"""图像检索 E2 的端点（独立前缀 /api/imgsearch）。

★ 端点一览
  GET  /api/imgsearch/search   检索图版（谁都能读）
  GET  /api/imgsearch/stats    索引状态 + 筛选选项 + 复核分布（谁都能读）
  GET  /api/imgsearch/related  相关图版（E2-1，谁都能读）
  POST /api/imgsearch/reindex  重建图版索引（**管理员**）

★ 响应信封按契约 §0：成功 {ok:true, data:{...}}，失败 {ok:false, error:"人话说明"}。

★ 复核状态在这里**只做显示**：
  端点不按复核状态过滤，也不按它排序 —— 复核归复核，检索归检索。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from . import index, query
from ..auth import AuthError
from ..auth import require_admin as _require_admin

# ★ 复用 api_v2 的 ApiError —— app.py 的异常处理器是按**那个类**注册的，
#   自己再定义一个会走 FastAPI 默认的 {detail:...} 信封，与契约 §0 不符。
from ..api_v2 import ApiError

from ..db import connect

router = APIRouter(prefix="/api/imgsearch", tags=["imgsearch"])


def require_admin(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    """管理端点的接口层锁。实现沉在 auth.py，这里只转换异常类型。"""
    try:
        return _require_admin(x_auth_token)
    except AuthError as e:
        raise ApiError(e.status_code, e.error)


@router.get("/search")
def imgsearch(q: str = "", mode: str = "both", img_type: str = "",
              obj_type: str = "", dim: str = "", tag: str = "",
              lit_id: str = "", page: int = 1, size: int = 20):
    """检索图版。

    ★ mode 只有两种：exact（图版自身文本）/ both（再加标签层）。
    ★ 精确层走 fts5 还是 like，由返回里的 exact_backend 说明。
    ★ 复核状态**不参与**过滤与排序，只在结果里带着显示。
    """
    conn = connect()
    try:
        data = query.search(conn, q=q, mode=mode, img_type=img_type,
                           obj_type=obj_type, dim=dim, tag=tag, lit_id=lit_id,
                           page=page, size=size)
        data["index"] = index.status(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.get("/stats")
def imgsearch_stats():
    """索引状态 + 筛选选项 + 复核分布。"""
    conn = connect()
    try:
        return {"ok": True, "data": query.stats(conn)}
    finally:
        conn.close()


@router.get("/related")
def imgsearch_related(img_id: str = "", limit: int = 12):
    """相关图版（E2-1）：同文献优先，其次同标签。"""
    if not img_id:
        raise ApiError(400, "缺少 img_id")
    conn = connect()
    try:
        return {"ok": True, "data": query.related(conn, img_id, limit)}
    finally:
        conn.close()


@router.post("/reindex")
def imgsearch_reindex(user=Depends(require_admin)):
    """重建图版索引（管理员）。索引是派生物，重建随时可做。"""
    conn = connect()
    try:
        rep = index.build_index(conn)
        return {"ok": True, "data": rep}
    finally:
        conn.close()
