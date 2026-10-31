# -*- coding: utf-8 -*-
"""原文资源 + XML 协作 的端点。

放在 `api_v2.py` 里保持「v2 契约」这一层的完整；但代码量大了会难维护，
所以**逻辑放模块**（`rawtext.py` / `xmlexchange.py`），这里只做端点壳。

─── 关于「原文通道对机构订阅件开不开」 ────────────────────────────────
用户明确选了 **全开（登录与否都给）**。所以这里**不按 open_type 拦**。

★ 但站点页脚那句「标注为机构订阅/限内部使用的文献不得外传」就与实际
  行为冲突了 —— **口径不能撒谎**。所以：
  · 页脚改成「外传与否由使用者自行判断，站内提供原文通道不代表授权」；
  · 每条原文响应里带 `open_type`，界面**如实显示**该条的开放类型；
  · 做成配置开关 `raw.access`（open / login / oa_only），
    将来要收紧改配置即可，不用改代码。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import rawtext, xmlexchange
from .auth import AuthError
from .auth import require_admin as _require_admin
from .auth import require_login as _require_login
from .common import attachment_headers, ok
from .config import get_config
from .db import connect
router = APIRouter(prefix="/api", tags=["raw"])


# ── 鉴权依赖（与 api_v2 同语义，只是错误信封不同）────────────────────
def require_admin(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    try:
        return _require_admin(x_auth_token)
    except AuthError as e:
        raise HTTPException(e.status_code, e.error)


def require_login(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    try:
        return _require_login(x_auth_token)
    except AuthError as e:
        raise HTTPException(e.status_code, e.error)


# ── 访问闸 ────────────────────────────────────────────────────────────
def _access_mode() -> str:
    """原文通道的访问口径。默认 `open`（用户选定：登录与否都给）。"""
    cfg = get_config()
    raw = (cfg.data.get("raw") or {})
    mode = str(raw.get("access") or "open").strip().lower()
    return mode if mode in ("open", "login", "oa_only") else "open"


def _is_open_access(open_type: str) -> bool:
    """开放获取 / 公开类 → True。订阅类 → False。"""
    t = (open_type or "").strip()
    if not t:
        return False
    return not any(k in t for k in ("机构订阅", "付费", "限内部"))


def _check_access(conn: sqlite3.Connection, lit_id: str) -> tuple[dict, str]:
    """按口径决定能不能给原文。→ (文献行, 拒绝理由)

    ★ 拒绝时**不区分「不存在」与「无权限」**会对可用性更友好，
      但这里为了让界面能写清原因，返回明确理由字符串。
    """
    row = conn.execute("SELECT * FROM literatures WHERE lit_id = ?", (lit_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"未找到文献 {lit_id}")
    lit = dict(row)
    mode = _access_mode()
    if mode == "open":
        return lit, ""
    if mode == "oa_only" and not _is_open_access(lit.get("open_type") or ""):
        return lit, "该条为机构订阅/付费件，按开放类型限制不提供原文"
    return lit, ""


# ── 原文资源 ──────────────────────────────────────────────────────────
@router.get("/raw/{lit_id}/markdown")
def raw_markdown(lit_id: str, download: int = 0):
    """这篇文献的**原文 Markdown**（渲染好的 HTML 片段 + 原始文本）。

    → `{html, text, chars, images:{total,rewritten,missed,skipped_reason}}`

    ★ 图版重写：把原文里的 CDN 直链换成本站图版。
      换不掉的（图数不等 / 校验不通过）**保持原样并标注**，
      绝不硬配 —— 错配等于造谣。
    """
    cfg = get_config()
    conn = connect()
    try:
        lit, deny = _check_access(conn, lit_id)
        if deny:
            raise HTTPException(403, deny)
        p = rawtext.safe_resolve(cfg.source_root, lit.get("md_path") or "")
        if p is None:
            raise HTTPException(404, "这篇文献的原文文件不在数据源目录里")
        text = p.read_text(encoding="utf-8", errors="replace")
        images = [dict(r) for r in conn.execute(
            "SELECT * FROM images WHERE lit_id = ? ORDER BY rowid", (lit_id,))]
        new_md, stat = rawtext.map_md_images(text, images)
        html = rawtext.md_to_html(new_md)
        payload = {
            "lit_id": lit_id,
            "title": lit.get("title") or "",
            "open_type": lit.get("open_type") or "",
            "html": html,
            "text": new_md,
            "chars": len(new_md),
            "source": Path(lit.get("md_path") or "").name,
            "images": stat,
        }
    finally:
        conn.close()
    if download:
        fn = "LIT-%s.md" % lit_id.replace("LIT-", "")
        return Response(content=payload["text"], media_type="text/markdown; charset=utf-8",
                        headers=attachment_headers("%s_原文.md" % lit_id, fn))
    return ok(payload)


@router.get("/raw/{lit_id}/pdf")
def raw_pdf(lit_id: str, download: int = 0):
    """这篇文献的**原始 PDF**（浏览器内嵌阅读 / 或下载）。

    ★ 内嵌时用 `Content-Disposition: inline`（浏览器自带阅读器直接显示），
      下载时用 `attachment`。
    ★ PDF 走 `<iframe>` 内嵌，**不引 pdf.js**（零外链纪律：
      本地 vendor 一个 pdf.js 体积大、还得维护版本）。
    """
    cfg = get_config()
    conn = connect()
    try:
        lit, deny = _check_access(conn, lit_id)
        if deny:
            raise HTTPException(403, deny)
        p = rawtext.safe_resolve(cfg.source_root, lit.get("pdf_path") or "")
        if p is None:
            raise HTTPException(404, "这篇文献没有可用的 PDF 原文")
        open_type = lit.get("open_type") or ""
        fn = str(lit.get("pdf_name") or p.name)
    finally:
        conn.close()
    headers = {}
    if download:
        headers = attachment_headers("%s_原文.pdf" % lit_id,
                                    "LIT-%s.pdf" % lit_id.replace("LIT-", ""))
    else:
        # ★ 内嵌阅读：文件名可能是中文 → 必须走 RFC 5987（latin-1 放不下中文）
        headers = {"Content-Disposition": "inline; filename*=UTF-8''%s"
                                         % quote(fn, safe="")}
    headers["X-LZKG-Open-Type"] = open_type.encode("ascii", "ignore").decode() or "unknown"
    return FileResponse(p, media_type="application/pdf", headers=headers)


@router.get("/literatures/{lit_id}/xml")
def literature_xml(lit_id: str):
    """这一篇的**结构化 XML 视图**（与交换格式同一套定义）。"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM literatures WHERE lit_id = ?", (lit_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"未找到文献 {lit_id}")
        lit = dict(row)
        images = [dict(r) for r in conn.execute(
            "SELECT * FROM images WHERE lit_id = ? ORDER BY rowid", (lit_id,))]
        tags: dict[str, list] = {"period": [], "region": [], "topic": [], "lineage": []}
        for r in conn.execute(
                "SELECT t.tag_id, t.dim, t.name, t.code FROM lit_tags tt "
                "JOIN tags t ON t.tag_id = tt.tag_id WHERE tt.lit_id = ?", (lit_id,)):
            tags.setdefault(r["dim"], []).append(
                {"tag_id": r["tag_id"], "name": r["name"], "code": r["code"]})
    finally:
        conn.close()
    xml = xmlexchange.literature_to_xml(lit, images=images, tags=tags)
    return Response(content=xml, media_type="application/xml; charset=utf-8")


@router.get("/export/xml")
def export_xml(lit_ids: str = "", q: str = "", doc_type: str = "", lang: str = "",
                year: str = "", period: str = "", region: str = "", topic: str = "",
                lineage: str = "", user=Depends(require_login)):
    """**协作交换**：导出 XML 数据集。

    · `lit_ids` 给了 → 只导这些（逗号分隔）。
    · 否则按**与文献页完全相同的筛选口径**导出（`q` / 各维度标签），
      所以「导出当前筛选结果」不会与页面上看到的不一致 —— 两处口径同源。
    """
    conn = connect()
    try:
        if lit_ids.strip():
            ids = [x.strip() for x in lit_ids.split(",") if x.strip()]
            qm = ",".join("?" * len(ids))
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM literatures WHERE lit_id IN (%s) ORDER BY lit_id" % qm, ids)]
        else:
            # ★ 复用 app.py 的 `_query_lits` —— 同一套筛选口径，不另写一份。
            #   `all_rows=True` 必须给：否则 size 被压到 100 条上限，导出会少一大半。
            from .app import _query_lits   # 惰性导入：app.py 要 import 本模块
            params = {"q": q, "doc_type": doc_type, "lang": lang, "year": year,
                      "period": period, "region": region, "topic": topic,
                      "lineage": lineage, "open_type": "", "source_type": "",
                      "sort": "lit_id", "page": 1, "size": 20, "all_rows": True}
            items, _total, _mode = _query_lits(conn, params)
            rows = items
        images_by_lit: dict[str, list] = {}
        tags_by_lit: dict[str, dict] = {}
        for lit in rows:
            lid = lit["lit_id"]
            images_by_lit[lid] = [dict(r) for r in conn.execute(
                "SELECT * FROM images WHERE lit_id = ? ORDER BY rowid", (lid,))]
            tg: dict[str, list] = {"period": [], "region": [], "topic": [], "lineage": []}
            for r in conn.execute(
                    "SELECT t.dim, t.name, t.code FROM lit_tags tt "
                    "JOIN tags t ON t.tag_id = tt.tag_id WHERE tt.lit_id = ?", (lid,)):
                tg.setdefault(r["dim"], []).append({"name": r["name"], "code": r["code"]})
            tags_by_lit[lid] = tg
    finally:
        conn.close()
    xml = xmlexchange.dataset_to_xml(rows, images_by_lit, tags_by_lit)
    return Response(content=xml, media_type="application/xml; charset=utf-8",
                    headers=attachment_headers("良渚文献协作交换.xml", "liangzhu-exchange.xml"))


@router.post("/import/xml/preview")
async def import_xml_preview(file: UploadFile = File(...),
                             user=Depends(require_admin)):
    """回传的 XML → **预检**：逐条 diff，**不写主库**。

    → 与 xlsx 导入同一套「提案—审核」链路：差异写进 `staging`，
      管理员在「采集与协作」里批准后才入库。
    """
    data = await file.read()
    try:
        parsed = xmlexchange.parse_dataset_xml(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    records = parsed["records"]
    if not records:
        raise HTTPException(400, "XML 里没有解析出任何文献记录")
    conn = connect()
    try:
        diffs = []
        for rec in records:
            lid = rec.get("lit_id") or ""
            if not lid:
                continue
            cur = conn.execute("SELECT * FROM literatures WHERE lit_id = ?", (lid,)).fetchone()
            changed = []
            if cur is None:
                changed.append({"field": "lit_id", "old": "", "new": lid, "kind": "insert"})
            else:
                old = dict(cur)
                for key, _label in xmlexchange.EXCHANGE_FIELDS:
                    if key == "lit_id":
                        continue
                    nv = rec.get(key)
                    if nv is None:
                        continue
                    ov = str(old.get(key) or "").strip()
                    if nv.strip() != ov:
                        changed.append({"field": key, "old": ov, "new": nv.strip(),
                                        "kind": "update"})
            diffs.append({"lit_id": lid, "changed": changed,
                          "n_changed": len(changed),
                          "status": "insert" if cur is None else
                                    ("changed" if changed else "same")})
    finally:
        conn.close()
    return ok({
        "filename": file.filename or "",
        "count": len(records),
        "errors": parsed["errors"],
        "diffs": diffs,
        "n_changed": sum(1 for d in diffs if d["n_changed"]),
        "note": "预检结果**未写主库**；改动请走「提案—审核」链路入库。",
    })
