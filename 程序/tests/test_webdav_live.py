# -*- coding: utf-8 -*-
"""WebDAV 全链路实测（本地 mock 服务器，零外部依赖、不联网）。

★ 为什么需要这个脚本
  之前 WebDAV 只做了**失败路径**实测（私网拒绝 / 认证失败 / 404），
  成功路径（MKCOL 建目录 → PUT 上传 → GET 还原）只做了代码级检查。
  而组员实测报的「上传失败：HTTP 404」恰恰发生在这条成功路径上，
  所以必须把它真正跑一次，不能只靠读代码。

★ mock 服务器刻意复刻坚果云的**两个真实行为**
  ① 根目录**可读不可写**：PUT 到 `/dav/x.json` → 404（不是 403、不是 409）
  ② **不自动创建父目录**：目录不存在时 PUT 到 `/dav/lzkg/x.json` 也是 404
  这两条正是当初踩到的坑；mock 不复刻就等于没测。

★ SSRF 怎么处理（不改生产代码）
  生产路径必须拒绝私网地址，但测试要连 127.0.0.1。
  这里 monkeypatch 模块级函数 `_assert_public_host`（只在本进程内生效），
  另有一条断言专门验证「不 patch 时私网确实被拒」，确保防护没被削弱。

用法：
  python tests/test_webdav_live.py
"""
from __future__ import annotations

import base64
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROG = Path(__file__).resolve().parent.parent
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

from lzkg import webdav  # noqa: E402

USER = "moonlight@example.com"
PASS = "app-token-9f3c"
AUTH_OK = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

# mock 的「文件系统」：目录集合 + 文件表
DIRS = {"/dav"}
FILES: dict[str, bytes] = {}
RECORD: list[tuple[str, str, int]] = []   # (method, path, status) 供断言用


def _norm(p: str) -> str:
    p = unquote(urlparse(p).path).rstrip("/")
    return p or "/"


class MockDav(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockDav/1.0"

    # ---- 基础工具 ----------------------------------------------------
    def log_message(self, *a):          # 静音：不给测试输出添噪
        pass

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
        RECORD.append((self.command, _norm(self.path), code))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authed(self) -> bool:
        if self.headers.get("Authorization") != AUTH_OK:
            self._reply(401)
            return False
        return True

    def _parent(self, path: str) -> str:
        return path.rsplit("/", 1)[0] or "/"

    # ---- 方法 --------------------------------------------------------
    def do_PROPFIND(self):
        if not self._authed():
            return
        path = _norm(self.path)
        if path in DIRS:
            xml = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<D:multistatus xmlns:D="DAV:"><D:response>'
                f"<D:href>{path}/</D:href><D:propstat><D:prop>"
                "<D:resourcetype><D:collection/></D:resourcetype>"
                "</D:prop><D:status>HTTP/1.1 200 OK</D:status>"
                "</D:propstat></D:response></D:multistatus>"
            )
            self._reply(207, xml.encode(), "application/xml; charset=utf-8")
            return
        if path in FILES:
            self._reply(207, b"<D:multistatus/>", "application/xml; charset=utf-8")
            return
        self._reply(404)

    def do_MKCOL(self):
        if not self._authed():
            return
        path = _norm(self.path)
        if path in DIRS:
            self._reply(405)                     # 已存在
            return
        if self._parent(path) not in DIRS:
            self._reply(409)                     # 父目录不存在
            return
        DIRS.add(path)
        self._reply(201)

    def do_PUT(self):
        # ★ 必须先读完 body 再判认证：401 时不读会把请求体留在
        #   keep-alive 连接里，污染同一个 client 的后续请求。
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if not self._authed():
            return
        path = _norm(self.path)
        # ★ 复刻坚果云：根目录（/dav）不允许直接写文件 → 404
        if self._parent(path) == "/dav":
            self._reply(404)
            return
        if self._parent(path) not in DIRS:
            self._reply(404)                     # 父目录不存在
            return
        FILES[path] = body
        self._reply(201)

    def do_GET(self):
        if not self._authed():
            return
        path = _norm(self.path)
        if path in FILES:
            self._reply(200, FILES[path], "application/octet-stream")
            return
        self._reply(404)


# ---- 断言工具 --------------------------------------------------------
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASSED.append(name)
        print(f"  [OK] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f"  → {detail}" if detail else ""))


def expect_err(name: str, fn, must_contain: str):
    """期望抛出 WebDavError，且消息里包含某个关键词。"""
    try:
        fn()
    except webdav.WebDavError as e:
        msg = str(e)
        check(name, must_contain in msg, f"消息里没有「{must_contain}」：{msg[:160]}")
        return
    except Exception as e:                       # noqa: BLE001
        check(name, False, f"抛的不是 WebDavError：{type(e).__name__}: {e}")
        return
    check(name, False, "本应报错，却成功了")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockDav)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}/dav"
    print(f"\n=== WebDAV 全链路实测（mock 服务器 {base}）===\n")

    real_guard = webdav._assert_public_host          # 存原函数，最后恢复

    # ---------- 1. 先证明 SSRF 防护本身有效（未 patch） ----------
    print("[1] SSRF 防护（未 patch，私网必须被拒）")
    expect_err("私网 127.0.0.1 被拒", lambda: webdav.check_url(base), "私网")
    expect_err("回环 127.0.0.1 被拒",
               lambda: webdav.check_url("http://127.0.0.1:8080/dav"), "不允许")
    expect_err("内网 192.168.x 被拒",
               lambda: webdav.check_url("http://192.168.1.10/dav"), "不允许")
    expect_err("file:// 协议被拒",
               lambda: webdav.check_url("file:///etc/passwd"), "http")
    expect_err("地址里带账号密码被拒",
               lambda: webdav.put_file(f"http://{USER}:{PASS}@127.0.0.1:{port}/dav",
                                       USER, PASS, "a.json", "{}", "lzkg"),
               "账号密码")

    # ---------- 2. 打补丁后跑成功路径 ----------
    webdav._assert_public_host = lambda host: None    # 仅本进程，仅本次测试
    try:
        print("\n[2] 复现组员的报错（不填存放目录 → 根目录不可写）")
        expect_err("不填存放目录 → 404 且提示填存放目录",
                   lambda: webdav.put_file(base, USER, PASS, "a.json", "{}", ""),
                   "存放目录")

        print("\n[3] 测试连接：目录未建时应说清「会自动创建」")
        expect_err("目标目录不存在 → 提示会自动创建",
                   lambda: webdav.test_connection(base, USER, PASS, "lzkg"),
                   "自动创建")
        check("根目录 PROPFIND 通过（可读不可写，正是坚果云行为）",
              webdav.test_connection(base, USER, PASS, "")["ok"] is True)

        print("\n[4] 上传：自动 MKCOL 建目录 → PUT")
        payload = '{"v":1,"items":[{"pid":"LIT-000122","title":"良渚遗址群调查简报"}]}'
        r1 = webdav.put_file(base, USER, PASS, "lzkg-browse-20261029.json",
                             payload, "lzkg")
        check("上传成功", r1.get("ok") is True, str(r1))
        check("目录状态 = created（确实是本次建出来的）",
              r1.get("dir_state") == "created", f"dir_state={r1.get('dir_state')}")
        check("服务端收到字节数与本地一致",
              FILES.get("/dav/lzkg/lzkg-browse-20261029.json", b"").decode("utf-8")
              == payload)
        check("中文内容没坏（UTF-8 往返一致）",
              "良渚遗址群调查简报" in
              FILES.get("/dav/lzkg/lzkg-browse-20261029.json", b"").decode("utf-8"))

        print("\n[5] 再次上传：目录已存在，不应重复创建也不应失败")
        r2 = webdav.put_file(base, USER, PASS, "lzkg-browse-20261029.json",
                             payload, "lzkg")
        check("第二次上传成功", r2.get("ok") is True, str(r2))
        check("目录状态 = exists（幂等）",
              r2.get("dir_state") == "exists", f"dir_state={r2.get('dir_state')}")

        print("\n[6] 下载还原：内容必须逐字一致")
        g1 = webdav.get_file(base, USER, PASS, "lzkg-browse-20261029.json", "lzkg")
        check("下载成功", g1.get("ok") is True, str(g1))
        check("内容逐字一致", g1.get("content") == payload)
        check("字节数一致", g1.get("bytes") == len(payload.encode("utf-8")))

        print("\n[7] 目录建好后，测试连接应转为通过")
        check("目标目录 PROPFIND 通过",
              webdav.test_connection(base, USER, PASS, "lzkg")["ok"] is True)

        print("\n[8] 边界：文件名与目录名的非法输入必须挡住")
        expect_err("文件名带斜杠 → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, "a/b.json", "{}", "lzkg"),
                   "文件名不合法")
        expect_err("文件名带反斜杠 → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, "x\\y.json", "{}", "lzkg"),
                   "文件名不合法")
        expect_err("隐藏文件 → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, ".token", "{}", "lzkg"),
                   "文件名不合法")
        expect_err("目录名带斜杠 → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, "a.json", "{}", "x/y"),
                   "存放目录不合法")
        expect_err("目录名以点开头 → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, "a.json", "{}", ".ssh"),
                   "存放目录不合法")

        print("\n[9] 边界：认证失败与文件过大")
        expect_err("密码错 → 认证失败",
                   lambda: webdav.put_file(base, USER, "wrong-pass", "a.json", "{}", "lzkg"),
                   "认证失败")
        expect_err("下载不存在的文件 → 404",
                   lambda: webdav.get_file(base, USER, PASS, "nope.json", "lzkg"),
                   "没有这个文件")
        expect_err("超过 2 MB → 拒绝",
                   lambda: webdav.put_file(base, USER, PASS, "big.json",
                                           "x" * (2 * 1024 * 1024 + 10), "lzkg"),
                   "文件太大")

        print("\n[10] 凭据不外泄：错误消息与返回里不能出现密码")
        leaked = False
        for fn in (lambda: webdav.put_file(base, USER, "wrong-pass", "a.json", "{}", "lzkg"),
                   lambda: webdav.test_connection(base, USER, "wrong-pass", "lzkg")):
            try:
                fn()
            except webdav.WebDavError as e:
                if "wrong-pass" in str(e):
                    leaked = True
        check("错误信息里没有密码原文", not leaked)
        check("返回体里 target 已打码/不含密码",
              PASS not in str(r1) and PASS not in str(g1))
    finally:
        webdav._assert_public_host = real_guard     # ★ 一定要恢复
        srv.shutdown()
        srv.server_close()

    # ---------- 收尾 ----------
    print("\n[11] 恢复后 SSRF 防护仍然生效")
    expect_err("patch 已恢复：私网再次被拒",
               lambda: webdav.check_url(base), "私网")

    print(f"\n=== 结果：{len(PASSED)} 通过 / {len(FAILED)} 失败 ===")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  - " + f)
        return 1
    print("全部通过：WebDAV 上传/下载成功路径已实测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())