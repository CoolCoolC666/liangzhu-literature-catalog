# -*- coding: utf-8 -*-
"""鉴权与账号：scrypt 密码哈希 + 会话 token + 两道权限锁。

契约（`_docs/接口契约-v2.md` §0.1 / §3.1）
-----------------------------------------
* 密码哈希：**标准库** `hashlib.scrypt`，格式 `scrypt$n$r$p$salt$dk`
  （salt / dk 用 hex）。零第三方依赖。
* token 放请求头 **`X-Auth-Token`**。
* **本机默认免登录**：`config.json` 的 `auth.enabled=false`（默认）时，
  所有人视为 `admin` 免登录身份，方便本机演示；`auth.enabled=true`
  才走真实账号体系。
* **两道锁**：接口层 `require_admin` —— 手工构造请求也只能拿到 403；
  界面层由前端不渲染管理入口（前端的事，后端不越位）。

★ 为什么失败也返回 `{"ok":false,"error":...}` 而不是 FastAPI 默认的 `{"detail":...}`：
  契约 §0 规定的失败信封就是这样。但**旧端点**（/api/literatures 等）保持
  原有 `HTTPException` 行为不动 —— 不改既有行为，只约束新端点。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .common import CST, now_str
from .config import get_config


# ── 两道权限锁 ─────────────────────────────────────────────────────────
# ★ 放在 `auth.py` 而不是 `api_v2.py`：
#   鉴权是**跨模块**的基础设施（原文资源、XML 协作、MCP… 都要用）。
#   留在 api_v2 里会让新模块反向 import api_v2 → 循环依赖。
def require_admin(x_auth_token: str = ""):
    """管理端点的接口层锁。

    * `auth.enabled=false`（本机默认）→ 所有人视为 admin 免登录身份；
    * 未登录 → 401；登录了但不是 admin → **403**。
    """
    p = resolve_principal(x_auth_token or "")
    if p is None:
        raise AuthError(401, "请先登录")
    if not p.is_admin:
        raise AuthError(403, "需要管理员权限")
    return p


def require_login(x_auth_token: str = ""):
    """需要登录（可以是 member）。未登录 → 401。"""
    p = resolve_principal(x_auth_token or "")
    if p is None:
        raise AuthError(401, "请先登录")
    return p


class AuthError(Exception):
    """鉴权失败。→ (状态码, 人话说明)

    ★ 为什么不直接用 HTTPException：`auth.py` 不该依赖 web 层。
      端点层（api_v2 / api_raw）捕获它并转成 `ApiError`。
    """

    def __init__(self, status_code: int, error: str):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
from .db import connect

# scrypt 参数：n=2**14, r=8, p=1（标准库的推荐交互参数，内存 ~16MB）
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


# ── 密码哈希 ────────────────────────────────────────────────────────────
def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R,
                  p: int = SCRYPT_P, dklen: int = SCRYPT_DKLEN) -> str:
    """→ `scrypt$n$r$p$salt$dk`（salt/dk 为 hex）。"""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验哈希。**格式不对或参数非法一律 False**（不抛异常给调用方）。"""
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"),
                            salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── 会话 ────────────────────────────────────────────────────────────────
@dataclass
class Principal:
    """当前请求的操作者。"""
    user_id: int | None
    username: str
    role: str          # admin | member
    token: str = ""
    implicit: bool = False   # True = 免登录模式下的隐式 admin

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def as_dict(self) -> dict:
        return {"username": self.username, "role": self.role,
                "implicit_login": self.implicit}


IMPLICIT_ADMIN = Principal(user_id=None, username="local", role="admin", implicit=True)


def _expiry(days: int) -> str:
    return (datetime.now(CST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def ensure_bootstrap_user(conn: sqlite3.Connection) -> dict:
    """确保引导账号存在（`config.json` 的 `auth.bootstrap`）。

    ★ 只有**登录时**才会用到；`auth.enabled=false` 的本机演示模式不需要账号。
    引导账号的明文密码只存在于 config.json（默认 `CHANGE-ME-AT-FIRST-LOGIN`），
    **首次登录后必须提示改密码**（见 `must_change_password`）。
    """
    cfg = get_config()
    boot = cfg.auth_bootstrap or {}
    username = (boot.get("username") or "admin").strip()
    password = boot.get("password") or "CHANGE-ME-AT-FIRST-LOGIN"
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (username, pass_hash, role, status, created_at) "
            "VALUES (?,?,?,?,?)",
            (username, hash_password(password), "admin", "active", now_str()))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row)


def login(username: str, password: str) -> tuple[str | None, dict, str]:
    """校验账号。→ (token, user, 错误说明)。token 为 None 表示失败。

    ★ 账号不存在与密码错误**给同一条错误信息**：不泄露「这个用户名存在」。
    """
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            # 引导账号可能是第一次用
            ensure_bootstrap_user(conn)
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(password, row["pass_hash"]):
            return None, {}, "用户名或密码不正确"
        if row["status"] != "active":
            return None, {}, "账号未激活，请联系管理员"
        token = secrets.token_urlsafe(32)
        cfg = get_config()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, row["id"], now_str(), _expiry(cfg.auth_session_days)))
        conn.commit()
        user = {"username": row["username"], "role": row["role"]}
        return token, user, ""
    finally:
        conn.close()


def logout(token: str) -> None:
    if not token:
        return
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def provision_from_email(email: str, display_name: str = "") -> tuple[str, dict]:
    """按**已验证的邮箱**开一个本站账号（开放注册）。→ (token, user)

    ★ 前提：邮箱**已经由 Cloudflare Access 用 PIN 码验证过**（见 `lzkg/access.py`）。
      本站**不再发第二封验证邮件**——我们没有 SMTP，也没必要：
      Access 那一层已经把邮箱验真了，再验一遍只是多一次发信、多一个失败点。

    ★ 密码设成**随机值**：这个账号**不能用密码登录**，
      只能凭 Access 身份进来。想用密码登录的话，自己在「工作台」里设一个。
      （这样也避免了「开放注册」给每个邮箱留一个可猜的口令。）

    ★ 角色默认 `member`：管理员由管理员在后台改，
      **不会因为「邮箱验证通过」就自动给 admin**。
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("邮箱格式不对")
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (email,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (username, pass_hash, role, status, created_at) "
                "VALUES (?,?,?,?,?)",
                (email, hash_password(secrets.token_urlsafe(24)), "member", "active", now_str()))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE username = ?", (email,)).fetchone()
        if row["status"] != "active":
            raise ValueError("该邮箱的账号已被停用，请联系管理员")
        token = secrets.token_urlsafe(32)
        cfg = get_config()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, row["id"], now_str(), _expiry(cfg.auth_session_days)))
        conn.commit()
        return token, {"username": row["username"], "role": row["role"],
                       "email": email, "display_name": display_name}
    finally:
        conn.close()


def set_password_for_username(username: str, new_password: str) -> None:
    """给指定用户名直接设一个口令（开放注册时顺手设本站口令用）。"""
    if not new_password or len(new_password) < 6:
        raise ValueError("密码至少 6 位")
    conn = connect()
    try:
        conn.execute("UPDATE users SET pass_hash = ? WHERE username = ?",
                     (hash_password(new_password), username))
        conn.commit()
    finally:
        conn.close()


def principal_for_token(token: str) -> Principal | None:
    """token → 操作者。无效/过期返回 None。"""
    if not token:
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT s.token, s.expires_at, u.id AS uid, u.username, u.role, u.status "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < now_str():
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        if row["status"] != "active":
            return None
        return Principal(user_id=row["uid"], username=row["username"],
                         role=row["role"], token=token)
    finally:
        conn.close()


def resolve_principal(token: str) -> Principal | None:
    """本机免登录模式下，未带 token 也算隐式 admin。

    `auth.enabled=false` → 所有人视为 admin 免登录身份（契约 §0.1）。
    """
    cfg = get_config()
    p = principal_for_token(token) if token else None
    if p is not None:
        return p
    if not cfg.auth_enabled:
        return IMPLICIT_ADMIN
    return None


def set_password(user_id: int, new_password: str) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE users SET pass_hash = ? WHERE id = ?",
                     (hash_password(new_password), user_id))
        conn.commit()
    finally:
        conn.close()


def change_password(username: str, old_password: str, new_password: str) -> tuple[bool, str]:
    """改密码。→ (成功, 说明)。"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            return False, "用户不存在"
        if not verify_password(old_password, row["pass_hash"]):
            return False, "原密码不正确"
        if not new_password or len(new_password) < 6:
            return False, "新密码至少 6 位"
        conn.execute("UPDATE users SET pass_hash = ? WHERE id = ?",
                     (hash_password(new_password), row["id"]))
        # 改密码后把该用户的所有会话作废（别人还拿着旧 token）
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def must_change_password(username: str, password: str) -> bool:
    """是否还在用引导密码（首次登录必须提示改密码）。"""
    boot = get_config().auth_bootstrap or {}
    return (username == (boot.get("username") or "admin")
            and password == (boot.get("password") or "CHANGE-ME-AT-FIRST-LOGIN"))
