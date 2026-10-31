# -*- coding: utf-8 -*-
"""F3 / P2 / P3 的新端点（路由层）。

★ 响应信封统一按契约 §0：成功 `{"ok":true,"data":{...}}`，
  失败 `{"ok":false,"error":"人话说明"}`（HTTP 状态码仍按语义给）。
  **旧端点（/api/overview 等）的响应结构不动** —— 不改既有行为。

★ 两道权限锁（契约 §0.1）：管理端点一律 `require_admin`，
  手工构造请求也只得到 403；界面层由前端不渲染管理入口。
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from pathlib import Path

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException,
                     Request, UploadFile)
from fastapi.responses import JSONResponse, Response

from . import importexport as ie
from . import statsapi
from .access import identity_from_headers
from .auth import (AuthError, change_password, login, logout,
                   must_change_password, provision_from_email,
                   resolve_principal, set_password, set_password_for_username)
from .auth import require_admin as _require_admin
from .auth import require_login as _require_login
from .common import attachment_headers, now_str, ok
from .config import get_config
from .db import connect, get_meta

router = APIRouter(prefix="/api", tags=["v2"])


def access_identity(request: Request) -> dict | None:
    """请求里带**已验签的** Cloudflare Access 身份吗？→ 身份 dict 或 None。

    ★ 验签在 `lzkg/access.py` 里做（用 Cloudflare 的公开公钥）。
      这里的头**不验签就不认**——请求头是能伪造的。
    """
    try:
        return identity_from_headers(request.headers)
    except Exception:
        return None


# ── 错误信封 ────────────────────────────────────────────────────────────
class ApiError(HTTPException):
    """带契约错误信封的异常（`{"ok":false,"error":"…"}`）。"""

    def __init__(self, status_code: int, error: str):
        super().__init__(status_code=status_code, detail=error)
        self.error = error


def _fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


def require_admin(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    """管理端点的接口层锁。

    * `auth.enabled=false`（本机默认）→ 所有人视为 admin 免登录身份；
    * 未登录 → 401；登录了但不是 admin → **403**。

    ★ 实现在 `auth.py`（跨模块基础设施），这里只把 `AuthError`
      转成本层的 `ApiError`，**对外行为完全不变**。
    """
    try:
        return _require_admin(x_auth_token)
    except AuthError as e:
        raise ApiError(e.status_code, e.error)


def require_login(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    """需要登录（可以是 member）。未登录 → 401。"""
    try:
        return _require_login(x_auth_token)
    except AuthError as e:
        raise ApiError(e.status_code, e.error)


def _local_check(request: Request) -> None:
    """仅本机可访问。

    ★ 判据**不能只看 `request.client.host`** —— 隧道/反代后面它恒为 127.0.0.1。
      直接复用 `app.py` 已实现的 `_is_local_request`（契约 §0.2 指定的规则），
      不另写一套。
    """
    from .app import _is_local_request
    if not _is_local_request(request):
        raise ApiError(403, "仅本机可访问")


# ══════════════════════════════════════════════════════════════════════
# F3 · 列定义（前端「在线录入」表单按列名动态渲染）
# ══════════════════════════════════════════════════════════════════════
def _mask_source_dir(text: str) -> str:
    """把说明文字里的**源目录名**脱敏（公开端点不回传源目录结构）。"""
    if not text:
        return text
    return text.replace("04_专题研究", "源数据目录")


@router.get("/registry/columns")
def registry_columns(kind: str = "literatures", variant: str = "full"):
    """返回登记表的列定义。

    ★ 前端**不要硬编码列名** —— 在线录入表单按这里的 `name` 动态渲染。
    """
    from .registry import VARIANT_FULL, VARIANT_MINIMAL, columns, norm_name
    kind = (kind or "literatures").strip()
    if kind not in ie.KIND_TABLES:
        raise ApiError(400, f"kind 必须是 {ie.KIND_TABLES} 之一")
    variant = VARIANT_FULL if variant == "full" else VARIANT_MINIMAL
    cols = columns(kind, variant)
    return ok({
        "kind": kind,
        "variant": variant,
        "columns": [{"no": c["no"],
                     # ★ `*` 是必填标记，不是列名的一部分（契约 §2.1）→
                     #   `name` 给前端当表单字段名用，`required` 单独表达必填。
                     "name": norm_name(c["name"]),
                     "raw_name": c["name"],
                     "required": bool(c.get("required")),
                     # ★ `help` 原文里有「04_专题研究」这类**源目录名**。
                     #   这是公开可读的端点，回传源目录名等于给访客送目录地图
                     #   （同 `/api/health/detail` 那条安全线）→ 这里把目录名脱敏，
                     #   但**保留 help 字段与说明结构**（契约 §0.0 规定的形状）。
                     "help": _mask_source_dir(c.get("help", "")),
                     "example": c.get("example", "")} for c in cols],
        "count": len(cols),
    })


# ══════════════════════════════════════════════════════════════════════
# F3 · 导入导出
# ══════════════════════════════════════════════════════════════════════
@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...),
                         kind: str = Form(...),
                         user=Depends(require_admin)):
    """登记表预检：逐行 diff，**不写主库**，把有变化的行写进 `staging`。"""
    kind = (kind or "").strip()
    if kind not in ie.KIND_TABLES:
        raise ApiError(400, f"kind 必须是 {ie.KIND_TABLES} 之一")
    suffix = ".xlsx"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (file.filename or ("upload" + suffix))
        data = await file.read()
        path.write_bytes(data)
        conn = connect()
        try:
            try:
                rep = ie.preview_import(conn, path, kind, file.filename or "",
                                        operator=user.username)
            except ValueError as e:
                raise ApiError(400, str(e))
            except KeyError as e:
                # 表头定位失败（表里没有主键列）
                raise ApiError(400, str(e).strip("'"))
            except Exception as e:
                raise ApiError(400, f"解析失败：{type(e).__name__}: {e}")
        finally:
            conn.close()
    return ok(rep)


@router.post("/import/apply")
async def import_apply(body: dict, user=Depends(require_admin)):
    """批准并写主库。`target_ids` 为空 → 该批次全部 pending 行。"""
    batch = (body.get("staging_id") or "").strip()
    if not batch:
        raise ApiError(400, "缺少 staging_id（批次号）")
    target_ids = body.get("target_ids") or []
    if not isinstance(target_ids, list):
        raise ApiError(400, "target_ids 必须是数组")
    conn = connect()
    try:
        rep = ie.apply_staging(conn, batch, [str(t) for t in target_ids],
                                operator=user.username,
                                reviewer=body.get("reviewer") or user.username,
                                note=body.get("note") or "")
    finally:
        conn.close()
    return ok(rep)


@router.get("/staging")
def staging_list(status: str = "", kind: str = "", page: int = 1, size: int = 50,
                 user=Depends(require_login)):
    conn = connect()
    try:
        return ok(ie.list_staging(conn, status=status, kind_=kind, page=page, size=size))
    finally:
        conn.close()


@router.post("/staging/{staging_id}/approve")
def staging_approve(staging_id: int, body: dict, user=Depends(require_admin)):
    """批准一条提案 → 写主库。已完成的不删除，只改 status。"""
    conn = connect()
    try:
        try:
            rep = ie.review_one(conn, staging_id, True,
                                reviewer=(body or {}).get("reviewer") or user.username,
                                note=(body or {}).get("note") or "")
        except KeyError as e:
            raise ApiError(404, str(e))
    finally:
        conn.close()
    return ok(rep)


@router.post("/staging/{staging_id}/reject")
def staging_reject(staging_id: int, body: dict, user=Depends(require_admin)):
    """驳回一条提案 → 只标状态，不写主库。"""
    conn = connect()
    try:
        try:
            rep = ie.review_one(conn, staging_id, False,
                                reviewer=(body or {}).get("reviewer") or user.username,
                                note=(body or {}).get("note") or "")
        except KeyError as e:
            raise ApiError(404, str(e))
    finally:
        conn.close()
    return ok(rep)


@router.get("/import/log")
def import_log(page: int = 1, size: int = 50, user=Depends(require_login)):
    conn = connect()
    try:
        return ok(ie.import_log_page(conn, page=page, size=size))
    finally:
        conn.close()


@router.get("/export/literatures.xlsx")
def export_literatures_xlsx(user=Depends(require_login)):
    """34 列全量 xlsx，结构沿用登记表（行1 标题/行2 说明/行3 空/行4 表头/行5+ 数据）。"""
    conn = connect()
    try:
        data = ie.export_literatures_xlsx(conn)
    finally:
        conn.close()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=attachment_headers("良渚文献基本信息登记表_导出.xlsx",
                                   "liangzhu-literatures.xlsx"))


@router.get("/export/images.xlsx")
def export_images_xlsx(user=Depends(require_login)):
    """26 列全量 xlsx，结构同上。"""
    conn = connect()
    try:
        data = ie.export_images_xlsx(conn)
    finally:
        conn.close()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=attachment_headers("良渚文献图像信息登记表_导出.xlsx",
                                   "liangzhu-images.xlsx"))


# ══════════════════════════════════════════════════════════════════════
# P2 · 账号
# ══════════════════════════════════════════════════════════════════════
@router.post("/auth/login")
def auth_login(body: dict):
    """登录。→ `{"token":…, "user":{…}}`；token 放请求头 `X-Auth-Token`。"""
    username = str((body or {}).get("username") or "").strip()
    password = str((body or {}).get("password") or "")
    if not username or not password:
        raise ApiError(400, "请填写用户名与密码")
    token, user, err = login(username, password)
    if not token:
        raise ApiError(401, err)
    data = {"token": token, "user": user}
    if must_change_password(username, password):
        data["must_change_password"] = True
        data["note"] = "当前仍在用初始密码，请尽快修改（POST /api/auth/password）。"
    return ok(data)


@router.post("/auth/logout")
def auth_logout(x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    logout(x_auth_token or "")
    return {"ok": True}


@router.get("/auth/me")
def auth_me(request: Request, x_auth_token: str = Header(default="", alias="X-Auth-Token")):
    """当前用户。

    ★ 三条来路，**优先级从高到低**：
      1. 带了本站 token（`X-Auth-Token`）→ 用它
      2. 带**已验签的** Cloudflare Access 身份 → **按邮箱开号（开放注册）**
      3. 本机免登录模式（`auth.enabled=false`）→ 隐式 admin
      4. 都没有 → 401
    """
    p = resolve_principal(x_auth_token or "")
    if p is not None and not p.implicit:
        return ok({"user": p.as_dict()})
    ident = access_identity(request)
    if ident:
        token, user = provision_from_email(ident["email"], ident.get("name") or "")
        return ok({"user": {"username": user["username"], "role": user["role"],
                            "email": user["email"], "via": "cloudflare-access"},
                   "token": token,
                   "note": "已按 Cloudflare Access 验证过的邮箱开通本站账号（开放注册）。"})
    if p is not None:
        return ok({"user": p.as_dict()})
    raise ApiError(401, "请先登录")


@router.get("/auth/access-identity")
def auth_access_identity(request: Request):
    """当前请求携带的、**已验签的** Cloudflare Access 身份。

    ★ 没有就是 401。**不验签的头一律不认**——请求头是任何人都能自己带的。
    """
    ident = access_identity(request)
    if not ident:
        raise ApiError(401, "没有已验证的 Cloudflare Access 身份（先在登录页用邮箱收 PIN 码登录）")
    return ok({"identity": ident,
               "note": "该邮箱已由 Cloudflare Access 用一次性 PIN 码验证。"})


@router.post("/auth/register")
def auth_register(body: dict, request: Request):
    """**开放注册**：用已被 Access 验证过的邮箱开一个本站账号。

    body: `{"email":"…"}`（可选 `{"password":"…"}` 顺手设一个本站口令）

    ★ 必须**同时**满足：
      1. 请求带着已验签的 Access 身份
      2. body 里的邮箱 == 该身份里的邮箱
      不满足 → 400，并说清差在哪一步。
    ★ 角色一律 `member`，**不会因为验证通过就给 admin**。
    """
    email = str((body or {}).get("email") or "").strip().lower()
    ident = access_identity(request)
    if not ident:
        raise ApiError(400, "还没有通过 Cloudflare Access 的邮箱验证："
                             "请先用这个邮箱在登录页收一次 PIN 码，再回来注册")
    if not email:
        email = ident["email"]
    if email != ident["email"]:
        raise ApiError(400, f"邮箱不匹配：本次验证的是 {ident['email']}")
    password = str((body or {}).get("password") or "").strip()
    try:
        token, user = provision_from_email(email, ident.get("name") or "")
    except ValueError as e:
        raise ApiError(400, str(e))
    if password:
        set_password_for_username(email, password)
    return ok({"token": token,
               "user": {"username": user["username"], "role": user["role"], "email": email},
               "note": "注册成功。角色是 member（管理员由管理员指定）。"})


@router.post("/auth/password")
def auth_password(body: dict, user=Depends(require_login)):
    """改密码。body: `{"old":"…","new":"…"}`。"""
    old = str((body or {}).get("old") or "")
    new = str((body or {}).get("new") or "")
    if not old or not new:
        raise ApiError(400, "请填写原密码与新密码")
    good, err = change_password(user.username, old, new)
    if not good:
        raise ApiError(400, err)
    return ok({"username": user.username, "changed_at": now_str()},
              note="密码已更新；该用户的所有会话已失效，需重新登录。")


# ══════════════════════════════════════════════════════════════════════
# P2 · 收藏
# ══════════════════════════════════════════════════════════════════════
def _fav_match_title_note(item: dict, tokens: list[str]) -> bool:
    """题名 / 备注的**本地快速筛**：所有词都要出现（AND），忽略大小写。"""
    hay = (item["title"] or "") + " " + (item["note"] or "")
    hay = hay.lower()
    return all(t.lower() in hay for t in tokens)


@router.get("/favorites")
def favorites_list(t: str = "", q: str = "", user=Depends(require_login)):
    """收藏列表 + **两层检索**。

    - `t`：**题名 / 备注**的本地快速筛（多词 AND，忽略大小写）
    - `q`：**全文检索**（题名 / 摘要 / 关键词 / 正文），**只在收藏集合内筛**

    ★ `q` 走全库检索再与收藏集合取交集 —— 收藏页**绝不能**返回没收藏的文献。
    ★ 两个都为空 → 返回全部收藏（与旧行为一致，向后兼容）。
    """
    conn = connect()
    try:
        uid = _uid(conn, user)
        rows = conn.execute(
            "SELECT f.lit_id, f.note, f.created_at, l.title, l.authors, l.year, "
            "       l.venue, l.keywords, l.abstract, l.open_type, l.doc_type "
            "FROM favorites f LEFT JOIN literatures l ON l.lit_id = f.lit_id "
            "WHERE f.user_id = ? ORDER BY f.created_at DESC, f.lit_id", (uid,)).fetchall()
        items = [{"lit_id": r["lit_id"], "title": r["title"] or "",
                  "note": r["note"] or "", "created_at": r["created_at"] or "",
                  "authors": r["authors"] or "", "year": r["year"] or "",
                  "venue": r["venue"] or "", "keywords": r["keywords"] or "",
                  "abstract": (r["abstract"] or "")[:300],
                  "open_type": r["open_type"] or "", "doc_type": r["doc_type"] or "",
                  "snippet": "", "matched": ""}
                 for r in rows]
        total = len(items)

        t_tokens = [x for x in re.split(r"[\s,，;；、]+", (t or "").strip()) if x]
        q = (q or "").strip()
        mode = ""
        snippets: dict[str, str] = {}

        if q:
            # 惰性导入：app.py 顶层要 import 本模块，直接 import 会循环依赖
            from .app import _match_lit_ids
            hits, mode = _match_lit_ids(conn, q)
            snippets = hits

        out = []
        for it in items:
            by_t = bool(t_tokens) and _fav_match_title_note(it, t_tokens)
            by_q = bool(q) and (it["lit_id"] in snippets)
            if t_tokens and not by_t:
                continue
            if q and not by_q:
                continue
            if by_t and by_q:
                it["matched"] = "both"
            elif by_t:
                it["matched"] = "title_note"
            elif by_q:
                it["matched"] = "full"
            it["snippet"] = snippets.get(it["lit_id"], "") or ""
            out.append(it)

        return ok({"items": out, "total": total, "matched": len(out),
                   "filters": {"t": (t or "").strip(), "q": q}, "mode": mode})
    finally:
        conn.close()


@router.post("/favorites/{lit_id}")
def favorites_add(lit_id: str, body: dict, user=Depends(require_login)):
    """收藏一篇文献。body: `{"note":""}`。"""
    conn = connect()
    try:
        lit = conn.execute("SELECT lit_id FROM literatures WHERE lit_id = ?",
                            (lit_id,)).fetchone()
        if lit is None:
            raise ApiError(404, f"未找到文献 {lit_id}")
        uid = _uid(conn, user)
        note = str((body or {}).get("note") or "")
        conn.execute(
            "INSERT INTO favorites (user_id, lit_id, created_at, note) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, lit_id) DO UPDATE SET note=excluded.note",
            (uid, lit_id, now_str(), note))
        conn.commit()
        row = conn.execute(
            "SELECT lit_id, note, created_at FROM favorites WHERE user_id=? AND lit_id=?",
            (uid, lit_id)).fetchone()
        return ok({"lit_id": lit_id, "title": "", "note": row["note"] or "",
                   "created_at": row["created_at"] or "", "favorited": True})
    finally:
        conn.close()


@router.delete("/favorites/{lit_id}")
def favorites_del(lit_id: str, user=Depends(require_login)):
    """取消收藏。"""
    conn = connect()
    try:
        uid = _uid(conn, user)
        conn.execute("DELETE FROM favorites WHERE user_id=? AND lit_id=?", (uid, lit_id))
        conn.commit()
        return ok({"lit_id": lit_id, "favorited": False})
    finally:
        conn.close()


def _uid(conn: sqlite3.Connection, user) -> int:
    """收藏需要 user_id；免登录模式下用一个固定的本地用户（uid=0）。"""
    if getattr(user, "user_id", None) is not None:
        return int(user.user_id)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (user.username,)).fetchone()
    if row:
        return int(row["id"])
    return 0


# ══════════════════════════════════════════════════════════════════════
# P2 · 在线录入（提案式：只写 staging，不写主库）
# ══════════════════════════════════════════════════════════════════════
@router.post("/entry")
async def entry_create(request: Request, user=Depends(require_login)):
    """在线录入一条文献 → 写 `staging`（kind='literatures', status='pending'）。

    ★ **不直接写主库** —— 这正是「提案—审核制」的含义。
    字段按**列名**给（不按列序）：multipart 表单或 JSON 都行。
    """
    values: dict = {}
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            values = await request.json()
        except Exception:
            raise ApiError(400, "JSON 解析失败")
        if not isinstance(values, dict):
            raise ApiError(400, "body 必须是对象")
    else:
        form = await request.form()
        for k in form.keys():
            v = form[k]
            values[k] = v if isinstance(v, str) else str(v)
    if not values:
        raise ApiError(400, "没有收到任何字段")
    conn = connect()
    try:
        rep = ie.create_entry(conn, "literatures", values, proposer=user.username)
    finally:
        conn.close()
    return ok(rep)


# ══════════════════════════════════════════════════════════════════════
# P2 · 统计 / 地图 / 遗址点位
# ══════════════════════════════════════════════════════════════════════
@router.get("/stats/timeline")
def stats_timeline():
    conn = connect()
    try:
        return ok(statsapi.stats_timeline(conn))
    finally:
        conn.close()


@router.get("/stats/region")
def stats_region():
    conn = connect()
    try:
        return ok(statsapi.stats_region(conn))
    finally:
        conn.close()


@router.get("/stats/map.png")
def stats_map():
    """后端离线出图（matplotlib PNG）。

    ★ 坐标补齐前自动退化成「空间分区文献数」柱状图，图上注明「坐标待补，当前显示分区统计」。
    """
    conn = connect()
    try:
        png, mode = statsapi.map_png(conn)
    finally:
        conn.close()
    return Response(content=png, media_type="image/png",
                    headers={"X-Chart-Mode": mode})


@router.get("/stats/mapdata")
def stats_mapdata():
    """交互地图的**点级数据**：地图上每个点 + 它关联的文献。

    ★ 为什么要它
      `/stats/map.png` 是**定死的一张图** —— 前端贴上去就完了，点不动、悬停不了，
      也没法「点某个遗址 → 看对应文献」。
      交互的前提是前端能拿到点级数据、自己决定画什么，
      所以这里把点与关联文献现算给前端。

    ★ 口径都写在 `notes` 里
      分区按标签精确关联；坐标点与候选遗址按题名/摘要/关键词/刊名文本命中。
    """
    conn = connect()
    try:
        return ok(statsapi.map_data(conn))
    finally:
        conn.close()


@router.get("/sites")
def sites():
    """遗址点位表（坐标等人工补，**不编造**）。"""
    conn = connect()
    try:
        return ok(statsapi.sites_payload(conn))
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# v3.3 · Web Dev（WebDAV）—— 把**自己的浏览数据**同步到**自己的**网盘
# ══════════════════════════════════════════════════════════════════════
# ★ 为什么要有这个
#   组员想把自己的浏览记录留下来（换设备还能接着看）。
#   浏览记录存在各自浏览器的 localStorage 里 → 换机器就没了。
#   于是给一条路：同步到**自己**的 WebDAV 网盘（坚果云 / 自建 Nextcloud 等），
#   数据还是自己的，站点只是**中转**。
#
# ★ 三条边界（界面与文档里都要写清）
#   ① 账号/密码存在**用户自己的浏览器**里，本站**不保存**任何人的 WebDAV 凭据
#   ② 但测试/上传/下载时，密码会随请求发给**本站后端**再转发给 WebDAV 服务器
#      （浏览器直连会被 CORS 挡死，这是 WebDAV 的现实）→ 后端不落盘、不写日志、不回显
#   ③ 后端**只允许公网地址**（`lzkg/webdav.py` 里有 SSRF 防护）
#
# ★ 总闸（两道锁，照既有的权限纪律）
#   `config.webdev.enabled=false` 时：
#     · 界面层：前端不渲染 Web Dev 入口
#     · 接口层：/api/webdav/* 一律 403（手构造请求也一样）
def _webdev_on() -> bool:
    from .config import get_config
    return get_config().webdev_enabled


@router.get("/webdev/status")
def webdev_status():
    """这个功能开没开（所有人可读；前端据此决定是否渲染入口）。"""
    from .config import get_config
    cfg = get_config()
    return ok({
        "enabled": cfg.webdev_enabled,
        "note": "Web Dev 总闸由管理员控制；各人的 WebDAV 凭据存在各自浏览器里，本站不保存。",
    })


@router.post("/webdev/toggle")
def webdev_toggle(body: dict, user=Depends(require_admin)):
    """管理员开关 Web Dev（整体开/关）。body: `{"enabled": true|false}`。"""
    from .config import get_config
    cfg = get_config()
    want = (body or {}).get("enabled")
    if want is None:
        raise ApiError(400, "缺少 enabled（true/false）")
    wd = cfg.data.setdefault("webdev", {})
    wd["enabled"] = bool(want)
    cfg.save()
    return ok({"enabled": bool(want),
               "message": ("Web Dev 已开启。" if wd["enabled"]
                           else "Web Dev 已关闭：入口不再渲染，接口一律 403。")})


@router.post("/webdav/test")
def webdav_test(body: dict, user=Depends(require_login)):
    """测试 WebDAV 连接（PROPFIND **最终目标目录**，不是根目录！）。

    ★ 为什么测目标目录：坚果云根目录能读、但不能写 →
      只测根目录会出现「测试通过、上传却 404」的迷惑现象。
    """
    if not _webdev_on():
        raise ApiError(403, "Web Dev 已被管理员关闭")
    from . import webdav
    b = body or {}
    try:
        return ok(webdav.test_connection(b.get("url", ""), b.get("user", ""),
                                          b.get("password", ""),
                                          b.get("folder", "")))
    except webdav.WebDavError as e:
        raise ApiError(400, str(e))


@router.post("/webdav/put")
def webdav_put(body: dict, user=Depends(require_login)):
    """把一份文本（浏览数据 JSON）上传到 WebDAV（目录不存在会自动创建）。"""
    if not _webdev_on():
        raise ApiError(403, "Web Dev 已被管理员关闭")
    from . import webdav
    b = body or {}
    content = b.get("content")
    if not isinstance(content, str):
        raise ApiError(400, "content 必须是字符串（浏览数据 JSON）")
    try:
        return ok(webdav.put_file(b.get("url", ""), b.get("user", ""),
                                  b.get("password", ""),
                                  b.get("name", "lzkg-browse.json"), content,
                                  b.get("folder", "")))
    except webdav.WebDavError as e:
        raise ApiError(400, str(e))


@router.post("/webdav/get")
def webdav_get(body: dict, user=Depends(require_login)):
    """从 WebDAV 下载一份文本（浏览数据 JSON）。"""
    if not _webdev_on():
        raise ApiError(403, "Web Dev 已被管理员关闭")
    from . import webdav
    b = body or {}
    try:
        return ok(webdav.get_file(b.get("url", ""), b.get("user", ""),
                                  b.get("password", ""),
                                  b.get("name", "lzkg-browse.json"),
                                  b.get("folder", "")))
    except webdav.WebDavError as e:
        raise ApiError(400, str(e))


# ══════════════════════════════════════════════════════════════════════
# 浏览记录「存本站服务器」—— 零配置的那条同步路
#
# ★ 为什么还要这条路（WebDAV 已经在跑了）
#   WebDAV 要每人自己去坚果云开第三方应用、生成应用密码、填四项配置，
#   对只想「浏览过什么别丢」的同学太重。这条路什么都不用配。
#
# ★ 两条路是**并行**的，不是替代：
#   · 存服务器 → 本站磁盘 `data/record/<身份>/`，站开着就能用
#   · WebDAV   → 自己的网盘，站关了也还在
#   前端按「谁配了就用谁」选目标：配了 WebDAV 走网盘，没配就走服务器。
#
# ★ 权限与隔离
#   · `require_login`：每个登录用户都能存**自己**那份（不需要管理员逐个开）
#   · 目录名由后端从身份推导，客户端传什么都进不去（见 lzkg/browserec.py）
#   · 读接口只返回调用者自己那一份，没有「按用户名读别人」的入口
#   · 管理员总闸 `config.browse_server.enabled=false` → 接口一律 403
# ══════════════════════════════════════════════════════════════════════
def _browse_server_on() -> bool:
    from .config import get_config
    return get_config().browse_server_enabled


@router.get("/browse/server/status")
def browse_server_status(user=Depends(require_login)):
    """这个身份的服务器记录状态（存没存过、多少条、上次什么时候）。"""
    from . import browserec
    on = _browse_server_on()
    if not on:
        return ok({"enabled": False, "usable": False,
                   "note": "管理员已关闭「存本站服务器」；浏览记录只存在你自己浏览器里，"
                           "要跨设备可用请配 Web Dev（WebDAV）。"})
    try:
        st = browserec.status(user)
    except browserec.BrowseRecError as e:
        raise ApiError(400, str(e))
    st["enabled"] = True
    st["note"] = "记录存在本站你自己的目录里；接口只返回你自己的那份。"
    return ok(st)


@router.post("/browse/server")
def browse_server_save(body: dict, user=Depends(require_login)):
    """把**自己的**浏览记录写到服务器（覆盖，旧版留成 browse.prev.json）。"""
    from . import browserec
    if not _browse_server_on():
        raise ApiError(403, "管理员已关闭「存本站服务器」")
    try:
        return ok(browserec.save(user, body or {}))
    except browserec.BrowseRecError as e:
        raise ApiError(400, str(e))


@router.get("/browse/server")
def browse_server_load(user=Depends(require_login)):
    """读回**自己的**那份服务器记录。"""
    from . import browserec
    if not _browse_server_on():
        raise ApiError(403, "管理员已关闭「存本站服务器」")
    try:
        return ok(browserec.load(user))
    except browserec.BrowseRecError as e:
        raise ApiError(400, str(e))


@router.post("/browse/server/toggle")
def browse_server_toggle(body: dict, user=Depends(require_admin)):
    """管理员开关「存本站服务器」。body: `{"enabled": true|false}`。"""
    from .config import get_config
    cfg = get_config()
    want = (body or {}).get("enabled")
    if want is None:
        raise ApiError(400, "缺少 enabled（true/false）")
    bs = cfg.data.setdefault("browse_server", {})
    bs["enabled"] = bool(want)
    cfg.save()
    return ok({"enabled": bool(want),
               "message": ("已开启：各人的浏览记录可存在本站自己的目录里。"
                           if bs["enabled"] else
                           "已关闭：接口一律 403，浏览记录只留各人浏览器里。")})


# ══════════════════════════════════════════════════════════════════════
# P2 · AI 问答
# ══════════════════════════════════════════════════════════════════════
@router.post("/ai/ask")
def ai_ask(body: dict):
    """AI 问答。没配 API key → 降级为本地 FTS5 检索摘要（`degraded=true`）。

    ★ body 可带 `provider` 块（浏览器里存的那套配置）：
      {"provider":"openai"|"anthropic"|"local","api_base":"…","api_key":"…","model":"…"}
      浏览器那套**优先**于服务端 config.json —— 每人带自己的 key，费用各归各人。
    ★ key 只在这一次请求里用完就走：**不落库、不写日志、不回显**。
    """
    from . import ai_providers
    question = str((body or {}).get("question") or "")
    top_k = int((body or {}).get("top_k") or 5)
    client_cfg = (body or {}).get("provider") or None
    conn = connect()
    try:
        rep = statsapi.ai_ask(conn, question, top_k, client_cfg=client_cfg)
    finally:
        conn.close()
    # ★ 回显前把 key 抹掉：万一 provider 块被原样带回来，也不能漏出去
    if isinstance(rep, dict):
        rep.pop("api_key", None)
    return ok(rep)


@router.get("/ai/providers")
def ai_providers_list():
    """可用的协议清单（前端下拉框从这里来，**不在前端硬编码**）。"""
    from . import ai_providers
    items = []
    for key, spec in ai_providers.PROVIDERS.items():
        items.append({
            "provider": key,
            "label": spec["label"],
            "default_base": spec["default_base"],
            "default_model": spec["default_model"],
            "needs_key": bool(spec["needs_key"]),
            "key_hint": spec["key_hint"],
        })
    return ok({"items": items,
               "note": "配置保存在你自己的浏览器里；提问时随请求发给本机后端代理，"
                       "后端不落盘、不写日志。公用电脑上不要填 key。"})


@router.post("/ai/test")
def ai_test(body: dict):
    """测一次最小调用，验证接口地址 / key / 模型名对不对。"""
    from . import ai_providers
    cfg = (body or {}).get("provider") or {}
    return ok(ai_providers.test_connection(cfg))


# ══════════════════════════════════════════════════════════════════════
# P3 · 体检
# ══════════════════════════════════════════════════════════════════════
@router.get("/diagnose")
def diagnose(request: Request, user=Depends(require_admin)):
    """一组可执行的体检项，每项带 ok / detail / fix。

    ★ **仅本机**（契约 §0.0 / §0.2）：内容含本机绝对路径。
      判据复用 `app.py` 的 `_is_local_request`（防隧道绕过）。
    ★ 还受**总闸** `config.json` 的 `public.local_detail` 控制：
      置 false 就把这类端点**整体关掉**（公网部署时推荐），
      不依赖「客户端地址判断」这种万一写错就失效的东西。
    """
    from .config import get_config as _cfg
    if not _cfg().local_detail_enabled:
        raise ApiError(403, "本机诊断端点已关闭（config.json 的 public.local_detail=false）")
    _local_check(request)
    conn = connect()
    try:
        items = statsapi.diagnose(conn)
    finally:
        conn.close()
    bad = [i for i in items if not i["ok"]]
    return ok({"items": items, "total": len(items), "failed": len(bad)},
              note=f"{len(items)} 项体检，{len(bad)} 项不通过。")


# ══════════════════════════════════════════════════════════════════════
# 契约 v3 §2 / §3 · MinerU 解析 → 提案 → 入库
# ══════════════════════════════════════════════════════════════════════
@router.get("/mineru/status")
def mineru_status(user=Depends(require_login)):
    """本机 MinerU 能力探测（**不含任何密钥**）。

    ★ `reachable` 是**真探一下**得到的：本地打 `/v1/health`，云端也打
      `/v1/health` 但**不带 key**（匿名探针只为知道地址通不通）。
      探不通 → `reachable:false` + 说明，**不抛 500**、不假装能用。
    """
    from . import mineru_parse
    return ok(mineru_parse.status_snapshot())


@router.post("/mineru/parse")
async def mineru_parse_start(file: UploadFile = File(...),
                             backend: str = Form("local"),
                             api_key: str = Form(""),
                             tier: str = Form(""),
                             pages: str = Form(""),
                             user=Depends(require_login)):
    """起一个**后台**解析任务 → 只回 `job_id`，前端轮询进度。

    ★ 解析可能几分钟 → **不挂在 HTTP 请求上**（后台线程跑）。
    ★ `api_key`（云端）只在这一次请求里用完就走：**不落库、不写日志、不回显**；
      异常信息里的 key 会被打码（`mineru_parse.scrub`）。
    """
    from . import mineru_parse
    name = Path(file.filename or "upload.pdf").name
    if not name.lower().endswith(".pdf"):
        raise ApiError(400, f"只支持 PDF 文件（这次上传的是 {Path(name).suffix or '无扩展名'}）")
    data = await file.read()
    if not data:
        raise ApiError(400, "上传的文件是空的")
    try:
        rep = mineru_parse.start_parse(data, name, backend=backend, api_key=api_key,
                                       tier=tier, pages=pages, proposer=user.username)
    except ValueError as e:
        raise ApiError(400, str(e))
    except Exception as e:
        raise ApiError(500, f"起解析任务失败：{mineru_parse.scrub(str(e), api_key)}")
    return ok(rep)


@router.get("/mineru/jobs")
def mineru_jobs(page: int = 1, size: int = 20, user=Depends(require_login)):
    """最近 N 次解析任务（**自己的在前**；管理员看全部）。"""
    from . import mineru_parse
    return ok(mineru_parse.list_jobs(proposer=user.username, is_admin=user.is_admin,
                                      limit=size, page=page))


@router.get("/mineru/jobs/{job_id}")
def mineru_job(job_id: str, user=Depends(require_login)):
    """单个解析任务的进度与结果。"""
    from . import mineru_parse
    job = mineru_parse.get_job(job_id)
    if not job:
        raise ApiError(404, f"没有这个解析任务：{job_id}")
    return ok(mineru_parse.job_view(job))


def _next_lit_id(conn: sqlite3.Connection) -> str:
    """下一个六位流水号（**现算**：库里最大号 +1，并避开已被提案占用的号）。"""
    n = 0
    row = conn.execute(
        "SELECT lit_id FROM literatures WHERE lit_id LIKE 'LIT-%' "
        "ORDER BY lit_id DESC LIMIT 1").fetchone()
    if row:
        m = re.search(r"(\d+)", row["lit_id"] or "")
        if m:
            n = int(m.group(1))
    while True:
        n += 1
        cand = f"LIT-{n:06d}"
        if conn.execute("SELECT 1 FROM literatures WHERE lit_id = ?", (cand,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM staging WHERE kind = 'parsed' AND target_id = ?",
                        (cand,)).fetchone():
            continue
        return cand


@router.post("/mineru/submit")
def mineru_submit(body: dict, user=Depends(require_login)):
    """把解析产物 + 题录合成**一条** `staging` 提案（kind='parsed'）。

    ★ **不写主库**（契约 v3 §3.1：批准前不动权威库）。
    ★ 产物此时仍在**暂存区**（`data/解析暂存/<job_id>/`），批准时才搬进源数据目录。
    """
    from . import mineru_parse
    b = body or {}
    job_id = str(b.get("job_id") or "").strip()
    if not job_id:
        raise ApiError(400, "缺少 job_id（先解析，再提交）")
    job = mineru_parse.get_job(job_id)
    if not job:
        raise ApiError(404, f"没有这个解析任务：{job_id}")
    if job.get("status") != "done":
        raise ApiError(400, f"解析还没完成（当前状态：{job.get('status')}），不能提交")
    record = b.get("record") or {}
    if not isinstance(record, dict) or not record:
        raise ApiError(400, "record 至少要有一项题录信息（键用登记表列名）")
    images = b.get("images") or []
    if not isinstance(images, list):
        raise ApiError(400, "images 必须是数组")
    lit_id = str(b.get("lit_id") or "").strip()
    artifacts = (job.get("result") or {}).get("artifacts") or []
    conn = connect()
    try:
        if lit_id:
            if conn.execute("SELECT 1 FROM literatures WHERE lit_id = ?",
                            (lit_id,)).fetchone():
                raise ApiError(400, f"文献 {lit_id} 已经在库里了，请换一个标识符")
        else:
            lit_id = _next_lit_id(conn)
        rep = ie.create_parsed_proposal(conn, job_id=job_id, lit_id=lit_id,
                                        record=record, images=images,
                                        artifacts=artifacts,
                                        source_file=job.get("file") or "",
                                        proposer=user.username)
    finally:
        conn.close()
    return ok(rep)
