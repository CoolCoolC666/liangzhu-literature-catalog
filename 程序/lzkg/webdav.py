# -*- coding: utf-8 -*-
"""WebDAV 客户端（后端代理）—— 让组员把**自己的浏览数据**同步到**自己的**网盘。

★ 为什么要后端代理，而不是浏览器直连
  浏览器的 `fetch` 跨源要过 **CORS**，而 WebDAV 服务（坚果云、绝大多数自建盘）
  **默认不发 CORS 头** → 网页里直连一定失败。
  所以只能由后端发请求（后端没有同源限制）。
  代价要说清：**密码会经过本站后端的内存**（不落盘、不写日志、不回显）。

★ 这个端点等于「一个能访问任意 URL 的代理」→ 必须防 SSRF
  否则任何登录用户都能让本站去扫内网、访问云厂商元数据接口。
  所以：
    ① 只允许 http/https
    ② 解析域名后逐个检查 IP：**私网 / 回环 / 链路本地 / 保留地址一律拒绝**
    ③ 不跟随重定向（避免 302 跳到内网）
    ④ 限制响应体大小（下载最大 2 MB）
    ⑤ 只支持 PROPFIND / PUT / GET 三种方法，不做任意方法透传

★ 凭据处理（与 AI key / MinerU token 同一套规矩）
  · 密码只在这一次请求里用完就走：**不落盘、不写日志、错误信息里打码**
  · 后端**不保存**任何人的 WebDAV 凭据
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

TIMEOUT = 30.0
MAX_DOWNLOAD = 2 * 1024 * 1024      # 下载上限 2 MB
MAX_UPLOAD = 2 * 1024 * 1024        # 上传上限 2 MB


class WebDavError(Exception):
    """对外只暴露安全信息（不含密码）。"""


def _mask(text: str) -> str:
    """把可能含密码的片段打码。"""
    if not text:
        return ""
    s = str(text)
    if "://" in s and "@" in s:
        head, _, tail = s.partition("://")
        if "@" in tail:
            tail = tail.split("@", 1)[1]
            s = head + "://***@" + tail
    return s[:200]


def _assert_public_host(host: str) -> None:
    """域名解析后，所有 IP 都必须是**公网地址**（防 SSRF）。"""
    if not host:
        raise WebDavError("地址里没有主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise WebDavError(f"域名解析失败：{host}") from e
    if not infos:
        raise WebDavError(f"域名解析失败：{host}")
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise WebDavError(f"无法识别的地址：{ip_text}")
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise WebDavError(
                f"目标 {ip_text} 属于私网/回环/保留地址，**不允许**通过本站访问"
                "（这是防 SSRF 的硬限制）：请填公网可访问的 WebDAV 地址。")


def check_url(url: str) -> str:
    """校验并规范化 WebDAV 根地址。返回去掉尾部斜杠的地址。"""
    url = (url or "").strip()
    if not url:
        raise WebDavError("请填 WebDAV 服务器地址")
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise WebDavError("地址必须以 http:// 或 https:// 开头")
    if u.username or u.password:
        raise WebDavError("请不要把账号密码写进地址里，用下面的账号/密码字段单独填")
    _assert_public_host(u.hostname or "")
    return url.rstrip("/")


def _auth(user: str, password: str) -> tuple[str, str]:
    return (user or "", password or "")


def _target_dir(base: str, folder: str) -> str:
    """服务器地址 + 存放目录 → 最终目录。

    ★ 为什么必须有「存放目录」
      坚果云**不允许直接往根目录写文件** —— PUT 到 `https://dav.jianguoyun.com/dav/x.json`
      会返回 **404**（不是 403，也不是 409）。
      实测与多方资料一致：WebDAV 服务一般**不会自动创建父目录**，
      文件必须放在**已存在的文件夹**里（Obsidian/Joplin 教程也都让加一层目录，
      否则同样报 404）。
      所以这里默认带一个目录名（`lzkg`），并在 PUT 前**自己 MKCOL 建出来**。
    """
    folder = (folder or "").strip().strip("/")
    if not folder:
        return base
    if "/" in folder or "\\" in folder or folder.startswith("."):
        raise WebDavError("存放目录不合法：只允许单层目录名（不要带斜杠或点开头）")
    return f"{base}/{folder}"


def _client() -> httpx.Client:
    # 不跟随重定向：302 到内网是最常见的 SSRF 绕过手法
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                        verify=True, trust_env=False)


def _basic(user: str, password: str) -> httpx.BasicAuth:
    return httpx.BasicAuth(user or "", password or "")


def _ensure_dir(c: httpx.Client, url: str, user: str, password: str) -> str:
    """确保目录存在（能建就建；已存在返回 'exists'）。"""
    try:
        r = c.request("MKCOL", url + "/", auth=_basic(user, password))
    except httpx.HTTPError:
        return "failed"
    if r.status_code in (201, 200):
        return "created"
    if r.status_code in (405, 301, 409):
        return "exists"
    return "failed"


def test_connection(url: str, user: str, password: str, folder: str = "") -> dict:
    """测试连接：PROPFIND **最终目标目录**（不是根目录！）。

    ★ 为什么测目标目录而不是根目录
      根目录 PROPFIND 成功 ≠ 能上传。坚果云根目录能读、但不能写 →
      于是出现「测试连接通过、上传却 404」的迷惑现象。
      测最终目标目录才能一次说清「这条路径到底通不通」。
    """
    base = check_url(url)
    target = _target_dir(base, folder)
    try:
        with _client() as c:
            r = c.request("PROPFIND", target + "/",
                          headers={"Depth": "0"}, auth=_basic(user, password))
    except httpx.HTTPError as e:
        raise WebDavError(f"连接失败：{type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise WebDavError("认证失败（401/403）：账号或**应用授权密码**不对。"
                          "坚果云要用「第三方应用管理」里生成的应用密码，不是账号密码。")
    if r.status_code == 404:
        raise WebDavError(
            "目标目录不存在（404）。"
            + ("坚果云不允许直接往根目录写文件 —— 请在「存放目录」里填一个文件夹名"
               "（如 lzkg），上传时会自动建出来；也可以先在坚果云网页端手动建好。"
               if not folder else
               f"目录「{folder}」不存在 —— 上传时会自动创建，也可以先在网盘网页端建好。"))
    if r.status_code in (405, 501):
        raise WebDavError(f"该地址不支持 WebDAV（{r.status_code}）。")
    if r.status_code >= 400:
        raise WebDavError(f"服务器返回 HTTP {r.status_code}")
    return {"ok": True, "status": r.status_code,
            "message": "连接正常，认证通过，目标目录可读。",
            "server": _mask(base),
            "target_dir": _mask(target)}


def put_file(url: str, user: str, password: str, name: str, content: str,
             folder: str = "") -> dict:
    """上传一个文件到 WebDAV（目录不存在会**自动创建**）。"""
    base = check_url(url)
    name = (name or "lzkg-browse.json").strip().lstrip("/")
    if "/" in name or "\\" in name or name.startswith("."):
        raise WebDavError("文件名不合法：只允许单层文件名")
    body = content.encode("utf-8")
    if len(body) > MAX_UPLOAD:
        raise WebDavError(f"文件太大（{len(body)//1024} KB），上限 2 MB")
    target_dir = _target_dir(base, folder)
    target = f"{target_dir}/{name}"
    with _client() as c:
        # 先确保目录存在（坚果云不会自动建父目录，缺了就是 404）
        state = _ensure_dir(c, target_dir, user, password)
        try:
            r = c.put(target, content=body, auth=_basic(user, password))
        except httpx.HTTPError as e:
            raise WebDavError(f"上传失败：{type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise WebDavError("认证失败（401/403）：账号或应用授权密码不对。")
    if r.status_code == 404:
        raise WebDavError(
            "上传路径不存在（404）。"
            + ("坚果云不允许直接往根目录写文件：请在「存放目录」填一个文件夹名"
               "（如 lzkg），本站在上传时会自动创建它。" if not folder else
               f"目录「{folder}」建不出来或不存在（状态：{state}）—— "
               "可在坚果云网页端手动建一个同名文件夹再试。"))
    if r.status_code == 409:
        raise WebDavError("目标冲突（409）：同名文件已存在，或父目录建不出来。")
    if r.status_code >= 400:
        raise WebDavError(f"上传失败：HTTP {r.status_code}")
    return {"ok": True, "status": r.status_code, "bytes": len(body),
            "target": _mask(target), "dir_state": state,
            "message": "已上传。"}


def get_file(url: str, user: str, password: str, name: str,
             folder: str = "") -> dict:
    """从 WebDAV 下载一个文件。"""
    base = check_url(url)
    name = (name or "lzkg-browse.json").strip().lstrip("/")
    if "/" in name or "\\" in name or name.startswith("."):
        raise WebDavError("文件名不合法：只允许单层文件名")
    target = f"{_target_dir(base, folder)}/{name}"
    with _client() as c:
        try:
            r = c.get(target, auth=_basic(user, password))
        except httpx.HTTPError as e:
            raise WebDavError(f"下载失败：{type(e).__name__}") from e
    if r.status_code == 404:
        raise WebDavError(
            "服务器上没有这个文件（404）。"
            + ("请确认「存放目录」与上传时一致。" if folder else
               "根目录下没有这个文件 —— 上传时若用了「存放目录」，这里也要填同样的目录名。"))
    if r.status_code in (401, 403):
        raise WebDavError("认证失败（401/403）：账号或应用授权密码不对。")
    if r.status_code >= 400:
        raise WebDavError(f"下载失败：HTTP {r.status_code}")
    if len(r.content) > MAX_DOWNLOAD:
        raise WebDavError("远端文件过大（超过 2 MB），拒绝下载。")
    return {"ok": True, "status": r.status_code, "content": r.text,
            "bytes": len(r.content), "message": "已下载。"}
