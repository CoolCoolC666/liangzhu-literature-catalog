# -*- coding: utf-8 -*-
"""检索增强 E1 的新端点：领域词典可视化编辑 + 词级索引 + 新词发现。

★ 响应信封按契约 §0：成功 `{"ok":true,"data":{...}}`，
  失败 `{"ok":false,"error":"人话说明"}`。

★ **两道权限锁**（契约 §0.1）：
  · 界面层：前端不渲染管理入口（非管理员整张卡都不出现）；
  · 接口层：管理端点一律 `require_admin` —— 手工构造请求也只得到 403。

★ 端点一览
  GET  /api/segment/status    索引状态（**谁都能读**：检索页要显示索引版本）
  GET  /api/segment/dict       读领域词典（管理员）
  POST /api/segment/dict       存领域词典（管理员）
  POST /api/segment/reindex    重建词级索引（管理员）
  POST /api/segment/newwords   发现候选新词（管理员）
  POST /api/segment/adopt      采纳候选入词典（管理员）
  POST /api/segment/reset      把词典恢复成内置初始版（管理员）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header

from . import segment
from .auth import AuthError
from .auth import require_admin as _require_admin
from .db import connect, init_db

# ★ 复用 api_v2 的 ApiError —— app.py 的异常处理器是按**那个类**注册的，
#   自己再定义一个会走 FastAPI 默认的 {"detail": ...} 信封，与契约 §0 不符。
from .api_v2 import ApiError

router = APIRouter(prefix="/api", tags=["segment"])


def require_admin(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    """管理端点的接口层锁。实现沉在 `auth.py`，这里只转换异常类型。"""
    try:
        return _require_admin(x_auth_token)
    except AuthError as e:
        raise ApiError(e.status_code, e.error)


def _fail(status: int, msg: str):
    raise ApiError(status, msg)


# ── 状态（谁都能读）──────────────────────────────────────────────────────
@router.get("/segment/status")
def segment_status():
    """词级索引状态：版本、词典指纹、是否过期、jieba 在不在。

    ★ 检索页要显示"索引版本 vN（词典 … 更新）"——
      词典改了但索引没重建时，这里 `stale=true`，界面必须提示。
    """
    conn = connect()
    try:
        st = segment.index_status(conn)
    finally:
        conn.close()
    return {"ok": True, "data": st}


# ── 词典（管理员）───────────────────────────────────────────────────────
@router.get("/segment/dict")
def segment_dict(user=Depends(require_admin)):
    """读领域词典。"""
    data = segment.load_dict()
    return {"ok": True, "data": {
        "dict": data,
        "path": str(segment.dict_path()),
        "dict_hash": segment.dict_hash(data),
        "builtin_available": True,
    }}


@router.post("/segment/dict")
def segment_dict_save(body: dict, user=Depends(require_admin)):
    """存领域词典。

    ★ 词典是**源数据**，索引是**派生物** —— 这里只写词典，
      索引要不要重建由调用方决定（前端会问一句）。
    ★ 每次保存 `segment_version + 1`，写完 `segment_meta` 才知道对不对得上。
    """
    payload = body.get("dict") if isinstance(body.get("dict"), dict) else body
    if not isinstance(payload, dict):
        _fail(400, "请求体里没有词典内容")
    try:
        saved = segment.save_dict(payload)
    except OSError as e:
        _fail(500, f"词典文件写不进去（{e}）。"
                   "词典文件在程序目录里，若目录只读请改权限后重试。")
    segment.reset_dict_cache()
    return {"ok": True, "data": {
        "dict": saved,
        "dict_hash": segment.dict_hash(saved),
        "segment_version": saved.get("segment_version"),
        "message": "词典已保存。索引需要重建才会生效。",
    }}


@router.post("/segment/reset")
def segment_dict_reset(user=Depends(require_admin)):
    """把词典恢复成内置初始版。

    ★ 动手前先备份当前词典 —— 管理员手滑删了词条要能救回来。
    """
    try:
        current = segment.load_dict()
        segment.save_dict(current)      # → 写一份 .bak
        builtin = segment._normalize_dict(segment._builtin_dict())
        builtin["segment_version"] = int(current.get("segment_version") or 1) + 1
        segment.save_dict(builtin)
    except OSError as e:
        _fail(500, f"恢复失败：{e}")
    segment.reset_dict_cache()
    return {"ok": True, "data": {
        "dict": builtin,
        "dict_hash": segment.dict_hash(builtin),
        "message": "已恢复内置初始词典（上一版存在 .bak 里）。索引需要重建才会生效。",
    }}


# ── 索引（管理员）───────────────────────────────────────────────────────
@router.post("/segment/reindex")
def segment_reindex(user=Depends(require_admin)):
    """重建词级索引。

    ★ 同步执行（实测 201 篇 ~10–70 秒，取决于机器）。
      前端按钮要禁用并显示进度说明，别让用户以为卡死了。
    ★ 没装 jieba 时**不报错** —— 明确告诉用户"检索已退回整句精确模式"。
    """
    conn = connect()
    try:
        init_db(conn)
        rep = segment.rebuild_index(conn)
    finally:
        conn.close()
    if not rep.get("ok"):
        return {"ok": False, "error": rep.get("error") or "重建失败"}
    return {"ok": True, "data": rep}


# ── 新词发现（管理员）───────────────────────────────────────────────────
@router.post("/segment/newwords")
def segment_newwords(body: dict, user=Depends(require_admin)):
    """发现候选新词。

    ★ 结果是**候选，不是结论** —— 收不收由人定。
    ★ 参数一律有上限，别让人把整个语料的 n-gram 全拉出来。
    """
    body = body if isinstance(body, dict) else {}
    try:
        min_count = max(5, min(200, int(body.get("min_count") or segment.MIN_COUNT)))
        topn = max(20, min(1000, int(body.get("topn") or segment.TOPN)))
    except Exception:
        min_count, topn = segment.MIN_COUNT, segment.TOPN
    conn = connect()
    try:
        rows = conn.execute("SELECT content FROM docs_fts").fetchall()
    finally:
        conn.close()
    texts = [r["content"] or "" for r in rows]
    data = segment.load_dict()
    cands = segment.discover_new_words(texts, data=data, min_count=min_count, topn=topn)
    return {"ok": True, "data": {
        "candidates": cands,
        "count": len(cands),
        "min_count": min_count,
        "corpus_docs": len(texts),
        "notice": "候选是候选，不是结论。统计法只能保证「大概率是个词」，"
                  "保证不了「在这个领域里该收」。收不收由人定。",
    }}


@router.post("/segment/adopt")
def segment_adopt(body: dict, user=Depends(require_admin)):
    """把勾选的候选收进领域词典。

    ★ 只接受**明确列出来的词**，不接受「全收」这种含糊指令。
    ★ 候选可能已经被别人删过/改过 → 收之前先查重。
    """
    body = body if isinstance(body, dict) else {}
    words = body.get("words")
    if not isinstance(words, list) or not words:
        _fail(400, "没有勾选任何候选词")
    if len(words) > 200:
        _fail(400, "一次最多收 200 个词")
    group = str(body.get("group") or "人工采纳").strip() or "人工采纳"
    note = str(body.get("note") or "").strip()
    data = segment.load_dict()
    existing = {w["word"] for w in (data.get("words") or [])}
    added, skipped = [], []
    for w in words:
        w = str(w).strip()
        if not w:
            continue
        if w in existing:
            skipped.append(w)
            continue
        data["words"].append({"word": w, "freq": 100000, "pos": "n",
                              "group": group, "note": note})
        existing.add(w)
        added.append(w)
    data["segment_version"] = int(data.get("segment_version") or 1) + 1
    data["updated_at"] = segment.now_str()
    try:
        segment.save_dict(data)
    except OSError as e:
        _fail(500, f"词典文件写不进去（{e}）")
    segment.reset_dict_cache()
    return {"ok": True, "data": {
        "added": added, "skipped": skipped,
        "dict_hash": segment.dict_hash(data),
        "segment_version": data["segment_version"],
        "message": f"已收 {len(added)} 个词。索引需要重建才会生效。",
    }}
