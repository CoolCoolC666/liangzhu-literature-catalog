# -*- coding: utf-8 -*-
"""构建「良渚文献目录站」对接包 zip。

★ 为什么要脚本化
---------------
上一版包是手工打的，结果**漏了新加的模块**（layout.py / registry.py / templates.py /
sheetio.py / schema/registry_columns.json），接手的人一跑就 ImportError。
手工打包的错误**没法在事后看出来**——zip 打得出来，不代表内容对。
所以这里脚本化：白名单式收集文件 + 打包后**回读校验**（逐条列出来、试开 zip、核对必备文件）。

用法：
    python 03/_tools/build_package.py                 # 构建 v2.0
    python 03/_tools/build_package.py --version 2.1   # 指定版本号
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

# 工程根：03/
JOB = Path(__file__).resolve().parents[1]
PROGRAM = JOB / "01-程序"
PKG_DIR = JOB / "03-对接包"
TOP = "良渚文献目录站"

# ── 白名单：程序目录下要进包的东西 ─────────────────────────────
INCLUDE_DIRS = ["lzkg", "tests", "_docs", "_tools"]
INCLUDE_FILES = ["启动.cmd", "停止.cmd", "README.md", "requirements.txt",
                "启动-公网.cmd", "停止-公网.cmd", "public.py"]
# 进包但不进「随包文档」的那几个
INCLUDE_DATA = ["遗址点位表.xlsx", "遗址坐标.json", "controlled_vocab_codes.json",
                "受控词表-校准报告.xlsx", "坐标证据台账.xlsx",
                "遗址坐标_公开检索报告.md"]
# 底图：完整版 22 MB → 精简版 3 MB（区域图层裁剪 + 压精度），包里放**精简版**
#   本机 data/basemap/ 保持完整版（可画任意范围），包内 data/basemap/ 用精简版，
#   程序读路径不变，只是内容不同。
BASEMAP_LITE = PROGRAM / "data" / "basemap_lite"
BASEMAP_FULL = PROGRAM / "data" / "basemap"
# config.json 要进包，但**必须剥掉机器相关的段**
#   source.root / db.path 是**这台机器**的绝对路径，进了包别人就找不到自己的数据源；
#   app.port / auth.bootstrap 也跟着本机走。
#   只保留 `public`（公网发布设置：Access 配置、local_detail 总闸）——那部分与机器无关。
CONFIG_KEEP = ("public",)

# 永远排除
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".err", ".tmp", ".bak"}
EXCLUDE_PREFIX = ("_server.", "_tmp", "scratch")

# 随包文档：从 03/ 拷进去
DOC_SOURCES = {
    "对接说明.md": PKG_DIR / "对接说明.md",
    "变更日志.md": PKG_DIR / "变更日志.md",
    "工程约定.md": JOB / "AGENTS.md",
}

# 必备文件：打包后必须逐个核对存在（缺一个就说明打包漏了）
REQUIRED = [
    "README.md", "requirements.txt", "启动.cmd", "停止.cmd",
    "lzkg/__init__.py", "lzkg/__main__.py", "lzkg/app.py", "lzkg/config.py",
    "lzkg/db.py", "lzkg/graph.py", "lzkg/layout.py", "lzkg/migrate.py",
    "lzkg/nbkg.py", "lzkg/registry.py", "lzkg/templates.py", "lzkg/sheetio.py",
    "lzkg/basemap.py", "lzkg/vocab.py",
    "lzkg/webdav.py", "lzkg/browserec.py",
    "lzkg/graphview.py",
    "lzkg/rawtext.py", "lzkg/xmlexchange.py", "lzkg/api_raw.py",
    # 检索增强 E1（v3.9）：分词层 + 领域词典 + 管理员可视化编辑器
    "lzkg/segment.py", "lzkg/api_segment.py",
    "lzkg/data/dict/lzkg_domain.json",
    "lzkg/schema/registry_columns.json",
    "lzkg/web/static/index.html",
    "tests/test_all.py",
    "tests/test_segment.py",
    "tests/test_graphview.py",
    "tests/test_rawtext.py",
    "tests/test_favsearch.py",
    "tests/test_favsearch.js",
    "tests/test_webdav_live.py",
    "tests/test_wdsafename.js",
    "tests/test_browsemerge.js",
    "lzkg/webdav.py",
    "lzkg/web/static/vendor/leaflet/leaflet.js",
    "_docs/数据契约.md",
    "data/controlled_vocab_codes.json",
    "data/遗址坐标.json",
    "data/basemap/manifest.json",
]


def _skip(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    if any(path.name.startswith(p) for p in EXCLUDE_PREFIX):
        return True
    return False


def collect() -> list[tuple[Path, str]]:
    """收集 (源文件, 包内相对路径)。"""
    items: list[tuple[Path, str]] = []
    for d in INCLUDE_DIRS:
        base = PROGRAM / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or _skip(p):
                continue
            rel = p.relative_to(PROGRAM).as_posix()
            items.append((p, rel))
    for f in INCLUDE_FILES:
        p = PROGRAM / f
        if p.exists():
            items.append((p, f))
    for f in INCLUDE_DATA:
        p = PROGRAM / "data" / f
        if p.exists():
            items.append((p, f"data/{f}"))
    # 底图：优先用精简版，没有就退回完整版
    bsrc = BASEMAP_LITE if BASEMAP_LITE.exists() else BASEMAP_FULL
    if bsrc.exists():
        for p in sorted(bsrc.glob("*")):
            if p.is_file() and not _skip(p):
                items.append((p, f"data/basemap/{p.name}"))
    # 库文件单独处理（很大，放最后）
    db = PROGRAM / "data" / "lzkg.db"
    if db.exists():
        items.append((db, "data/lzkg.db"))
    return items


def sanitized_config() -> str | None:
    """生成**剥掉机器相关段**的 config.json 内容（只留 `public`）。"""
    src = PROGRAM / "config.json"
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return None
    keep = {k: data[k] for k in CONFIG_KEEP if k in data}
    if not keep:
        return None
    return json.dumps(keep, ensure_ascii=False, indent=2)


def build(version: str, out: Path) -> dict:
    items = collect()
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, rel in items:
            z.write(src, f"{TOP}/{rel}")
        cfg = sanitized_config()
        if cfg:
            z.writestr(f"{TOP}/config.json", cfg.encode("utf-8"))
        for name, src in DOC_SOURCES.items():
            if src.exists():
                z.write(src, f"{TOP}/{name}")

    # ── 回读校验 ──────────────────────────────────────────────
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        bad = z.testzip()
        missing = [r for r in REQUIRED if f"{TOP}/{r}" not in names]
        size = out.stat().st_size
    if bad:
        raise SystemExit(f"[X] 包内文件损坏：{bad}")
    if missing:
        raise SystemExit(f"[X] 包里缺这些必备文件：{missing}")
    # ★ 硬红线：源数据副本（机构订阅原件，约 2.1 GB）**不进对接包**。
    #   包里已有 lzkg.db（含可检索的全文与解析 JSON），体量可控；
    #   原始 PDF 与图版文件本体走**单独的数据包**（build_data_package.py）。
    leaked = [n for n in names if n.startswith(f"{TOP}/data/source/")]
    if leaked:
        raise SystemExit(
            f"[X] 拒绝出包：包内出现了 {len(leaked)} 个 data/source/ 文件 —— "
            "源数据是机构订阅件，对接包只带程序与库，原始文件单独分发。")

    h = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    return {
        "zip": str(out), "entries": len(names), "bytes": size,
        "mb": round(size / 1048576, 1), "sha256_16": h,
        "required_ok": len(REQUIRED),
        "docs": [n for n, s in DOC_SOURCES.items() if s.exists()],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="2.0")
    a = ap.parse_args()
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    out = PKG_DIR / f"良渚文献目录站_对接包_v{a.version}.zip"
    rep = build(a.version, out)
    for k, v in rep.items():
        print(f"  {k}: {v}")
    print(f"已打包 → {out}")
