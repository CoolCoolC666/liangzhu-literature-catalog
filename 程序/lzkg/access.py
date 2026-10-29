# -*- coding: utf-8 -*-
"""Cloudflare Access 身份断言的**验签**。

★ 这一层解决的是「开放注册」怎么落地
------------------------------------
用户拍板：邮箱验证走 Cloudflare Access，**并且**开放注册。
Access 那一层已经用一次性 PIN 码验证了邮箱；那么本站的账号体系就不该再去发一封
验证邮件（我们没有 SMTP），而应该**认 Access 已经验证过的那个邮箱**。

所以流程是：
    访客 → Cloudflare Access 登录页（输邮箱 → 收 PIN 码 → 通过）
         → 带 `Cf-Access-Jwt-Assertion` 头到达本站
         → 本站**验签**这个断言 → 取出邮箱 → 按邮箱开一个本站账号

★ 为什么必须验签，不能直接读 `Cf-Access-Authenticated-User-Email`
---------------------------------------------------------------
那两个头是**请求头**，任何人都能自己带上。
不验签就等于「任何人伪造一个邮箱头就能当任意成员登录」。
所以必须用 Cloudflare 的**公钥**验 JWT 签名：
公钥从 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` 取，
**这个地址是公开的、不需要任何凭据**，正好——本站不需要存任何 Cloudflare 密钥。

验签三件事：
    1. 签名（RS256，公钥）
    2. `aud` == 本站的 Access 应用 AUD（防止拿别的应用的断言来冒充）
    3. `exp` 未过期

任何一项不过 → **不认这个身份**（返回 None），不做任何降级放行。
"""
from __future__ import annotations

import json
import time
import urllib.request

from .config import get_config

# 公钥缓存：Cloudflare 的密钥轮换不频繁，缓存 1 小时足够
_CERTS_CACHE: dict = {"at": 0.0, "keys": {}}
CERTS_TTL = 3600.0


def _public_certs() -> dict[str, str]:
    """取 Access 公钥集合 → {kid: PEM}。取不到就返回空 dict。"""
    now = time.time()
    if now - _CERTS_CACHE["at"] < CERTS_TTL and _CERTS_CACHE["keys"]:
        return _CERTS_CACHE["keys"]
    cfg = get_config()
    pub = cfg.public_cfg or {}
    team = (pub.get("access_team_domain") or "").strip()
    if not team:
        return {}
    url = f"https://{team}/cdn-cgi/access/certs"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lzkg-access/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    keys: dict[str, str] = {}
    for k in (data.get("keys") or []):
        kid = k.get("kid")
        if not kid:
            continue
        try:
            import jwt as _jwt
            pem = _jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
            keys[kid] = pem
        except Exception:
            continue
    if keys:
        _CERTS_CACHE.update({"at": now, "keys": keys})
    return keys


def verify_assertion(token: str) -> dict | None:
    """验签 Access 的 JWT 断言 → payload；不通过返回 None。

    ★ 任何失败都返回 None，**不抛异常**——调用方只关心「有没有可信身份」。
    """
    if not token:
        return None
    cfg = get_config()
    pub = cfg.public_cfg or {}
    aud = (pub.get("access_aud") or "").strip()
    if not aud:
        return None
    keys = _public_certs()
    if not keys:
        return None
    try:
        import jwt as _jwt
        header = _jwt.get_unverified_header(token)
        kid = header.get("kid")
        pem = keys.get(kid)
        if not pem:
            return None
        payload = _jwt.decode(token, pem, algorithms=["RS256"], audience=aud,
                              options={"require": ["exp", "iat", "aud"]})
        return payload
    except Exception:
        return None


def identity_from_headers(headers) -> dict | None:
    """从请求头里取出**已验签**的 Access 身份 → {"email","name","sub"}；没有就 None。

    ★ 只信 `Cf-Access-Jwt-Assertion` 验签后的内容；
      `Cf-Access-Authenticated-User-Email` 只在**验签通过后**才作为补充信息读。
    """
    try:
        token = headers.get("cf-access-jwt-assertion") or headers.get("Cf-Access-Jwt-Assertion")
    except Exception:
        token = None
    if not token:
        return None
    payload = verify_assertion(token)
    if not payload:
        return None
    email = (payload.get("email") or "").strip().lower()
    if not email:
        # 某些策略下 email 在 custom claim 里
        email = (payload.get("custom_email") or "").strip().lower()
    if not email:
        return None
    return {
        "email": email,
        "name": (payload.get("name") or payload.get("custom_name") or "").strip(),
        "sub": (payload.get("sub") or "").strip(),
        "aud": payload.get("aud", ""),
    }


def access_enabled() -> bool:
    cfg = get_config()
    pub = cfg.public_cfg or {}
    return bool(pub.get("access_enabled")) and bool(pub.get("access_aud")) \
        and bool(pub.get("access_team_domain"))
