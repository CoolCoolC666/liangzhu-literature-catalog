# -*- coding: utf-8 -*-
"""启动前的体检：依赖 + 端口。

★ 为什么要单独一个模块
  「双击启动.cmd 一闪就没」有两种根因，症状完全一样，必须在起服务**之前**查清楚：
    ① 依赖没装/没声明 → ImportError → 进程秒退 → 窗口秒关；
    ② 端口被占 → uvicorn `address already in use` → 进程秒退 → 窗口秒关。
  把这两件事放在 Python 里做（而不是堆在 .cmd 里），好处有三个：
    - 判定逻辑可写判据（.cmd 里的逻辑写不了判据）；
    - 端口能分清「**占着的是不是本站**」（别人占 = 端口被劫持）；
    - 依赖缺失可以**自动装**，并对国内源做回退。

★ 依赖清单的单一事实来源 = `requirements.txt`
  启动脚本的探测清单、这里的检查、判据里的对账，三处必须一致 → 所以都从同一个文件读。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

PROGRAM_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = PROGRAM_ROOT / "requirements.txt"

# pip 包名 → import 模块名（少数两个名字不一样）
PIP_TO_MODULE = {"PyJWT": "jwt", "Pillow": "PIL"}
# 可选依赖：缺了只是少一个功能，不该拦启动（jieba 缺 → 检索退回整句精确）
OPTIONAL = {"jieba"}
# 装不上时的镜像回退（默认 PyPI 在国内经常连不上）
MIRRORS = ["https://pypi.tuna.tsinghua.edu.cn/simple",
           "https://mirrors.aliyun.com/pypi/simple/"]

PORT_FREE = "free"      # 端口没人用 → 可以起
PORT_OURS = "ours"      # 端口上是**本站** → 已经跑着了，别再起一个
PORT_OTHER = "other"    # 端口被**别的程序**占 → 端口被劫持


def requirement_names() -> list[str]:
    """requirements.txt 里声明的包名（去注释、去版本约束）。"""
    out: list[str] = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = line.split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("~")[0]
        name = name.split("[")[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def missing_deps() -> list[str]:
    """**装不上的**硬依赖（pip 名）。可选依赖不在其中。"""
    missing = []
    for pip_name in requirement_names():
        if pip_name in OPTIONAL:
            continue
        module = PIP_TO_MODULE.get(pip_name, pip_name.replace("-", "_"))
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(pip_name)
        except (ImportError, ValueError):
            missing.append(pip_name)
    return missing


def in_venv() -> bool:
    """当前解释器是不是在 venv 里（venv 里 --user 会直接报错，不能乱加）。"""
    return getattr(sys, "base_prefix", sys.prefix) != sys.prefix


def install_deps(verbose: bool = True) -> bool:
    """缺依赖就装。返回是否装成功。

    ★ 装到**当前解释器**（sys.executable）——即启动脚本选中的那个 Python，
      不是项目内独立 venv：组员机器上第一个被找到的 Python 3.10+ 就是它。
    ★ 回退顺序（组员机器常见坑 → 逐个兜）：
      ① 默认源；② 默认源 + --user（非管理员 + 系统级 Python 时只有这条能装）；
      ③ 清华镜像；④ 镜像 + --user。
      venv 里不加 --user（会报 Can not perform a '--user' install）。
    """
    def _pip(args: list[str]) -> bool:
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)] + args
        if verbose:
            print("[lzkg] 正在安装缺失依赖：" + " ".join(cmd[3:]))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
        except Exception as e:
            print(f"[lzkg] pip 调用失败：{e}")
            return False
        if r.returncode == 0:
            return True
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        if verbose and tail:
            print("[lzkg] " + tail[-1])
        return False

    plans: list[list[str]] = [[]]
    if not in_venv():
        plans.append(["--user"])
    for mirror in MIRRORS:
        plans.append(["-i", mirror])
        if not in_venv():
            plans.append(["-i", mirror, "--user"])
    for args in plans:
        if _pip(args):
            return True
        if args:
            print(f"[lzkg] 换一种装法再试：pip install {' '.join(args)}")
    return False


def port_state(host: str, port: int, timeout: float = 1.0) -> str:
    """端口是空的、还是**本站**、还是**别人**？

    ★ 为什么要分三态：端口被占 ≠ 站点已在运行。
      别人（别的服务/代理）占着 8771 时，双击启动会秒退，用户还会以为
      「我的站被劫持了」。区分开就能说人话。
    """
    s = socket.socket()
    s.settimeout(timeout)
    try:
        if s.connect_ex((host, port)) != 0:
            return PORT_FREE
    except OSError:
        return PORT_FREE
    finally:
        s.close()
    # 端口通 → 问它一句「你是不是本站」
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/overview", timeout=2) as resp:
            body = resp.read(4000).decode("utf-8", "replace")
        if "lit_count" in body:
            return PORT_OURS
    except Exception:
        pass
    return PORT_OTHER


def site_is_ours(host: str, port: int) -> bool:
    """占着端口的是不是本站（= 是不是我们自己起的那个进程）。"""
    return port_state(host, port) == PORT_OURS


def open_browser(url: str) -> None:
    """开浏览器（失败无所谓，不影响服务）。"""
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def preflight(host: str, port: int, install: bool = True) -> dict:
    """启动前体检。返回 {'deps_ok':bool, 'missing':[...], 'port':'free|ours|other'}"""
    missing = missing_deps()
    if missing and install:
        print("[lzkg] 缺少依赖：" + " ".join(missing))
        if not install_deps():
            missing = missing_deps()   # 装完再查一次，以实测为准
    state = port_state(host, port)
    return {"deps_ok": not missing, "missing": missing, "port": state}
