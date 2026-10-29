# -*- coding: utf-8 -*-
"""
良渚文化文献目录站 · 公网启停（手动，**不做开机自启**）

用法（双击「启动-公网.cmd」「停止-公网.cmd」即可，也可以命令行跑）：
    python public.py start
    python public.py stop
    python public.py restart
    python public.py status
    python public.py check

三个动作分别管三件事：
    本地服务     python -m lzkg serve          -> http://127.0.0.1:8771
    Cloudflare 隧道                              -> 公网 https://lztp.example.com
    自检         逐条检查并告诉人「缺哪一步、怎么补」

★ 启停规矩（照旧）
  · **不做开机自启**。要挂公网就双击「启动-公网.cmd」，关掉就双击「停止-公网.cmd」。
  · 只停**自己启动过**的进程（看 PID 文件）。别人的隧道不碰。
  · 同一个隧道**不能起两个 cloudflared**：会互相抢连接，表现是「公网时好时坏」。
    所以 start 前先探活，已在跑就直接复用。

★ 这个脚本**不改** Cloudflare 的账号设置（DNS 记录、隧道公网主机名）：
  那两样要么需要面板操作、要么需要带 DNS 编辑权的 token。
  脚本只负责「本机进程 + 自检 + 告诉你缺哪一步」。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PID_WEB = DATA / "public-web.pid"
PID_TUN = DATA / "public-tunnel.pid"
LOG_WEB = DATA / "public-web.log"
LOG_TUN = DATA / "public-tunnel.log"
ERR_WEB = DATA / "public-web.err"
ERR_TUN = DATA / "public-tunnel.err"

PORT = 8771
DOMAIN = "lztp.example.com"
TUNNEL_ID = "00000000-0000-0000-0000-000000000000"
CF_CONFIG = Path.home() / ".cloudflared" / "config.yml"
CF_EXE = Path.home() / "AppData" / "Local" / "Programs" / "cloudflared.exe"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET} {msg}")


def err(msg: str) -> None:
    print(f"{RED}[X]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[!]{RESET} {msg}")


def dim(msg: str) -> None:
    print(f"{DIM}{msg}{RESET}")


def which_python() -> str:
    for exe in ("python", "py"):
        p = subprocess.run([exe, "-c", "import sys;print(sys.executable)"],
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    raise SystemExit("找不到可用的 Python")


def which_cloudflared() -> str | None:
    if CF_EXE.exists():
        return str(CF_EXE)
    p = subprocess.run(["where", "cloudflared"], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().splitlines()[0].strip()
    return None


def port_alive(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str, timeout: float = 6.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (r.read(400).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)[:120]


# ★ 自检请求必须用**浏览器 UA**：Cloudflare 的浏览器完整性检查会把
#   `Python-urllib/…` 这类 UA 直接判成 1010 拦掉 → 自检会误报「公网打不开」，
#   而实际上公网是好的。这类误报比漏报更费时间。
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def http_raw(url: str, timeout: float = 8.0) -> tuple[int, str, dict]:
    """不跟随重定向地请求 → (状态码, Location, 响应头)。"""
    import urllib.request
    import urllib.error

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    # ★ 绕过系统/注册表里的代理设置（ProxyHandler({}) = 空代理）。
    #   本机若装了 Clash 之类代理（注册表 ProxyServer 指向 127.0.0.1:7897），
    #   urllib 会自动走它 → 代理对某些域名返回**自签证书** → 严格校验下报
    #   「certificate verify failed: self-signed certificate」，
    #   看起来像"公网打不开"，其实是本机代理在中间。
    #   自检要反映**站点本身**的链路，所以这里显式不走代理。
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", BROWSER_UA)]
    try:
        req = urllib.request.Request(url)
        with opener.open(req, timeout=timeout) as r:
            return r.status, (r.headers.get("Location") or ""), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Location") or ""), dict(e.headers or {})
    except Exception as e:
        return 0, str(e)[:140], {}


def dns_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, socket.AF_INET)
        return True
    except Exception:
        return False


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    except Exception:
        return False


def _cloudflared_cmdlines() -> list[str]:
    """列出所有 cloudflared 的命令行（用来分辨「是谁的隧道」）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
             "ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=20)
        return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        return []


def tunnel_running() -> bool:
    """**我们这条**隧道（428fdf6b / 本地 config）是否已经在跑。

    ★ 不能只问「有没有 cloudflared 在跑」
      这台机器上还有别的项目的隧道（token 隧道、临时隧道）。
      只看进程存在会误判成「隧道已在跑」→ 我们的隧道就永远起不来。
      所以必须按 **隧道 ID / 配置文件路径**认人。
    """
    for cmd in _cloudflared_cmdlines():
        if TUNNEL_ID in cmd or str(CF_CONFIG) in cmd:
            return True
    return False


def other_tunnels() -> list[str]:
    """别的 cloudflared 进程（**只报告，不碰**）。"""
    out = []
    for cmd in _cloudflared_cmdlines():
        if TUNNEL_ID not in cmd and str(CF_CONFIG) not in cmd:
            out.append(cmd[:120])
    return out


def start_web(py: str) -> None:
    if port_alive(PORT):
        ok(f"本地服务已在跑（127.0.0.1:{PORT}），不重复起")
        return
    DATA.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    with open(LOG_WEB, "wb") as o, open(ERR_WEB, "wb") as e:
        p = subprocess.Popen([py, "-m", "lzkg", "serve"], cwd=str(ROOT), env=env,
                             stdout=o, stderr=e)
    PID_WEB.write_text(str(p.pid), encoding="utf-8")
    for _ in range(40):
        if port_alive(PORT):
            ok(f"本地服务已起（PID {p.pid}）→ http://127.0.0.1:{PORT}/")
            return
        time.sleep(0.5)
    err(f"本地服务起不来，看 {ERR_WEB.name}")


def stop_web() -> None:
    pid = read_pid(PID_WEB)
    if not pid_alive(pid):
        dim("本地服务本来就没在跑（或不是本脚本起的，不碰）")
        PID_WEB.unlink(missing_ok=True)
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    ok(f"已停本地服务 PID={pid}")
    PID_WEB.unlink(missing_ok=True)


def start_tunnel() -> None:
    if tunnel_running():
        ok("隧道已在跑（不重复起：同一隧道起两个会互抢连接）")
        return
    exe = which_cloudflared()
    if not exe:
        err("找不到 cloudflared")
        return
    if not CF_CONFIG.exists():
        err(f"缺隧道配置：{CF_CONFIG}")
        return
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG_TUN, "wb") as o, open(ERR_TUN, "wb") as e:
        p = subprocess.Popen([exe, "tunnel", "--config", str(CF_CONFIG), "run"],
                             cwd=str(CF_CONFIG.parent), stdout=o, stderr=e)
    PID_TUN.write_text(str(p.pid), encoding="utf-8")
    for _ in range(30):
        try:
            txt = ERR_TUN.read_text(encoding="utf-8", errors="replace")
            if "Registered tunnel connection" in txt:
                ok(f"隧道已起（PID {p.pid}）→ https://{DOMAIN}/")
                return
        except Exception:
            pass
        time.sleep(1.0)
    warn(f"隧道起来了但还没看到注册成功，看 {ERR_TUN.name}")


def stop_tunnel() -> None:
    pid = read_pid(PID_TUN)
    if not pid_alive(pid):
        dim("隧道本来就没在跑（或不是本脚本起的，不碰）")
        PID_TUN.unlink(missing_ok=True)
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    ok(f"已停隧道 PID={pid}")
    PID_TUN.unlink(missing_ok=True)


def check() -> int:
    """逐条自检：**说清缺哪一步、怎么补**，不只报一个 FAIL。"""
    print("=== 良渚文献目录站 · 公网自检 ===\n")
    bad = 0

    if port_alive(PORT):
        ok(f"本地服务在跑（127.0.0.1:{PORT}）")
    else:
        bad += 1
        err("本地服务没跑 —— 先双击「启动.cmd」或跑 public.py start")

    if tunnel_running():
        ok("cloudflared 进程在跑")
    else:
        bad += 1
        err("隧道没跑 —— 跑 public.py start（它会一并把隧道拉起来）")

    if dns_resolves(DOMAIN):
        ok(f"DNS 能解析 {DOMAIN}")
    else:
        bad += 1
        err(f"DNS 解析不了 {DOMAIN}")
        print("   → 在 Cloudflare 面板加一条记录：")
        print(f"     类型 CNAME　主机名 lztp　目标 {TUNNEL_ID}.cfargotunnel.com")
        print("     代理状态：已代理（橙云）")
        print("     ★ 隧道跑着但没有这条 DNS 记录，域名就解析不到 —— 这是最常见的一步漏。")

    code, loc, _h = http_raw(f"https://{DOMAIN}/api/health", timeout=20)
    if code == 302 and "cloudflareaccess.com" in loc:
        ok(f"公网被 Cloudflare Access 接管（邮箱验证层生效）→ {loc[:64]}…")
    elif code == 200:
        warn("公网直接返回 200 —— Access 没生效？成员就能绕过邮箱验证直接进来")
    else:
        bad += 1
        err(f"公网打不开（HTTP {code}）{loc[:120]}")
        print("   → 先看隧道日志里的 dial 错误；本地服务没起会 502")
        if "certificate" in loc.lower() or "ssl" in loc.lower():
            print("   → ★ 这是**证书**问题，不是站点问题：多半是本机的代理软件"
                  "（Clash 之类）对本域名有特殊规则，返回了自签证书。")
            print("     浏览器通常还能用（它放行自签证书）；想真正直连，"
                  "在代理规则里给本域名加一条 DIRECT。")

    # 源站侧：带公网 Host 头打本地端口，证明「隧道转发的目标」本身是通的
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/health",
                                     headers={"Host": DOMAIN, "User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read(120).decode("utf-8", "replace")
        ok(f"源站认这个 Host（隧道转发的目标通）→ {body.strip()[:40]}")
    except Exception as e:
        bad += 1
        err(f"源站对公网 Host 无响应：{str(e)[:90]}")

    # 本机诊断端点的门
    try:
        from lzkg.config import get_config
        cfg = get_config()
        if cfg.local_detail_enabled:
            warn("config.json 的 public.local_detail 还是 true —— 挂公网前请置 false")
            print("   → 那两个端点含本机绝对路径（库在哪、源数据在哪）")
        else:
            ok("本机诊断端点已对外关闭（public.local_detail=false）")
    except Exception as e:
        warn(f"读不到配置：{str(e)[:80]}")

    print()
    if bad:
        err(f"还有 {bad} 项没过 —— 照上面每条的提示做，做完再跑一次 check")
        return 1
    ok("公网链路全部就绪")
    return 0


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if action == "start":
        start_web(which_python())
        start_tunnel()
        print()
        print(f"   本机   http://127.0.0.1:{PORT}/")
        print(f"   公网   https://{DOMAIN}/")
        print("   停：双击「停止-公网.cmd」")
        return 0
    if action == "stop":
        stop_web()
        stop_tunnel()
        return 0
    if action == "restart":
        stop_web()
        stop_tunnel()
        time.sleep(1)
        start_web(which_python())
        start_tunnel()
        return 0
    if action == "status":
        print(f"本地服务 : {'在跑' if port_alive(PORT) else '没跑'}")
        print(f"隧道     : {'在跑' if tunnel_running() else '没跑'}（{TUNNEL_ID[:8]}…）")
        print(f"DNS      : {DOMAIN} {'可解析' if dns_resolves(DOMAIN) else '解析不了'}")
        print(f"公网地址 : https://{DOMAIN}/")
        others = other_tunnels()
        if others:
            print(f"另有 {len(others)} 个别的 cloudflared 在跑（**不是本项目的，不碰**）：")
            for o in others:
                print("   · " + o)
        return 0
    if action == "check":
        return check()
    print("用法：public.py start | stop | restart | status | check")
    return 1


if __name__ == "__main__":
    sys.exit(main())
