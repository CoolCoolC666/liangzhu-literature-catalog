# -*- coding: utf-8 -*-
"""FastAPI 服务：良渚文化文献目录站。

端点严格按 `_docs/数据契约.md` §2 实现；前端不得假设契约之外的字段。
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from .config import get_config
from .db import (MIN_FTS_LEN, connect, count, fts_query, get_meta,
                 like_clause, needs_like)
from .graph import (REL_LABELS, analyze, build_graph_into_db, crop_scope,
                     degree_map, evidence_for, load_graph)
from .nbkg import nbkg_export_rows

CST = timezone(timedelta(hours=8))

# ★ 公网开放时：**不要**暴露 Swagger/OpenAPI（= 一张完整接口地图），
#   而且要明确告诉搜索引擎「别收录」——公网仅成员内部开放，内容放开但爬虫必须挡在门外。
app = FastAPI(title="良渚文化文献目录站", version="1.0",
              description="宁波地域文明探源工程 · 良渚文化专题 · 课程作业",
              docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _noindex_headers(request: Request, call_next):
    """所有响应加 noindex 头：内部资料一旦被收录 = 等于公开泄露。"""
    resp = await call_next(request)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n",
                             media_type="text/plain; charset=utf-8")


def db() -> sqlite3.Connection:
    return connect()


def log_access(action: str, target: str = "", detail: str = "",
               user_id: int | None = None, ip: str = "") -> None:
    """访问留痕：公网仅成员开放时，内部协作最怕的不是外泄，是「不知道谁改的」。"""
    try:
        c = connect()
        c.execute(
            "INSERT INTO access_log (user_id, action, target, detail, ip, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, action, target, detail, (ip or "")[:64],
             datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        c.close()
    except Exception:
        pass    # 留痕失败不能拖垮主流程


# ── 静态与首页 ──────────────────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"


@app.get("/", response_class=HTMLResponse)
def index():
    f = STATIC_DIR / "index.html"
    if not f.exists():
        return HTMLResponse(
            "<h1>良渚文化文献目录站</h1>"
            "<p>前端文件缺失：lzkg/web/static/index.html</p>"
            "<p>请检查程序目录是否完整。</p>", status_code=200)
    # ★ 首页**不缓存**：前端是单文件、经常改，
    #   浏览器缓存住旧版会让「我明明改了却没变」，很难查。
    return HTMLResponse(f.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store, must-revalidate"})


# ── 离线底图（GeoJSON）─────────────────────────────────────────────────
# 前端交互地图要自己画底图 → 必须能直接取到 GeoJSON 文件。
# ★ 不用 StaticFiles 整目录挂载：那样会把 data/ 下的库文件一起暴露出去。
#   这里只放行「basemap/ 目录下的 .geojson」，且**逐个文件名校验**（防目录穿越）。
BASEMAP_DIR = Path(__file__).resolve().parent.parent / "data" / "basemap"
_BASEMAP_OK = re.compile(r"^[A-Za-z0-9_\-.]+\.geojson$")


@app.get("/basemap/{name}")
def basemap_file(name: str):
    """离线底图 GeoJSON（Natural Earth 公共域 + Overpass/OSM）。

    ★ 安全性
      · 只允许 `.geojson` 后缀，且文件名必须匹配白名单正则（**防目录穿越**）
      · 不挂整个目录 —— `data/` 下面还有 347 MB 的库文件，绝不能被静态暴露
      · 这些是**公开数据**（公共域），公网可读没问题；响应带长缓存头
    """
    if not _BASEMAP_OK.match(name or ""):
        raise HTTPException(status_code=404, detail="底图文件不存在")
    p = BASEMAP_DIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="底图文件不存在")
    data = p.read_bytes()
    return Response(
        content=data, media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400",
                 "Content-Length": str(len(data))})


def _is_loopback(ip: str) -> bool:
    ip = (ip or "").strip()
    if ip in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"):
        return True
    return ip.startswith("127.")


def _client_ip(request: Request) -> tuple[str, str]:
    """返回 (直连方地址, 转发来的真实客户端地址)。

    ★ 为什么不能只看 request.client.host
    ------------------------------------
    在 **Cloudflare Tunnel / 反向代理**后面，uvicorn 看到的直连方是
    **代理进程本身**（Cloudflare Tunnel 走本机回环）→ `client.host` 恒为
    `127.0.0.1`。拿它判「仅本机」，等于**这个限制根本没生效**，
    完整诊断信息（含本机绝对路径）会被公网任意访客拿走。

    所以规则是：
      · 直连方**必须**是回环地址（说明请求是本机进程发起的）
      · 且**没有**转发头、或转发来的地址也是回环，才算「本机访问」
    这样：隧道进来的请求（直连方回环、转发头是公网 IP）→ 判为非本机 → 403；
    外网直连（直连方是公网 IP）→ 非本机 → 403；
    本机直接访问 → 本机 → 放行。
    """
    peer = request.client.host if request.client else ""
    fwd = ""
    for h in ("cf-connecting-ip", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            fwd = v.strip()
            break
    if not fwd:
        v = request.headers.get("x-forwarded-for")
        if v:
            fwd = v.split(",")[0].strip()
    return peer, fwd


def _is_local_request(request: Request) -> bool:
    peer, fwd = _client_ip(request)
    if not _is_loopback(peer):
        return False
    # 有转发头就说明请求是**从代理转过来的**：真实客户端是 fwd 那个地址。
    # 它是公网地址 → 这不是本机访问。
    if fwd and not _is_loopback(fwd):
        return False
    return True


@app.get("/api/health")
def health():
    """★ 脱敏：只回 {"ok": true}。

    完整信息里含**本机绝对路径**（库在哪、源数据在哪），
    一旦挂公网，等于给访问者送了一张地图。msyt `SECURITY.md` 第 2 条就是这个。
    """
    return {"ok": True}


@app.get("/api/health/detail")
def health_detail(request: Request):
    """完整信息：**只允许本机访问**（公网模式下不返回）。

    ★ 判断逻辑见 `_is_local_request`：**不能只看 request.client.host**，
      隧道/反代后面它恒为 127.0.0.1，直接用等于没锁。
    """
    cfg = get_config()
    if not cfg.local_detail_enabled:
        return JSONResponse({"error": "已关闭本机诊断端点"}, status_code=403)
    if not _is_local_request(request):
        return JSONResponse({"error": "仅本机可访问"}, status_code=403)
    c = db()
    try:
        from .statsapi import health_detail as _detail
        return _detail(c)
    finally:
        c.close()


# ── xlsx 模板下载 ────────────────────────────────────────────────────────
def _template_response(kind: str, variant: str):
    from .registry import VARIANT_FULL, VARIANT_MINIMAL
    from .templates import build_template_bytes, template_filename
    from urllib.parse import quote
    variant = VARIANT_FULL if variant == "full" else VARIANT_MINIMAL
    data = build_template_bytes(kind, variant)
    name = template_filename(kind, variant)
    log_access("download", f"template:{kind}:{variant}", name)
    ascii_name = f"registry_template_{kind}_{variant}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"})


@app.get("/api/template/literatures")
def template_literatures(variant: str = "minimal"):
    """下载空白《文献基本信息登记表》模板（xlsx）。

    ★ 直接返回**文件流**，不 302：跨源 302 会让浏览器忽略 `download` 属性，
    下载到的文件名会变成 `literatures`（无扩展名）。
    ★ 模板由**代码从列定义生成**，不是下载一个附件 → 不会与导入器漂移。
    """
    return _template_response("literatures", variant)


@app.get("/api/template/images")
def template_images(variant: str = "minimal"):
    """下载空白《文献图像信息登记表》模板（xlsx）。"""
    return _template_response("images", variant)


# ── 总览 ────────────────────────────────────────────────────────────────
def _group(conn, sql, params=()):
    return [{"k": r["k"], "n": r["n"]} for r in conn.execute(sql, params)]


@app.get("/api/overview")
def overview():
    c = db()
    try:
        meta = get_meta(c)
        by_doc = _group(c, "SELECT doc_type k, COUNT(*) n FROM literatures GROUP BY doc_type ORDER BY n DESC")
        by_lang = _group(c, "SELECT lang k, COUNT(*) n FROM literatures GROUP BY lang ORDER BY n DESC")
        by_year = _group(c, "SELECT year k, COUNT(*) n FROM literatures WHERE year<>'' GROUP BY year ORDER BY year")
        by_src = _group(c, "SELECT source_type k, COUNT(*) n FROM literatures GROUP BY source_type ORDER BY n DESC")
        by_open = _group(c, "SELECT open_type k, COUNT(*) n FROM literatures GROUP BY open_type ORDER BY n DESC")
        # 四维标签分布：按规范化后的标签表统计（比原文拆分更准）
        by_period = _group(c, "SELECT name k, SUM(lit_count) n FROM tags WHERE dim='period' GROUP BY name ORDER BY n DESC LIMIT 30")
        by_region = _group(c, "SELECT name k, SUM(lit_count) n FROM tags WHERE dim='region' GROUP BY name ORDER BY n DESC LIMIT 30")
        by_topic = _group(c, "SELECT name k, SUM(lit_count) n FROM tags WHERE dim='topic' GROUP BY name ORDER BY n DESC LIMIT 30")

        # 复核状态：按**状态字段**统计（复核日期全非空，不能用来判断是否已复核）
        rev = {r["k"]: r["n"] for r in c.execute(
            "SELECT review_status k, COUNT(*) n FROM images GROUP BY review_status")}
        PASS = ("REV-PASS｜通过", "已人工核实")
        PEND = ("REV-PENDING｜待人工复核", "待复核", "待人工复核")
        n_pass = sum(v for k, v in rev.items() if k in PASS)
        n_pend = sum(v for k, v in rev.items() if k in PEND)
        n_other = sum(v for k, v in rev.items() if k not in PASS and k not in PEND)
        total_img = n_pass + n_pend + n_other

        md_chars = sum(r["md_chars"] or 0 for r in c.execute("SELECT md_chars FROM literatures"))
        return {
            "lit_count": count(c, "literatures"),
            "img_count": count(c, "images"),
            "tag_count": count(c, "tags"),
            "raw_json_count": count(c, "raw_json"),
            "md_total_chars": md_chars,
            "docs_fts_count": count(c, "docs_fts"),
            "migrated_at": meta.get("migrated_at"),
            "updated_at": meta.get("migrated_at"),
            "by_doc_type": by_doc, "by_lang": by_lang, "by_year": by_year,
            "by_source_type": by_src, "by_open_type": by_open,
            "by_period": by_period, "by_region": by_region, "by_topic": by_topic,
            "review": {"pass": n_pass, "pending": n_pend, "other": n_other,
                       "total": total_img,
                       "rate": round(n_pass / total_img, 4) if total_img else 0},
            "note": "复核状态按 images.review_status 统计；复核日期全非空，不等于已复核",
        }
    finally:
        c.close()


# ── 检索核心 ────────────────────────────────────────────────────────────
def _match_lit_ids(conn, q: str) -> tuple[dict[str, str], str]:
    """关键词 → {lit_id: snippet}。返回 (命中表, 使用的检索方式)。

    ★ 短于 trigram 阈值的词会让整条查询退回 LIKE（半边索引半边扫描会漏结果）。
    """
    q = (q or "").strip()
    if not q:
        return {}, ""
    tokens = [t for t in re.split(r"[\s,，;；、]+", q) if t]
    if not tokens:
        return {}, ""
    hits: dict[str, str] = {}
    if not needs_like(tokens):
        try:
            rows = conn.execute(
                "SELECT lit_id, snippet(docs_fts, 1, '<mark>', '</mark>', ' … ', 14) s "
                "FROM docs_fts WHERE docs_fts MATCH ? LIMIT 400", (fts_query(tokens),)).fetchall()
            for r in rows:
                hits[r["lit_id"]] = r["s"] or ""
            mode = "fts5"
        except sqlite3.OperationalError:
            hits, mode = {}, "like"
    else:
        mode = "like"
    if mode == "like":
        where, params = like_clause(tokens, ["content"])
        rows = conn.execute(
            f"SELECT lit_id, substr(content, max(instr(content, ?), 1) - 40, 130) s "
            f"FROM docs_fts WHERE {where} LIMIT 400", [tokens[0]] + params).fetchall()
        for r in rows:
            frag = (r["s"] or "").replace("\n", " ")
            frag = re.sub(r"\s{2,}", " ", frag).strip()
            hits[r["lit_id"]] = ("…" + frag + "…") if frag else ""
        # 标题/作者/关键词也命中（这些字段没有长文本，给个短标记）
        where2, params2 = like_clause(tokens, ["title", "authors", "keywords", "abstract"])
        rows2 = conn.execute(f"SELECT lit_id FROM literatures WHERE {where2} LIMIT 400", params2).fetchall()
        for r in rows2:
            hits.setdefault(r["lit_id"], f"命中：{q}")
    return hits, mode


def _snippet_literal(text: str, token: str, width: int = 60) -> str:
    i = text.find(token)
    if i < 0:
        return ""
    s = max(0, i - width // 2)
    frag = text[s: i + len(token) + width // 2]
    frag = re.sub(r"\s+", " ", frag).strip()
    return f"…{frag}…"


def _lit_filter_sql(params: dict) -> tuple[str, list, str]:
    """把筛选参数拼成 WHERE。维度内 OR、维度间 AND。"""
    where: list[str] = []
    args: list[Any] = []

    def multi(col: str, raw: str):
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        if not vals:
            return
        # 标签列存的是原文，用 LIKE 匹配原文包含关系
        where.append("(" + " OR ".join(f"{col} LIKE ?" for _ in vals) + ")")
        args.extend(f"%{v}%" for v in vals)

    if params.get("doc_type"):
        multi("doc_type", params["doc_type"])
    if params.get("lang"):
        multi("lang", params["lang"])
    if params.get("year"):
        where.append("year = ?"); args.append(params["year"])
    if params.get("open_type"):
        multi("open_type", params["open_type"])
    if params.get("source_type"):
        multi("source_type", params["source_type"])
    for dim, col in (("period", "period_tags"), ("region", "region_tags"),
                     ("topic", "topic_tags"), ("lineage", "lineage_tags")):
        if params.get(dim):
            multi(col, params[dim])
    return (" AND ".join(where) if where else "1=1"), args, " AND ".join(where)


def _query_lits(conn, params: dict) -> tuple[list[dict], int, str]:
    """按筛选 + 关键词查文献，返回 (items, total, 检索方式)。

    `params['all_rows']=True` 时**不分页**（导出用；否则 size 会被压到 100 条上限，
    导出的 CSV 会少掉一大半，且界面看不出来）。
    """
    where, args, _ = _lit_filter_sql(params)
    sql = f"SELECT * FROM literatures WHERE {where}"
    hits, mode = ({}, "")
    if params.get("q"):
        hits, mode = _match_lit_ids(conn, params["q"])
        if not hits:
            return [], 0, mode
        ids = list(hits.keys())
        sql += f" AND lit_id IN ({','.join('?' * len(ids))})"
        args += ids
    total = conn.execute(f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
    sort = params.get("sort") or "lit_id"
    sort_sql = {"lit_id": "lit_id", "year": "CAST(NULLIF(year,'') AS INTEGER)", "title": "title"}.get(sort, "lit_id")
    if params.get("all_rows"):
        rows = conn.execute(f"{sql} ORDER BY {sort_sql}, lit_id", args).fetchall()
    else:
        page = max(1, int(params.get("page") or 1))
        size = min(100, max(1, int(params.get("size") or 20)))
        rows = conn.execute(f"{sql} ORDER BY {sort_sql}, lit_id LIMIT ? OFFSET ?",
                            args + [size, (page - 1) * size]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        if params.get("q"):
            d["_snippet"] = hits.get(d["lit_id"], "") or _snippet_literal(
                (d.get("abstract") or "") + (d.get("keywords") or ""),
                params["q"].split()[0] if params["q"].split() else params["q"])
            d["_hit_mode"] = mode
        items.append(d)
    return items, total, mode


@app.get("/api/literatures")
def api_literatures(page: int = 1, size: int = 20, q: str = "",
                    doc_type: str = "", lang: str = "", year: str = "",
                    period: str = "", region: str = "", topic: str = "",
                    lineage: str = "", open_type: str = "", source_type: str = "",
                    sort: str = "lit_id"):
    c = db()
    try:
        items, total, mode = _query_lits(c, locals())
        return {"total": total, "page": page, "size": size, "items": items,
                "mode": mode}
    finally:
        c.close()


@app.get("/api/images")
def api_images(lit_id: str = "", page: int = 1, size: int = 40,
               img_type: str = "", obj_type: str = "", review_status: str = "",
               q: str = ""):
    c = db()
    try:
        where, args = ["1=1"], []
        if lit_id:
            where.append("lit_id = ?"); args.append(lit_id)
        if img_type:
            where.append("img_type LIKE ?"); args.append(f"%{img_type}%")
        if obj_type:
            where.append("obj_type LIKE ?"); args.append(f"%{obj_type}%")
        if review_status:
            where.append("review_status LIKE ?"); args.append(f"%{review_status}%")
        if q:
            where.append("(caption LIKE ? OR img_title LIKE ? OR fig_no LIKE ?)")
            args.extend([f"%{q}%"] * 3)
        w = " AND ".join(where)
        total = c.execute(f"SELECT COUNT(*) n FROM images WHERE {w}", args).fetchone()["n"]
        size = min(200, max(1, size))
        rows = c.execute(f"SELECT * FROM images WHERE {w} ORDER BY lit_id, filename LIMIT ? OFFSET ?",
                         args + [size, (max(1, page) - 1) * size]).fetchall()
        return {"total": total, "page": page, "size": size,
                "items": [dict(r) for r in rows]}
    finally:
        c.close()


@app.get("/api/tags")
def api_tags(dim: str = "", limit: int = 500):
    c = db()
    try:
        w = "WHERE dim = ?" if dim else ""
        args = (dim,) if dim else ()
        rows = c.execute(
            f"SELECT * FROM tags {w} ORDER BY (lit_count + img_count) DESC, name LIMIT ?",
            args + (min(2000, max(1, limit)),)).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        c.close()


@app.get("/api/literatures/{lit_id}")
def api_lit_detail(lit_id: str, img_limit: int = 60):
    c = db()
    try:
        row = c.execute("SELECT * FROM literatures WHERE lit_id = ?", (lit_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"未找到文献 {lit_id}")
        lit = dict(row)
        # ★ 图版限量：有的文献有上百张图，全量返回会让详情页一次渲染上百个 <img>。
        #   超出部分只给计数，前端显示「还有 N 张」。
        img_limit = max(0, min(500, img_limit))
        n_img_total = c.execute("SELECT COUNT(*) n FROM images WHERE lit_id = ?",
                                (lit_id,)).fetchone()["n"]
        imgs = [dict(r) for r in c.execute(
            "SELECT * FROM images WHERE lit_id = ? ORDER BY filename LIMIT ?",
            (lit_id, img_limit))]
        raws = [dict(r) for r in c.execute(
            "SELECT lit_id, filename, size_bytes, text FROM raw_json WHERE lit_id = ?", (lit_id,))]
        for r in raws:
            t = r.pop("text") or ""
            r["snippet"] = t[:400]
        tags: dict[str, list] = {"period": [], "region": [], "topic": [], "lineage": []}
        for r in c.execute(
                "SELECT t.tag_id, t.dim, t.name, t.code FROM lit_tags tt "
                "JOIN tags t ON t.tag_id = tt.tag_id WHERE tt.lit_id = ?", (lit_id,)):
            tags.setdefault(r["dim"], []).append(
                {"tag_id": r["tag_id"], "name": r["name"], "code": r["code"]})
        md_row = c.execute("SELECT content FROM docs_fts WHERE lit_id = ? LIMIT 1", (lit_id,)).fetchone()
        md_text = md_row["content"] if md_row else ""
        md = {"path": lit.get("md_path") or "", "chars": lit.get("md_chars") or 0,
              "pages": lit.get("md_pages") or 0, "img_refs": lit.get("md_img_refs") or 0,
              "excerpt": md_text[:1200]}
        similar = []
        for r in c.execute(
                "SELECT l.lit_id, l.title FROM lit_tags a JOIN lit_tags b ON a.tag_id=b.tag_id "
                "JOIN literatures l ON l.lit_id=b.lit_id WHERE a.lit_id=? AND b.lit_id<>? "
                "GROUP BY l.lit_id ORDER BY COUNT(*) DESC LIMIT 8", (lit_id, lit_id)):
            shared = [x["name"] for x in c.execute(
                "SELECT t.name FROM lit_tags a JOIN tags t ON t.tag_id=a.tag_id "
                "WHERE a.lit_id=? AND a.tag_id IN (SELECT tag_id FROM lit_tags WHERE lit_id=?)",
                (lit_id, r["lit_id"]))]
            similar.append({"lit_id": r["lit_id"], "title": r["title"], "shared_tags": shared[:6]})
        return {"lit": lit, "images": imgs, "images_total": n_img_total,
                "images_truncated": n_img_total > len(imgs),
                "raw_json": raws, "tags": tags,
                "md": md, "similar": similar}
    finally:
        c.close()


@app.get("/api/literatures/{lit_id}/map")
def api_lit_map(lit_id: str):
    """**这一篇文献的地图标记**（详情页用），按精度分四档。

    ★ 为什么要单独一个端点
      `/api/stats/mapdata` 是**反向**的（点 → 哪些文献），地图页用；
      详情页要**正向**的（这篇文献 → 落在哪些点上、那个坐标有多准）。
    ★ 分档是硬要求：文献坐标（米级）/ 公开权威坐标 / 分区代表点（±1.1 km）
      精度差一个数量级，混画会让人以为「每篇都有一个精确位置」—— 那是骗人。
    ★ 没命中就是「未定位」，**不推测填充**。
    """
    from .statsapi import lit_map_data
    c = db()
    try:
        return lit_map_data(c, lit_id)
    finally:
        c.close()


@app.get("/api/search")
def api_search(q: str = "", page: int = 1, size: int = 20, doc_type: str = "",
               lang: str = "", year: str = "", period: str = "", region: str = "",
               topic: str = "", lineage: str = ""):
    c = db()
    try:
        params = dict(q=q, page=page, size=size, doc_type=doc_type, lang=lang, year=year,
                      period=period, region=region, topic=topic, lineage=lineage)
        items, total, mode = _query_lits(c, params)
        hits = []
        for it in items[:50]:
            hits.append({"lit_id": it["lit_id"], "kind": it.get("_hit_mode", mode),
                         "snippet": it.get("_snippet", "")})
        return {"total": total, "items": items, "hits": hits, "mode": mode}
    finally:
        c.close()


# ── 图谱 ────────────────────────────────────────────────────────────────
def _graph_scope(conn, kinds: str = "", min_degree: int = 1, keyword: str = "",
                 focus: str = "", hop: int = 0, limit: int = 600):
    nodes, edges = load_graph(conn)
    if kinds:
        keep = {k.strip() for k in kinds.split(",") if k.strip()}
        nodes = {n: v for n, v in nodes.items() if v["kind"] in keep}
        edges = [e for e in edges if e["src"] in nodes and e["dst"] in nodes]
    if min_degree and min_degree > 1:
        deg = degree_map(nodes, edges)
        keep = {n for n, d in deg.items() if d >= min_degree}
        nodes = {n: v for n, v in nodes.items() if n in keep}
        edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    fn, fe = crop_scope(nodes, edges, keyword=keyword, focus=focus, hop=hop)
    return fn, fe


@app.get("/api/graph")
def api_graph(kinds: str = "", min_degree: int = 1, keyword: str = "",
              focus: str = "", hop: int = 0, limit: int = 600,
              edge_cap: int = 6, dense: bool = True):
    """图谱数据。

    ★ `dense=true`（默认）时做**三处降密**（实测基线 600 节点 / 7179 边时
      **513 个（85%）的最近邻距离 < 20px**，11px 的标签字宽约 60px → 必然糊）：
      ① 边按度数上限裁剪（`graphview.trim_edges`）
      ② 剔除什么都没连的节点（`graphview.prune_isolated`）
      ③ **按裁剪后的子图重算布局**（`graphview.layout_subgraph`）——
         原来用的是全图 3486 节点的坐标，裁到 600 个后 hub 全挤在簇心
      外加：给出**该显示名字的节点**（`graphview.label_candidates`）——
         标签给「社区代表」而不是「度数最高」（度数最高的全是 hub，全挤在簇心：
         度数 top60 两两最近距中位 19.4px；社区代表 93.9px）

    ★ `dense=false` 保留旧行为（全边 + 全图坐标），用于对照。
    ★ 布局仍**由后端算好**（固定 seed、可复现），前端 physics 保持关闭 ——
      这是本项目一贯的「同一份数据永远画出同一张图」的要求。
    """
    c = db()
    try:
        nodes, edges = _graph_scope(c, kinds, min_degree, keyword, focus, hop)
        total_nodes, total_edges = len(nodes), len(edges)
        # 截断：按度数高的先给
        deg = degree_map(nodes, edges)
        limit = max(10, min(3000, limit))
        if len(nodes) > limit:
            top = sorted(nodes, key=lambda n: (-deg.get(n, 0), n))[:limit]
            keep = set(top)
            nodes = {n: v for n, v in nodes.items() if n in keep}
            edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]

        raw_nodes, raw_edges = len(nodes), len(edges)
        pruned = 0
        label_nodes: list[str] = []
        if dense and nodes and edges:
            from . import graphview as gv
            kept = gv.trim_edges(nodes, edges, edge_cap)
            nodes, edges, dropped = gv.prune_isolated(nodes, kept)
            pruned = len(dropped)
            pos = gv.layout_subgraph(nodes, edges)
            label_nodes = gv.label_candidates(nodes, edges)
        else:
            pos = {}

        # ★ 分析与画图**共用同一个子图**（口径必须一致，见 graph.crop_scope 的说明）
        comps = analyze(nodes, edges)
        return {
            "nodes": [{"id": n, "kind": v["kind"], "label": v["label"],
                       "lit_id": v["lit_id"], "weight": v["weight"],
                       "degree": deg.get(n, 0),
                       # 确定性布局坐标：前端 physics:false 直接用这组坐标画，
                       #   同一份数据永远画出同一张图（力导向每次打开都会漂）。
                       "x": (pos.get(n) or (v.get("x"), v.get("y")))[0]
                       if pos.get(n) else v.get("x"),
                       "y": (pos.get(n) or (v.get("x"), v.get("y")))[1]
                       if pos.get(n) else v.get("y"),
                       # ★ 该不该显示名字由后端说了算（社区代表，不是度数最高）
                       "show_label": (n in label_nodes) if label_nodes else True}
                      for n, v in nodes.items()],
            "edges": edges,
            "label_nodes": label_nodes,
            "stats": {
                "nodes": total_nodes, "edges": total_edges,
                "returned_nodes": len(nodes), "returned_edges": len(edges),
                "raw_nodes": raw_nodes, "raw_edges": raw_edges,
                "pruned_isolated": pruned,
                "edge_cap": edge_cap if dense else 0,
                "dense": bool(dense),
                "truncated": len(nodes) < total_nodes or len(edges) < total_edges,
                "components": comps["components"], "max_component": comps["max_component"],
            },
            "rel_labels": REL_LABELS,
        }
    finally:
        c.close()


@app.get("/api/graph/analysis")
def api_graph_analysis(kinds: str = "", min_degree: int = 1, keyword: str = "",
                       focus: str = "", hop: int = 0):
    c = db()
    try:
        nodes, edges = _graph_scope(c, kinds, min_degree, keyword, focus, hop)
        res = analyze(nodes, edges)
        res["scope"] = {"nodes": len(nodes), "edges": len(edges)}
        res["note"] = ("Louvain seed=42 固定；社区未自动命名，sample_labels 仅供参考。"
                       "度中心性≠权责边界，介数才是。")
        return res
    finally:
        c.close()


@app.get("/api/graph/evidence")
def api_graph_evidence(src: str = "", dst: str = "", rel: str = ""):
    if not src or not dst:
        raise HTTPException(400, "需要 src 与 dst")
    c = db()
    try:
        items = evidence_for(c, src, dst, rel)
        return {"items": items, "rel_label": REL_LABELS.get(rel, rel)}
    finally:
        c.close()


# ── vis-network 运行时（零 CDN 内联）────────────────────────────────────
def _vislib(name: str) -> bytes | None:
    """从 pyvis 包内找 vis-network 的 js / css 原文。

    pyvis 0.3.2 的实际布局是 `lib/vis-<版本>/vis-network.min.js`（如 vis-9.1.2），
    **版本号会变**，所以要 glob 而不是写死路径。
    """
    import pyvis
    base = Path(pyvis.__file__).resolve().parent
    cands = [
        base / "lib" / "bindings" / name,
        base / "lib" / name,
    ]
    cands += sorted(base.glob(f"lib/vis-*/{name}"), reverse=True)
    cands += sorted(base.glob(f"templates/lib/vis-*/{name}"), reverse=True)
    for cand in cands:
        if cand.exists():
            return cand.read_bytes()
    return None


@app.get("/api/graph/vislib.js")
def vislib_js():
    data = _vislib("vis-network.min.js") or _vislib("vis-network.js")
    if data is None:
        raise HTTPException(404, "未找到 vis-network 运行时")
    return Response(content=data, media_type="application/javascript")


@app.get("/api/graph/vislib.css")
def vislib_css():
    data = _vislib("vis-network.min.css") or _vislib("vis-network.css")
    if data is None:
        raise HTTPException(404, "未找到 vis-network 样式")
    return Response(content=data, media_type="text/css")


# ── 导出 ────────────────────────────────────────────────────────────────
def _attachment(cn_name: str, ascii_name: str) -> dict:
    """构造 Content-Disposition。

    ⚠️ HTTP 头只能放 latin-1 字符，中文文件名直接写进去会抛
    `UnicodeEncodeError: 'latin-1' codec can't encode characters`。
    正确做法：RFC 5987 的 `filename*=UTF-8''...` + 一个 ASCII 回退名。
    """
    from urllib.parse import quote
    return {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cn_name)}"
    }


def _csv_response(rows: list[dict], filename: str, footer: list[str] | None = None):
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(list(rows[0].keys()))
        for r in rows:
            w.writerow(["" if r.get(k) is None else r.get(k) for k in rows[0].keys()])
    for line in (footer or []):
        w.writerow([line])
    data = buf.getvalue().encode("utf-8-sig")   # BOM：Excel 直接双击不乱码
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers=_attachment(filename, "liangzhu-export.csv"))


@app.get("/api/export/literatures.csv")
def export_lit_csv(q: str = "", doc_type: str = "", lang: str = "", year: str = "",
                    period: str = "", region: str = "", topic: str = "",
                    lineage: str = "", open_type: str = "", source_type: str = ""):
    c = db()
    try:
        params = dict(q=q, page=1, size=100000, doc_type=doc_type, lang=lang, year=year,
                      period=period, region=region, topic=topic, lineage=lineage,
                      open_type=open_type, source_type=source_type, all_rows=True)
        items, total, mode = _query_lits(c, params)
        for it in items:
            it.pop("_snippet", None)
            it.pop("_hit_mode", None)
        meta = get_meta(c)
        footer = [
            "",
            f"口径说明：导出时间 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}",
            f"筛选条件：q={q or '(无)'} doc_type={doc_type or '(全部)'} lang={lang or '(全部)'} "
            f"year={year or '(全部)'} period={period or '(全部)'} region={region or '(全部)'} "
            f"topic={topic or '(全部)'} lineage={lineage or '(全部)'} open_type={open_type or '(全部)'}",
            f"命中条数：{total}（库内共 {meta.get('lit_count', '?')} 篇；检索方式 {mode or '无关键词'}）",
            "使用条件以「open_type」列为准；部分文献来自机构订阅，不得公开外传。",
            f"数据迁移时间：{meta.get('migrated_at', '?')}",
        ]
        return _csv_response(items, "良渚文献目录.csv", footer)
    finally:
        c.close()


@app.get("/api/export/images.csv")
def export_img_csv(lit_id: str = "", img_type: str = "", obj_type: str = "",
                   review_status: str = ""):
    c = db()
    try:
        where, args = ["1=1"], []
        if lit_id:
            where.append("lit_id = ?"); args.append(lit_id)
        if img_type:
            where.append("img_type LIKE ?"); args.append(f"%{img_type}%")
        if obj_type:
            where.append("obj_type LIKE ?"); args.append(f"%{obj_type}%")
        if review_status:
            where.append("review_status LIKE ?"); args.append(f"%{review_status}%")
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM images WHERE {' AND '.join(where)} ORDER BY lit_id, filename", args)]
        meta = get_meta(c)
        footer = ["", f"口径说明：导出时间 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}",
                  f"条数：{len(rows)}（库内共 {meta.get('img_count', '?')} 张）",
                  "复核状态按 review_status 列；复核日期非空不代表已复核。"]
        return _csv_response(rows, "良渚文献图像.csv", footer)
    finally:
        c.close()


@app.get("/api/export/nbkg.csv")
def export_nbkg_csv():
    c = db()
    try:
        rows = nbkg_export_rows(c)
        meta = get_meta(c)
        footer = ["", f"口径说明：导出时间 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}",
                  f"条数：{len(rows)}；字段映射见程序目录 _docs/数据契约.md §4",
                  f"数据迁移时间：{meta.get('migrated_at', '?')}"]
        return _csv_response(rows, "良渚_主库字段映射导出.csv", footer)
    finally:
        c.close()


@app.get("/api/export/graph.html")
def export_graph_html(kinds: str = "", min_degree: int = 1, keyword: str = "",
                      focus: str = "", hop: int = 0, limit: int = 800):
    c = db()
    try:
        nodes, edges = _graph_scope(c, kinds, min_degree, keyword, focus, hop)
        deg = degree_map(nodes, edges)
        limit = max(10, min(3000, limit))
        if len(nodes) > limit:
            keep = set(sorted(nodes, key=lambda n: (-deg.get(n, 0), n))[:limit])
            nodes = {n: v for n, v in nodes.items() if n in keep}
            edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
        html = build_graph_html(nodes, edges, deg)
        meta = get_meta(c)
        html = html.replace("<!-- FOOTER -->", (
            f"<p class='foot'>口径：筛选后节点 {len(nodes)}、边 {len(edges)}；"
            f"导出时间 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}；"
            f"数据迁移 {meta.get('migrated_at', '?')}。图谱为「本数据集可观测到的关联结构」，非良渚文化全貌。</p>"))
        return Response(content=html, media_type="text/html; charset=utf-8",
                        headers=_attachment("良渚图谱.html", "liangzhu-graph.html"))
    finally:
        c.close()


def build_graph_html(nodes: dict, edges: list, deg: dict) -> str:
    """生成**离线自包含**的图谱 HTML（内联 vis-network，零 CDN 外链）。

    ⚠️ 这里刻意**不用 f-string**：JS/CSS 里的花括号会跟 f-string 的转义打架，
    很容易写出 `single '}' is not allowed`。改用普通模板 + replace。
    """
    js = _vislib("vis-network.min.js") or _vislib("vis-network.js")
    css = _vislib("vis-network.min.css") or _vislib("vis-network.css")
    if js is None:
        return "<h1>图谱导出失败</h1><p>未找到 vis-network 运行时（pyvis 包内缺失）。</p>"
    colors = {"lit": "#4C78A8", "img": "#F58518", "tag": "#54A24B", "objtype": "#E45756"}
    ndata = []
    for nid, v in nodes.items():
        ndata.append({"id": nid, "label": v["label"][:40], "group": v["kind"],
                      "value": 1 + min(8, deg.get(nid, 0)),
                      "title": f"{v['label']}<br>类型：{v['kind']}<br>度数：{deg.get(nid,0)}"})
    edata = [{"from": e["src"], "to": e["dst"], "label": REL_LABELS.get(e["rel"], e["rel"]),
              "title": REL_LABELS.get(e["rel"], e["rel"]), "value": 1}
             for e in edges]
    payload = json.dumps({"nodes": ndata, "edges": edata}, ensure_ascii=False)
    counts = {k: sum(1 for v in nodes.values() if v["kind"] == k)
              for k in ("lit", "img", "tag", "objtype")}
    groups = json.dumps({
        "lit": {"color": colors["lit"], "shape": "dot"},
        "img": {"color": colors["img"], "shape": "square"},
        "tag": {"color": colors["tag"], "shape": "triangle"},
        "objtype": {"color": colors["objtype"], "shape": "diamond"},
    }, ensure_ascii=False)

    tpl = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>良渚文献关联图谱 · 离线版</title>
<style>__CSS__</style></head><body>
<div id="wrap"><div id="net"></div>
<div class="legend"><b>图例</b><br>
<i style="background:__C_LIT__"></i>文献（__N_LIT__）<br>
<i style="background:__C_IMG__"></i>图像（__N_IMG__）<br>
<i style="background:__C_TAG__"></i>标签（__N_TAG__）<br>
<i style="background:__C_OBJ__"></i>对象类型（__N_OBJ__）</div>
<!-- FOOTER --></div>
<style>
#wrap{height:100vh;position:relative}
#net{width:100%;height:100%}
.legend{position:absolute;left:12px;top:12px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:8px 12px;font-size:13px;line-height:1.9;z-index:9}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:middle}
.foot{position:absolute;left:12px;bottom:10px;right:12px;font-size:12px;color:#555;background:#fffc;padding:6px 10px;border-radius:6px;z-index:9}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;margin:0;background:#f7f8fa}
</style>
<script>__JS__</script>
<script>
const DATA = __PAYLOAD__;
const GROUPS = __GROUPS__;
const nodes = new vis.DataSet(DATA.nodes);
const edges = new vis.DataSet(DATA.edges);
new vis.Network(document.getElementById("net"), {nodes: nodes, edges: edges},
  {nodes: {shape: "dot", size: 12, font: {size: 11, color: "#333"}, borderWidth: 0},
   edges: {color: {color: "#bbb"}, smooth: true, width: 1},
   groups: {color: {color: "#bbb"}, node: {color: "#4C78A8", shape: "dot", size: 12}},
   physics: {stabilization: {iterations: 180}, barnesHut: {springLength: 110}}}});
</script></body></html>"""
    out = (tpl
           .replace("__CSS__", css.decode("utf-8", errors="replace") if css else "")
           .replace("__JS__", js.decode("utf-8", errors="replace"))
           .replace("__PAYLOAD__", payload)
           .replace("__GROUPS__", groups)
           .replace("__C_LIT__", colors["lit"]).replace("__N_LIT__", str(counts["lit"]))
           .replace("__C_IMG__", colors["img"]).replace("__N_IMG__", str(counts["img"]))
           .replace("__C_TAG__", colors["tag"]).replace("__N_TAG__", str(counts["tag"]))
           .replace("__C_OBJ__", colors["objtype"]).replace("__N_OBJ__", str(counts["objtype"])))
    return out


# ── 图版代理 ────────────────────────────────────────────────────────────
PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
    '<rect width="100%" height="100%" fill="#eef1f5"/>'
    '<text x="50%" y="50%" text-anchor="middle" fill="#8895a7" '
    'font-family="Microsoft YaHei" font-size="13">图版未找到</text></svg>'
).encode("utf-8")


@app.get("/media/img/{filename}")
def media_img(filename: str):
    cfg = get_config()
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    p = cfg.image_dir / filename
    if not p.exists():
        return Response(content=PLACEHOLDER_SVG, media_type="image/svg+xml")
    ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return FileResponse(p, media_type=ctype)


@app.get("/api/notes")
def api_notes():
    """口径与免责说明（前端页脚用）。"""
    c = db()
    try:
        meta = get_meta(c)
    finally:
        c.close()
    return {
        "data_version": meta.get("migrated_at"),
        "source": "宁波地域文明探源工程考古数据库 · 良渚文化专题（第一阶段数据集）",
        "usage": "使用条件以各条记录的「开放类型/使用条件」为准；机构订阅取得的文献不得公开外传。",
        "disclaimer": "AI 初标只是候选标注，不是考古结论；未复核部分不代表定论。",
        "graph_note": "图谱是「本数据集可观测到的关联结构」，不等于良渚文化的完整知识体系。",
        "review_note": "复核状态按状态字段统计；复核日期非空不代表已复核。",
    }


# ══════════════════════════════════════════════════════════════════════
# F3 / P2 / P3 新端点（另装在 api_v2.py 里，避免本文件继续膨胀）
# ══════════════════════════════════════════════════════════════════════
from .api_v2 import ApiError, router as _router_v2   # noqa: E402

# 原文资源 + XML 协作（另装，避免本文件继续膨胀）
from .api_raw import router as _router_raw   # noqa: E402


@app.exception_handler(ApiError)
async def _api_v2_error_handler(request: Request, exc: ApiError):
    """契约 §0 的失败信封：`{"ok":false,"error":"人话说明"}` + 语义状态码。

    ★ 只作用于新端点抛的 `ApiError`；旧端点的 `HTTPException` 行为不变。
    """
    return JSONResponse(status_code=exc.status_code,
                        content={"ok": False, "error": exc.error})


@app.on_event("startup")
def _ensure_runtime_columns():
    """启动时补上后来新增的列（`staging.batch` / `staging.filename`）。

    ★ `init_db` 只在 migrate / doctor 里跑，服务启动不会跑它 →
      新列不在启动路径上补，导入功能就会报「no such column」。
    """
    try:
        from .db import ensure_columns
        c = db()
        try:
            ensure_columns(c)
        finally:
            c.close()
    except Exception:
        pass    # 补列失败不该让服务起不来


app.include_router(_router_v2)
app.include_router(_router_raw)

# ── 本地第三方库（Leaflet 等）──────────────────────────────────────────
# ★ 为什么需要挂
#   这个站点的首页是**由后端直接返回的 HTML**（没有挂 StaticFiles 整站），
#   所以页面里引用的 `/vendor/leaflet/leaflet.js` 默认会 404。
#   只挂 `vendor/` 一个子目录 —— 它里面只有第三方前端库（当前是 Leaflet），
#   **不碰 data/**（那里有 347 MB 的库文件与原始数据，绝不能被静态暴露）。
VENDOR_DIR = STATIC_DIR / "vendor"
if VENDOR_DIR.exists():
    app.mount("/vendor", StaticFiles(directory=str(VENDOR_DIR)), name="vendor")
