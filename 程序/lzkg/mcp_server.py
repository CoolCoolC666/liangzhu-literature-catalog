# -*- coding: utf-8 -*-
"""MCP 服务（契约 v3 §4）：给智能体用的**只读**检索工具。

★ 2.x 的三个坑（都踩过）：
  1. `FastMCP` 在 MCP 2.x 里**不存在** → 改名 `MCPServer`
     （`from mcp.server.mcpserver import MCPServer`）。
  2. `mcp_app.lifespan` 属性不存在（它就是个普通 Starlette）→ 要用
     `mcp_app.router.lifespan_context(mcp_app)` 把**会话管理器**带起来；
     少了这一步，客户端 `initialize` 直接报 "Server returned an error response"。
  3. `streamable_http_app(host=...)` 传回环地址（127.0.0.1/localhost/::1）时，
     SDK 会**自动开启 DNS 重绑定保护**（allowed_hosts 只有回环）→ 公网域名会被拒。
     所以这里传**本站的公网域名**（`config.json` 的 `public.domain`）。

★ 只读：只暴露检索/查看类工具，**不开放**导入、审批、删除（那些是界面层的事）。
★ 每个工具的返回里都带**口径说明**（这个数是怎么算出来的）。
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from .common import now_str
from .config import get_config
from .db import connect, count

try:
    from mcp.server.mcpserver import MCPServer
    MCP_AVAILABLE = True
except Exception:      # pragma: no cover - 没装 mcp 时站点照常跑
    MCPServer = None    # type: ignore
    MCP_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════
# 工具（六个，只读）
# ══════════════════════════════════════════════════════════════════════
if MCP_AVAILABLE:
    mcp = MCPServer(
        name="lzkg",
        instructions=("良渚文化文献目录站（宁波地域文明探源工程考古数据库 · 良渚文化专题）"
                      "的只读检索工具。工具只返回库里的既有数据，"
                      "每个返回都带口径说明；站点不提供写入、审批、删除能力。"),
    )
else:      # pragma: no cover
    mcp = None


def _conn():
    return connect()


def _tags_of(conn, lit_id: str) -> dict:
    """四维标签（按**名称**；标签节点的 name 才是可靠口径）。"""
    out = {"period": [], "region": [], "topic": [], "lineage": []}
    rows = conn.execute(
        "SELECT t.dim, t.name FROM lit_tags lt JOIN tags t ON t.tag_id = lt.tag_id "
        "WHERE lt.lit_id = ? ORDER BY t.dim, t.name", (lit_id,)).fetchall()
    for r in rows:
        out.setdefault(r["dim"], []).append(r["name"])
    return out


def _public_lit(row: dict) -> dict:
    """文献条目的对外形状（只保留有意义的字段）。"""
    return {
        "lit_id": row.get("lit_id") or "",
        "title": row.get("title") or "",
        "year": row.get("year") or "",
        "doc_type": row.get("doc_type") or "",
        "authors": row.get("authors") or "",
        "venue": row.get("venue") or "",
        "open_type": row.get("open_type") or "",
        "source_type": row.get("source_type") or "",
    }


if MCP_AVAILABLE:

    @mcp.tool(description="按关键词/类型/分区/主题/年份检索文献（只读）。"
                           "返回命中列表与片段，每个返回都带口径说明。")
    def search_literatures(query: str = "", doc_type: str = "", region: str = "",
                           topic: str = "", year: str = "", limit: int = 10) -> dict:
        """检索文献。

        ★ 口径：关键词走**本地 FTS5 全文索引**（≤2 字的词自动退回 LIKE 扫描）；
          类型/分区/主题是**包含匹配**（库里标签列存的是原文），年份是**精确相等**。
        """
        from .app import _query_lits
        conn = _conn()
        try:
            size = max(1, min(50, int(limit or 10)))
            params = {"q": (query or "").strip(), "doc_type": doc_type or "",
                      "region": region or "", "topic": topic or "",
                      "year": (year or "").strip(), "page": 1, "size": size,
                      "sort": "lit_id"}
            items, total, mode = _query_lits(conn, params)
            out = []
            for it in items:
                d = _public_lit(it)
                d["snippet"] = (it.get("_snippet") or "")[:300]
                d["period_tags"] = it.get("period_tags") or ""
                d["region_tags"] = it.get("region_tags") or ""
                d["topic_tags"] = it.get("topic_tags") or ""
                out.append(d)
            return {"ok": True, "total": total, "returned": len(out),
                    "search_mode": mode, "items": out,
                    "口径": ("关键词走本地 FTS5 全文索引（≤2 字的词自动退回 LIKE 扫描）；"
                             "类型/分区/主题为包含匹配；年份精确相等；"
                             "total 是筛选后的总数，returned 是本次返回条数。")}
        finally:
            conn.close()

    @mcp.tool(description="按文献标识符取完整题录 + 四维标签 + 来源判据 + 开放类型（只读）。")
    def get_literature(lit_id: str) -> dict:
        """取一条文献的完整题录。

        ★ 口径：四维标签按**标签名称**给（标签代码一码多义，不可靠，故不给代码）；
          「开放类型/使用条件」原样回传，使用与否以它为准。
        """
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM literatures WHERE lit_id = ?",
                               (lit_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": f"未找到文献 {lit_id}",
                        "口径": "按 literatures.lit_id 精确匹配。"}
            lit = dict(row)
            n_img = conn.execute("SELECT COUNT(*) c FROM images WHERE lit_id = ?",
                                 (lit_id,)).fetchone()["c"]
            return {"ok": True, "literature": lit,
                    "tags": _tags_of(conn, lit_id),
                    "image_count": n_img,
                    "口径": ("题录取自 literatures 表全字段；四维标签按标签**名称**"
                             "（代码一码多义、不可靠）；图像数为 images 表按 lit_id 计数；"
                             "开放类型/使用条件以该字段为准。")}
        finally:
            conn.close()

    @mcp.tool(description="列某篇文献的图版清单（文件名/图注/页码/对象类型，只读）。")
    def list_images(lit_id: str, limit: int = 50) -> dict:
        """图版清单。

        ★ 口径：取自 images 表（登记表「图像信息登记表」的 26 列），
          按 filename 排序；「人工复核状态」原样回传。
        """
        conn = _conn()
        try:
            size = max(1, min(500, int(limit or 50)))
            rows = conn.execute(
                "SELECT img_id, lit_id, filename, path, img_title, img_type, obj_type, "
                "fig_no, caption, orig_page, pdf_page, review_status "
                "FROM images WHERE lit_id = ? ORDER BY filename LIMIT ?",
                (lit_id, size)).fetchall()
            items = [dict(r) for r in rows]
            total = conn.execute("SELECT COUNT(*) c FROM images WHERE lit_id = ?",
                                 (lit_id,)).fetchone()["c"]
            return {"ok": True, "total": total, "returned": len(items), "items": items,
                    "口径": ("图版行取自 images 表（登记表的图像信息登记表）；"
                             "人工复核状态按 review_status 字段，"
                             "复核日期非空**不代表**已复核。")}
        finally:
            conn.close()

    @mcp.tool(description="读某篇文献的正文 Markdown 摘录（默认只给前 3000 字，只读）。")
    def read_markdown(lit_id: str, max_chars: int = 3000) -> dict:
        """正文 Markdown 摘录。

        ★ 口径：优先读登记表指针列 `md_path` 指向的 .md 文件（相对 04_专题研究
          的上级目录）；读不到时退回全文索引里的正文。默认只给前 3000 字。
        """
        cfg = get_config()
        conn = _conn()
        try:
            row = conn.execute("SELECT lit_id, title, md_path, md_chars FROM literatures "
                               "WHERE lit_id = ?", (lit_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": f"未找到文献 {lit_id}",
                        "口径": "按 literatures.lit_id 精确匹配。"}
            n = max(1, min(200000, int(max_chars or 3000)))
            rel = (row["md_path"] or "").replace("\\", "/").lstrip("/")
            text = ""
            source = ""
            if rel:
                for base in (cfg.source_root.parent, cfg.source_root):
                    p = base / rel
                    if p.exists():
                        text = p.read_text(encoding="utf-8", errors="replace")
                        source = "登记表指针列 md_path 指向的文件"
                        break
            if not text:
                fts = conn.execute("SELECT content FROM docs_fts WHERE lit_id = ? LIMIT 1",
                                   (lit_id,)).fetchone()
                if fts:
                    text = fts["content"] or ""
                    source = "全文索引 docs_fts（迁移时的正文快照）"
            if not text:
                return {"ok": False, "error": f"{lit_id} 没有可读的正文 Markdown",
                        "口径": "先查 md_path 指向的文件，再退回全文索引；两处都没有就是没有。"}
            return {"ok": True, "lit_id": lit_id, "title": row["title"] or "",
                    "chars_total": len(text), "returned_chars": min(n, len(text)),
                    "markdown": text[:n], "source": source,
                    "口径": ("正文取自登记表指针列 md_path 指向的 Markdown（读不到时退回"
                             "全文索引）；返回的是**前 N 字**摘录，不是全文。")}
        finally:
            conn.close()

    @mcp.tool(description="查图谱邻居：给节点 id（如 lit:LIT-000001）返回邻居与关系名（只读）。")
    def graph_neighbors(node_id: str, depth: int = 1, limit: int = 50) -> dict:
        """图谱邻居。

        ★ 口径：图谱由登记表**零抽取**而来（文献包含图像 / 图像描绘对象 /
          文献标注标签 / 图像标注标签 / 共享标签共现），
          节点 id 形如 `lit:LIT-000001`、`img:IMG-…`、`tag:…`、`objtype:…`。
        """
        conn = _conn()
        try:
            d = max(1, min(3, int(depth or 1)))
            size = max(1, min(500, int(limit or 50)))
            from .graph import REL_LABELS
            frontier = {node_id}
            seen = {node_id}
            out_edges = []
            for _ in range(d):
                if not frontier or len(out_edges) >= size:
                    break
                marks = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"SELECT src, dst, rel, lit_id FROM graph_edges "
                    f"WHERE src IN ({marks}) OR dst IN ({marks}) "
                    f"ORDER BY src, dst, rel LIMIT ?",
                    list(frontier) + list(frontier) + [size]).fetchall()
                nxt = set()
                for r in rows:
                    out_edges.append({"src": r["src"], "dst": r["dst"], "rel": r["rel"],
                                      "rel_label": REL_LABELS.get(r["rel"], r["rel"]),
                                      "lit_id": r["lit_id"] or ""})
                    for nid in (r["src"], r["dst"]):
                        if nid not in seen:
                            seen.add(nid)
                            nxt.add(nid)
                frontier = nxt
            nodes = []
            if seen:
                marks = ",".join("?" * len(seen))
                nodes = [dict(r) for r in conn.execute(
                    f"SELECT node_id, kind, label, lit_id, weight FROM graph_nodes "
                    f"WHERE node_id IN ({marks}) ORDER BY node_id", list(seen))]
            return {"ok": True, "node_id": node_id, "depth": d,
                    "nodes": nodes, "edges": out_edges[:size],
                    "口径": ("图谱由登记表零抽取（文献包含图像 / 图像描绘对象 / "
                             "文献标注标签 / 图像标注标签 / 共享标签共现）；"
                             "关系名见 rel_label；共现边是抽样出来的，不等于全集。")}
        finally:
            conn.close()

    @mcp.tool(description="库规模与分布概览（文献数/图像数/标签数/复核覆盖率等，只读）。")
    def stats_overview() -> dict:
        """库规模与分布。

        ★ 口径：文献数/图像数/标签数**现算**；
          **复核覆盖率按 `review_status` 状态字段统计**（复核日期非空 ≠ 已复核）。
        """
        conn = _conn()
        try:
            rev = {r["k"]: r["n"] for r in conn.execute(
                "SELECT review_status k, COUNT(*) n FROM images GROUP BY review_status")}
            PASS = ("REV-PASS｜通过", "已人工核实")
            PEND = ("REV-PENDING｜待人工复核", "待复核", "待人工复核")
            n_pass = sum(v for k, v in rev.items() if k in PASS)
            n_pend = sum(v for k, v in rev.items() if k in PEND)
            n_other = sum(v for k, v in rev.items() if k not in PASS and k not in PEND)
            total_img = n_pass + n_pend + n_other
            by_doc = [{"k": r["doc_type"], "n": r["n"]} for r in conn.execute(
                "SELECT doc_type, COUNT(*) n FROM literatures GROUP BY doc_type "
                "ORDER BY n DESC, doc_type")]
            by_period = [{"k": r["name"], "n": r["n"]} for r in conn.execute(
                "SELECT name, SUM(lit_count) n FROM tags WHERE dim='period' "
                "GROUP BY name ORDER BY n DESC, name LIMIT 20")]
            return {
                "ok": True,
                "generated_at": now_str(),
                "lit_count": count(conn, "literatures"),
                "img_count": count(conn, "images"),
                "tag_count": count(conn, "tags"),
                "raw_json_count": count(conn, "raw_json"),
                "graph_nodes": count(conn, "graph_nodes"),
                "graph_edges": count(conn, "graph_edges"),
                "review": {"pass": n_pass, "pending": n_pend, "other": n_other,
                           "total": total_img,
                           "rate": round(n_pass / total_img, 4) if total_img else 0},
                "by_doc_type": by_doc,
                "by_period": by_period,
                "口径": ("条数一律现算；复核覆盖率按 images.review_status 状态字段统计，"
                         "复核日期非空不代表已复核；分期按 tags.dim='period' 的文献数，"
                         "一篇文献可带多个标签，合计可能大于文献总数。"),
            }
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════
# HTTP 形态（挂到本站 FastAPI 上，路径 /mcp）
# ══════════════════════════════════════════════════════════════════════
class _AuthGate:
    """MCP HTTP 端点的**接口层锁**（沿用本站规则）。

    ★ 放行条件（任一）：
      · 带本站 token（`X-Auth-Token`）且有效 → 放行；
      · 带**已验签的** Cloudflare Access 身份 → 放行；
      · 本机免登录模式（`auth.enabled=false`）→ 放行（本机演示）。
      都不满足 → **401**（不是 403：没登录就是没登录）。
    ★ 只拦 `/mcp` 路径：挂载在 `/` 上的其它路径不该被这里改掉。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path.startswith("/mcp") and not _authorized(scope):
            body = json.dumps({"ok": False, "error": "请先登录"}, ensure_ascii=False).encode("utf-8")
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _authorized(scope) -> bool:
    headers = {}
    for k, v in scope.get("headers") or []:
        try:
            headers[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception:
            continue
    try:
        from .auth import resolve_principal
        if resolve_principal(headers.get("x-auth-token") or "") is not None:
            return True
    except Exception:
        pass
    try:
        from .access import identity_from_headers
        if identity_from_headers(headers) is not None:
            return True
    except Exception:
        pass
    return False


def build_http_app():
    """MCP 的 Streamable HTTP 应用（Starlette）。"""
    if not MCP_AVAILABLE:
        raise RuntimeError("没装 MCP SDK（pip install 'mcp>=2'）")
    cfg = get_config()
    domain = str((cfg.public_cfg or {}).get("domain") or "lztp.example.com")
    # ★ host 传**公网域名**：传回环地址时 SDK 会自动开 DNS 重绑定保护，
    #   只放行 127.0.0.1/localhost/::1 → 公网域名会被直接拒。
    return mcp.streamable_http_app(streamable_http_path="/mcp", host=domain)


_ATTACHED: dict = {}


def attach_to_host_app(host_app) -> bool:
    """把 MCP 的 HTTP 形态挂到本站 FastAPI 上（路径 `/mcp`）。

    ★ 两处必须做对：
      1. **lifespan**：MCP 的会话管理器要靠
         `mcp_app.router.lifespan_context(mcp_app)` 启动 ——
         少了这一步，客户端 `initialize` 直接报 "Server returned an error response"。
      2. **挂载位置**：挂载点是 `/`（匹配一切路径），所以必须挂在**最后**
         （`app.include_router` 之后），否则会把既有端点全遮住。
         → 因此这里改的是 **lifespan**（服务启动时路由表已定稿）。
    """
    if not MCP_AVAILABLE:
        return False
    if _ATTACHED.get("app") is host_app:
        return True
    mcp_app = build_http_app()
    gated = _AuthGate(mcp_app)
    orig = host_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with orig(app):
            async with mcp_app.router.lifespan_context(mcp_app):
                from starlette.routing import Mount
                already = any(isinstance(r, Mount) and getattr(r, "path", "") == "/"
                              and getattr(r, "app", None) is gated
                              for r in host_app.router.routes)
                if not already:
                    host_app.router.routes.append(Mount("/", gated))
                yield

    host_app.router.lifespan_context = lifespan
    _ATTACHED["app"] = host_app
    return True


def build_standalone_http_app():
    """`python -m lzkg mcp --http` 用的独立 FastAPI 应用（供本地测试）。"""
    from fastapi import FastAPI
    from starlette.routing import Mount
    mcp_app = build_http_app()
    gated = _AuthGate(mcp_app)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="良渚文献目录站 · MCP", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.mount("/", gated)
    return app


def run_stdio() -> int:
    """`python -m lzkg mcp` → stdio 形态（给本地智能体）。"""
    if not MCP_AVAILABLE:
        print("[X] 没装 MCP SDK：pip install 'mcp>=2'")
        return 1
    mcp.run(transport="stdio")
    return 0
