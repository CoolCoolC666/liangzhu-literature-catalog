# -*- coding: utf-8 -*-
"""匿名暴露面实测：量化「一个没登录的人能摸到什么」。

★ 为什么先量化再加固
-------------------
公网发布这条线上，最容易出的事是「以为锁了，其实没锁」：
只藏界面 → 改 URL 就能调；只锁接口 → 界面还在误导人。
所以要有**一份可复跑的清单**：每个端点匿名访问会返回什么、泄露了什么。

★ 本脚本**不做任何公网暴露**
--------------------------
它只在本机把服务拉起来、用**匿名请求**逐个打，然后按「泄露了什么」分类。
真要把隧道开到公网是另一件事，要先有人拍板。

用法：
    python 03/_tools/audit_exposure.py                 # 自己起一个临时服务（8798）来测
    python 03/_tools/audit_exposure.py --base http://127.0.0.1:8771   # 测已在跑的服务
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROGRAM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAM_ROOT))

# ── 泄露判定规则 ────────────────────────────────────────────────
# 每条规则 = (正则, 说明)。命中即认为「泄露了不该给匿名者的信息」。
LEAK_RULES = [
    (r"[A-Za-z]:\\\\?[^\\\"'\s]{4,}", "本机绝对路径"),
    (r"/Users/[^\s\"']+", "类 Unix 绝对路径"),
    (r"\.db\b", "数据库文件名"),
    (r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "精确时间戳"),
    (r"source_root|db_path|source_evidence", "内部字段名"),
    (r"04_专题研究", "数据源目录名"),
]
# 这些端点本来就该公开（公开只读目录）
ALLOWED_PUBLIC = {"/", "/api/health", "/robots.txt", "/favicon.ico"}


def _is_textual(content_type: str) -> bool:
    """这个响应是不是**文本**（能拿来做内容泄露扫描）。

    ★ 必须判一下，否则会**误报**：
      xlsx / png / zip 这类二进制响应被 decode 成 UTF-8 后是乱码，
      乱码里随机出现的字节组合会碰巧长得像 `X:\\...`（实测命中过 `P:\\^…`）
      → 报一个根本不存在的「本机绝对路径泄露」。
      误报比漏报更费时间：它会让人去查一个不存在的问题。
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct:
        return False
    if ct.startswith("text/"):
        return True
    if ct in ("application/json", "application/javascript", "application/xml",
              "application/xhtml+xml", "application/x-ndjson"):
        return True
    return False


def classify(path: str, status: int, body: str, headers: dict) -> dict:
    leaks = []
    content_type = headers.get("content-type", "")
    if _is_textual(content_type):
        for pat, desc in LEAK_RULES:
            m = re.search(pat, body)
            if m:
                leaks.append(desc)
    # ★ 响应头也要扫：正文是二进制的（比如 xlsx）不代表头里没有路径
    #   （Content-Disposition / Location 之类）。
    hdr_text = " ".join(f"{k}: {v}" for k, v in headers.items())
    for pat, desc in LEAK_RULES:
        if re.search(pat, hdr_text):
            leaks.append(desc + "（响应头）")
    xrobots = headers.get("x-robots-tag", "")
    return {
        "path": path,
        "status": status,
        "leaks": sorted(set(leaks)),
        "content_type": content_type,
        "noindex": "noindex" in (xrobots or "").lower(),
        "size": len(body),
    }


def routes_of(app) -> list[str]:
    out = []
    for r in app.routes:
        p = getattr(r, "path", None)
        m = getattr(r, "methods", None)
        if not p or not m:
            continue
        if "GET" in m:
            out.append(p)
    return sorted(set(out))


def probe(base: str, paths: list[str], timeout: int = 60,
          extra_headers: dict | None = None) -> list[dict]:
    import urllib.request
    import urllib.error
    res = []
    for p in paths:
        url = base + p
        t0 = time.time()
        try:
            hdr = {"User-Agent": "lzkg-exposure-audit/1.0"}
            hdr.update(extra_headers or {})
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                body = raw.decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            body = (e.read() or b"").decode("utf-8", "replace")
            status = e.code
        except Exception as e:
            res.append({"path": p, "status": 0, "leaks": [], "noindex": False,
                        "size": 0, "error": str(e)[:120], "ms": round((time.time() - t0) * 1000)})
            continue
        row = classify(p, status, body, hdrs)
        row["ms"] = round((time.time() - t0) * 1000)
        res.append(row)
    return res


def main() -> int:
    import requests
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--port", type=int, default=8798)
    a = ap.parse_args()

    base = a.base
    proc = None
    if not base:
        import subprocess
        import os
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROGRAM_ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LZKG_PORT"] = str(a.port)
        proc = subprocess.Popen([sys.executable, "-m", "lzkg", "serve"],
                                cwd=str(PROGRAM_ROOT), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{a.port}"
        for _ in range(60):
            try:
                if requests.get(base + "/api/health", timeout=3).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("[X] 临时服务起不来")
            return 2

    try:
        from lzkg.app import app as fastapi_app
        paths = routes_of(fastapi_app)
        print(f"探测 {len(paths)} 个 GET 端点 → {base}\n")
        rows = probe(base, paths)

        leak_rows = [r for r in rows if r["leaks"]]
        no_noindex = [r for r in rows
                      if r["status"] == 200 and r["path"] not in ALLOWED_PUBLIC
                      and not r.get("noindex") and not r["path"].startswith("/static")]
        ok_rows = [r for r in rows if r["status"] in (200, 301, 302, 401, 403, 404)]

        print("=== 逐个端点 ===")
        for r in rows:
            flag = ""
            if r["leaks"]:
                flag += "  ★泄露:" + ",".join(r["leaks"])
            if r["status"] == 0:
                flag += "  ★连不上"
            if r["status"] == 200 and r["path"] not in ALLOWED_PUBLIC and not r.get("noindex"):
                flag += "  ★缺 noindex"
            print(f"  {r['status']:>3}  {r['path']:<46} {r['size']:>8}B {r['ms']:>5}ms{flag}")

        print(f"\n=== 汇总 ===")
        print(f"  端点总数        : {len(rows)}")
        print(f"  匿名可达(200)   : {sum(1 for r in rows if r['status'] == 200)}")
        print(f"  需鉴权(401/403) : {sum(1 for r in rows if r['status'] in (401, 403))}")
        print(f"  已关闭(404)     : {sum(1 for r in rows if r['status'] == 404)}")
        print(f"  ★泄露信息的端点 : {len(leak_rows)}")
        print(f"  ★缺 noindex 的  : {len(no_noindex)}")
        for r in leak_rows:
            print(f"      - {r['path']} → {', '.join(r['leaks'])}")
        for r in no_noindex:
            print(f"      - {r['path']}（200 但没有 X-Robots-Tag: noindex）")

        out = PROGRAM_ROOT / "_docs" / "暴露面实测.json"
        out.parent.mkdir(parents=True, exist_ok=True)

        # ── 第二遍：「隧道视角」再打一次 ────────────────────────
        #  模拟经 Cloudflare Tunnel 进来的请求（直连方是本机回环，
        #  转发头带公网 IP）。**这是最容易漏的一类**：
        #  只看 request.client.host 的话，隧道后面 client.host 恒为 127.0.0.1，
        #  「仅本机」的端点会**原样放行**给公网访客。
        LOCAL_ONLY = ["/api/health/detail", "/api/diagnose"]
        tunnel_rows = probe(base, [p for p in LOCAL_ONLY if p in paths],
                            extra_headers={"CF-Connecting-IP": "203.0.113.7",
                                           "X-Forwarded-For": "203.0.113.7"})
        print("\n=== 隧道视角（模拟经代理进入的公网访客）===")
        tunnel_fail = []
        for r in tunnel_rows:
            ok = r["status"] in (401, 403)
            print(f"  {r['status']:>3}  {r['path']:<40} {'★没挡住！本机端点被公网看到了' if not ok else '已挡住'}")
            if not ok:
                tunnel_fail.append(r["path"])
        print(f"  隧道视角下未被挡住的本机端点：{len(tunnel_fail)} {tunnel_fail}")

        out.write_text(json.dumps(
            {"base": base, "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "rows": rows, "tunnel_view": tunnel_rows,
             "tunnel_leaks": tunnel_fail}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已存 → {out}")
        if tunnel_fail:
            print("\n[X] 有本机端点在隧道视角下没挡住 —— 这类漏洞上线即被利用，必须先修")
            return 1
        return 0
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
