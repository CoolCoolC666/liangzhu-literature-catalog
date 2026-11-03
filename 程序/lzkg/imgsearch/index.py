# -*- coding: utf-8 -*-
"""图版索引：建表 + 灌库 + 状态（版本 / 指纹 / 是否过期）。

★ 索引是**派生物**，登记表是**源数据**：
  库里记一份 source_sig（源数据指纹），指纹一变就 stale=true，界面提示重建。
  （与 E1 的 segment_meta 同一套思路，各自独立。）

★ 为什么用 FTS5 **trigram**：与 docs_fts 同款，**子串友好**。
  图版检索里两字词极常见（「玉琮」「城墙」「水坝」「神人」），
  trigram 索引不了两字词 → 由 query.py 走 LIKE 兜底（img_fts 只有几千行，扫得很快）。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time

from . import clean

IMG_SCHEMA = """
-- 图版文本索引（**独立表**，不碰 docs_fts / docs_seg / raw_json_fts）
CREATE VIRTUAL TABLE IF NOT EXISTS img_fts USING fts5(
    caption_zh, caption_en, ai_desc, ai_objects, fig_no, tags_text,
    img_id UNINDEXED, lit_id UNINDEXED,
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS img_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: 参与文本检索的层（mode=exact）；mode=both 再加 tags_text
TEXT_LAYERS = ("caption_zh", "caption_en", "ai_desc", "ai_objects", "fig_no")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。**建表不等于建索引** —— 内容要 build_index 灌。"""
    conn.executescript(IMG_SCHEMA)
    conn.commit()


def _tags_by_image(conn: sqlite3.Connection) -> dict:
    """img_id → 标签名列表（走 img_tags + tags 的规范化倒排，不自己拼字符串）。"""
    out = {}
    try:
        rows = conn.execute(
            "SELECT it.img_id, t.name FROM img_tags it "
            "LEFT JOIN tags t ON t.tag_id = it.tag_id").fetchall()
    except sqlite3.Error:
        return out
    for img_id, name in rows:
        if not img_id or not name:
            continue
        out.setdefault(img_id, []).append(str(name))
    return out


def source_rows(conn: sqlite3.Connection) -> list:
    """从 images + img_tags 读出待索引的原始行（**只读**，不写库）。"""
    tags = _tags_by_image(conn)
    rows = []
    for r in conn.execute(
            "SELECT img_id, lit_id, caption, ai_desc, ai_objects, fig_no "
            "FROM images ORDER BY img_id"):
        img_id = r["img_id"] if isinstance(r, sqlite3.Row) else r[0]
        lit_id = r["lit_id"] if isinstance(r, sqlite3.Row) else r[1]
        caption = r["caption"] if isinstance(r, sqlite3.Row) else r[2]
        ai_desc = r["ai_desc"] if isinstance(r, sqlite3.Row) else r[3]
        ai_objects = r["ai_objects"] if isinstance(r, sqlite3.Row) else r[4]
        fig_no = r["fig_no"] if isinstance(r, sqlite3.Row) else r[5]
        zh, en = clean.split_caption(caption or "")
        rows.append({
            "img_id": img_id,
            "lit_id": lit_id or "",
            "caption_zh": zh,
            "caption_en": en,
            "ai_desc": clean.nfkc(ai_desc or ""),
            "ai_objects": clean.clean_ai_objects(ai_objects or ""),
            "fig_no": clean.clean_fig_no(fig_no or ""),
            "tags_text": clean.join_tags(tags.get(img_id, [])),
            "ai_generic": clean.is_generic_ai(ai_desc or ""),
        })
    return rows


def source_sig(rows: list) -> str:
    """源数据指纹：内容变了就 stale。"""
    h = hashlib.sha256()
    for r in rows:
        for k in ("img_id", "caption_zh", "caption_en", "ai_desc", "ai_objects",
                  "fig_no", "tags_text"):
            h.update(str(r.get(k) or "").encode("utf-8"))
            h.update(b"\x1f")
        h.update(b"\x1e")
    return h.hexdigest()[:16]


def build_index(conn: sqlite3.Connection) -> dict:
    """重建图版索引（幂等：先清空再灌）。"""
    ensure_schema(conn)
    rows = source_rows(conn)
    conn.execute("DELETE FROM img_fts")
    conn.executemany(
        "INSERT INTO img_fts (caption_zh, caption_en, ai_desc, ai_objects, fig_no, "
        "tags_text, img_id, lit_id) VALUES (?,?,?,?,?,?,?,?)",
        [(r["caption_zh"], r["caption_en"], r["ai_desc"], r["ai_objects"], r["fig_no"],
          r["tags_text"], r["img_id"], r["lit_id"]) for r in rows])
    sig = source_sig(rows)
    ver = int(get_meta(conn, "img_index_version", "0") or 0) + 1
    generic = sum(1 for r in rows if r["ai_generic"])
    set_meta(conn, "img_index_version", str(ver))
    set_meta(conn, "img_index_rows", str(len(rows)))
    set_meta(conn, "img_index_built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    set_meta(conn, "img_index_sig", sig)
    set_meta(conn, "img_index_generic", str(generic))
    conn.commit()
    return {"ok": True, "rows": len(rows), "version": ver, "sig": sig,
            "generic_ai": generic}


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        r = conn.execute("SELECT value FROM img_index_meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    if not r:
        return default
    return r[0] if r[0] is not None else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO img_index_meta (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, str(value)))


def status(conn: sqlite3.Connection) -> dict:
    """索引状态：行数 / 版本 / 指纹 / 是否过期。"""
    ensure_schema(conn)
    cur = conn.execute("SELECT count(*) FROM img_fts").fetchone()
    actual = cur[0] if cur else 0
    sig = get_meta(conn, "img_index_sig", "")
    try:
        now_sig = source_sig(source_rows(conn))
    except sqlite3.Error:
        now_sig = sig
    return {
        "rows": actual,
        "meta_rows": int(get_meta(conn, "img_index_rows", "0") or 0),
        "version": int(get_meta(conn, "img_index_version", "0") or 0),
        "built_at": get_meta(conn, "img_index_built_at", ""),
        "sig": sig,
        "source_sig": now_sig,
        "stale": bool(sig and now_sig and sig != now_sig),
        "generic_ai": int(get_meta(conn, "img_index_generic", "0") or 0),
        "built": actual > 0,
    }
