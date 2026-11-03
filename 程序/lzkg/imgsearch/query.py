# -*- coding: utf-8 -*-
"""图版检索：分词、匹配、片段、命中层级。

★ 两级降级（与文献检索同一套**道理**，但**代码各写各的**）
  · 全部 token ≥ 3 字符 → FTS5 MATCH（trigram，子串语义）
  · 只要有一个 token < 3 字符（「玉琮」「城墙」这种两字词）→ 整条退回 LIKE 扫
    （混合查询按最长的短板决定：不能半边走索引半边扫描）

★ mode 的含义（**只**这两种，不与「走 FTS5 还是 LIKE」混用）：
  · exact —— 只匹配图版**自身文本**（图注中英 / AI 描述 / AI 物体 / 图号）
  · both  —— 再加上**标签文本**（四维标签 + 类型标签）
  精确层实际走 fts5 还是 like，由 exact_backend 字段单独说明（E1 的教训：一字段一词表）。

★ 复核状态**只显示、不影响检索**：不做过滤、不做降权、不做排序权重。
"""
from __future__ import annotations

import html
import re
import sqlite3
from urllib.parse import quote

from . import clean

#: trigram 的最小匹配长度（与 docs_fts 同一个门槛）
MIN_TOKEN = 3

#: mode=exact 参与匹配的文本列
TEXT_COLS = ["caption_zh", "caption_en", "ai_desc", "ai_objects", "fig_no"]
#: mode=both 额外参与匹配的列
TAG_COL = "tags_text"

#: 复核状态里算「已核实」的几种写法（**只用于显示徽标**）
VERIFIED_MARKERS = ("已人工核实", "REV-PASS")

_SPLIT_RE = re.compile(r"[\s,，、;；]+")
_STRIP_RE = re.compile(r"^[\.\.。、:：\"'“”‘’()（）\[\]【】<>《》]+|[\.\.。、:：\"'“”‘’()（）\[\]【】<>《》]+$")


def tokenize(q: str) -> list:
    """把查询串切成词。中文按空格/标点切；英文同样。"""
    s = clean.nfkc(q or "")
    out = []
    for p in _SPLIT_RE.split(s):
        p = _STRIP_RE.sub("", p.strip())
        if p:
            out.append(p)
    return out


def _like_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_expr(tokens: list, mode: str) -> str:
    """FTS5 MATCH 表达式。exact 用列过滤把标签层排除掉。"""
    if mode == "exact":
        colf = "{" + " ".join(TEXT_COLS) + "}"
        return " AND ".join(colf + ' : "' + t.replace('"', '""') + '"' for t in tokens)
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _like_clause(tokens: list, cols: list) -> tuple:
    """每个词都要在**任一**列命中（与词之间是 AND）。"""
    where = []
    params = []
    for t in tokens:
        parts = []
        for c in cols:
            # ★ 必须带表名限定：img_fts 与 images 都有 ai_desc / ai_objects，
            #   不限定 → SQLite 报 ambiguous column name（实测踩过）。
            parts.append("img_fts." + c + " LIKE ? ESCAPE '\\'")
            params.append("%" + _like_escape(t) + "%")
        where.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(where), params


def _filters(img_type: str, obj_type: str, dim: str, tag: str, lit_id: str):
    """筛选条件（打在 images / img_tags 上）。"""
    where = []
    params = []
    if img_type:
        where.append("i.img_type = ?")
        params.append(img_type)
    if obj_type:
        where.append("i.obj_type = ?")
        params.append(obj_type)
    if lit_id:
        where.append("i.lit_id = ?")
        params.append(lit_id)
    if dim and tag:
        where.append("EXISTS (SELECT 1 FROM img_tags it JOIN tags t "
                     "ON t.tag_id = it.tag_id WHERE it.img_id = i.img_id "
                     "AND it.dim = ? AND t.name = ?)")
        params.extend([dim, tag])
    return where, params


def _hit_layers(row, tokens: list, cols: list) -> list:
    """命中层级：逐层判断词是否真的出现在这一层里。"""
    hits = []
    for c in cols:
        v = str(row[c] or "").lower()
        if not v:
            continue
        for t in tokens:
            if t.lower() in v:
                hits.append(c)
                break
    return hits


def _snippet(text: str, tokens: list, width: int = 120) -> str:
    """截一段带 <mark> 的片段（服务端自己截，HTML 先转义再打标记）。"""
    if not text:
        return ""
    low = text.lower()
    pos = -1
    hit = ""
    for t in tokens:
        p = low.find(t.lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos, hit = p, t
    if pos < 0:
        return html.escape(text[:width], quote=True)
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    s = html.escape(text[start:pos], quote=True)
    m = html.escape(text[pos:pos + len(hit)], quote=True)
    e = html.escape(text[pos + len(hit):end], quote=True)
    pre = "…" if start > 0 else ""
    post = "…" if end < len(text) else ""
    return pre + s + "<mark>" + m + "</mark>" + e + post


def _thumb(path: str, filename: str) -> str:
    """站内缩略图 URL（只给文件名，不外泄本机路径）。"""
    name = str(path or "").replace("\\", "/").split("/")[-1]
    if not name:
        name = str(filename or "")
    if not name:
        return ""
    return "/media/img/" + quote(name)


def _verified(status: str) -> bool:
    """这条图版是否**已人工核实**（**只用于显示徽标**，不影响检索）。"""
    s = str(status or "")
    return any(m in s for m in VERIFIED_MARKERS)


def search(conn: sqlite3.Connection, q: str = "", mode: str = "both",
           img_type: str = "", obj_type: str = "", dim: str = "", tag: str = "",
           lit_id: str = "", page: int = 1, size: int = 20) -> dict:
    """检索图版。返回 {total, page, size, items, mode, exact_backend, terms}。"""
    from . import index
    index.ensure_schema(conn)
    mode = mode if mode in ("exact", "both") else "both"
    tokens = tokenize(q)
    cols = list(TEXT_COLS) + ([TAG_COL] if mode == "both" else [])
    where = []
    params = []
    backend = "none"
    if tokens:
        if any(len(t) < MIN_TOKEN for t in tokens):
            backend = "like"
            lc, lp = _like_clause(tokens, cols)
            where.append("(" + lc + ")")
            params.extend(lp)
        else:
            backend = "fts5"
            where.append("img_fts MATCH ?")
            params.append(_match_expr(tokens, mode))
    fw, fp = _filters(img_type, obj_type, dim, tag, lit_id)
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where) if where else "1=1"
    base = ("FROM img_fts JOIN images i ON i.img_id = img_fts.img_id ")
    total = conn.execute("SELECT count(*) " + base + " WHERE " + where_sql, params).fetchone()[0]
    page = max(1, int(page or 1))
    size = max(1, min(100, int(size or 20)))
    cols_sql = ("i.img_id, i.lit_id, i.fig_no, i.img_title, i.caption, i.ai_desc, "
                "i.ai_objects, i.img_type, i.obj_type, i.review_status, "
                "i.filename, i.path, img_fts.caption_zh, img_fts.caption_en, "
                "img_fts.tags_text")
    rows = conn.execute("SELECT " + cols_sql + " " + base + " WHERE " + where_sql +
                        " ORDER BY i.lit_id, i.img_id LIMIT ? OFFSET ?",
                        params + [size, (page - 1) * size]).fetchall()
    items = []
    for r in rows:
        row = dict(r)
        layers = _hit_layers(row, tokens, cols) if tokens else []
        src = ""
        for c in layers:
            if row.get(c):
                src = str(row[c])
                break
        if not src:
            src = str(row.get("caption") or "")
        items.append({
            "img_id": row["img_id"],
            "lit_id": row["lit_id"],
            "fig_no": row["fig_no"],
            "img_title": row["img_title"],
            "caption": row["caption"],
            "caption_zh": row.get("caption_zh") or "",
            "caption_en": row.get("caption_en") or "",
            "tags_text": row.get("tags_text") or "",
            "ai_desc": row["ai_desc"],
            "ai_objects": row["ai_objects"],
            "img_type": row["img_type"],
            "obj_type": row["obj_type"],
            "review_status": row["review_status"],
            "review_verified": _verified(row["review_status"]),
            "ai_generic": clean.is_generic_ai(row["ai_desc"] or ""),
            "filename": row["filename"],
            "thumb_url": _thumb(row["path"], row["filename"]),
            "hit_layers": layers,
            "snippet": _snippet(src, tokens) if tokens else "",
        })
    return {"total": total, "page": page, "size": size, "items": items,
            "mode": mode, "exact_backend": backend, "terms": tokens}


def related(conn: sqlite3.Connection, img_id: str, limit: int = 12) -> dict:
    """相关图版（E2-1）：同文献优先，其次同标签。**只读标签倒排，不建第二套索引**。"""
    from . import index
    index.ensure_schema(conn)
    row = conn.execute("SELECT lit_id FROM images WHERE img_id = ?", (img_id,)).fetchone()
    if not row:
        return {"total": 0, "items": []}
    lit_id = row[0]
    limit = max(1, min(60, int(limit or 12)))
    sql = ("SELECT i.img_id, i.lit_id, i.fig_no, i.img_title, i.caption, i.img_type, "
           "i.obj_type, i.review_status, i.filename, i.path, "
           "(SELECT count(*) FROM img_tags x JOIN img_tags y ON x.tag_id = y.tag_id "
           " WHERE x.img_id = i.img_id AND y.img_id = ?) AS shared "
           "FROM images i WHERE i.img_id <> ? "
           "AND (i.lit_id = ? OR EXISTS (SELECT 1 FROM img_tags z WHERE z.img_id = i.img_id "
           "AND z.tag_id IN (SELECT tag_id FROM img_tags WHERE img_id = ?))) "
           "ORDER BY (i.lit_id = ?) DESC, shared DESC, i.lit_id, i.img_id LIMIT ?")
    rows = conn.execute(sql, (img_id, img_id, lit_id, img_id, lit_id, limit)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        items.append({"img_id": d["img_id"], "lit_id": d["lit_id"],
                      "fig_no": d["fig_no"], "img_title": d["img_title"],
                      "caption": d["caption"], "img_type": d["img_type"],
                      "obj_type": d["obj_type"],
                      "review_status": d["review_status"],
                      "review_verified": _verified(d["review_status"]),
                      "filename": d["filename"],
                      "thumb_url": _thumb(d["path"], d["filename"]),
                      "shared": d["shared"]})
    return {"total": len(items), "items": items}


def tag_options(conn: sqlite3.Connection) -> dict:
    """每个维度的标签选项（图版检索的筛选下拉用）。"""
    out = {}
    try:
        rows = conn.execute(
            "SELECT it.dim, t.name, count(DISTINCT it.img_id) FROM img_tags it "
            "JOIN tags t ON t.tag_id = it.tag_id GROUP BY it.dim, t.name "
            "ORDER BY 3 DESC").fetchall()
    except sqlite3.Error:
        return out
    for dim, name, n in rows:
        out.setdefault(dim, []).append({"k": name, "n": n})
    return out


def stats(conn: sqlite3.Connection) -> dict:
    """筛选用的选项 + 复核分布（复核**只统计与显示**，不参与检索过滤）。"""
    from . import index
    st = index.status(conn)
    def grp(sql):
        return [{"k": r[0], "n": r[1]} for r in conn.execute(sql)]
    return {
        "index": st,
        "img_types": grp("SELECT COALESCE(img_type,'(未分类)'), count(*) FROM images "
                         "GROUP BY 1 ORDER BY 2 DESC"),
        "obj_types": grp("SELECT COALESCE(obj_type,'(未分类)'), count(*) FROM images "
                         "GROUP BY 1 ORDER BY 2 DESC"),
        "dims": grp("SELECT dim, count(DISTINCT tag_id) FROM img_tags GROUP BY 1"),
        "tag_options": tag_options(conn),
        "review": grp("SELECT COALESCE(review_status,'(未标注)'), count(*) FROM images "
                       "GROUP BY 1 ORDER BY 2 DESC"),
        "total_images": conn.execute("SELECT count(*) FROM images").fetchone()[0],
        "total_lits": conn.execute("SELECT count(DISTINCT lit_id) FROM images").fetchone()[0],
    }
