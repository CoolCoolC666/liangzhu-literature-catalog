# -*- coding: utf-8 -*-
"""构建「良渚文化原始数据包」zip（**与程序包分开分发**）。

★ 为什么单独做一个包
------------------------------------------------------------------
v3.8 起源数据（原始 PDF / MinerU 解析结果 / 文献全文 MD / 图版 / 登记表，约 2.1 GB）
被复制进 `01-程序/data/source/`，程序不再依赖作业 02 目录。

但这批文件**全是机构订阅件**（CNKI 等下载原件），
→ **绝对不能**进公开仓库（GitHub），也**塞不进**对接包（会让包从 90 MB 变成 2.1 GB，
微信传不动、网盘也不友好）。

所以拆成两个包：

| 包 | 内容 | 体积 | 给谁 |
|---|---|---|---|
| 对接包 `良渚文献目录站_对接包_vX.Y.zip` | 程序 + 库（lzkg.db，可检索全文已在库内） | ~90 MB | 组员（能跑起来、能检索） |
| **数据包** `良渚文化原始数据包_vX.Y.zip` | `data/source/**`（PDF / 解析结果 / MD / 图版 / 登记表） | ~2.0 GB | 需要**原始 PDF 与图版文件本体**的人 |

用法：
    python 03/_tools/build_data_package.py                # 构建 v3.8
    python 03/_tools/build_data_package.py --version 1.0   # 指定版本号
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

JOB = Path(__file__).resolve().parents[1]
PROGRAM = JOB / "01-程序"
SRC = PROGRAM / "data" / "source"
PKG_DIR = JOB / "03-对接包"
TOP = "良渚文化原始数据"

# 包内必备：**文件**（精确条目）与**目录**（该前缀下至少有一个文件）
# ★ zip 里**不含空目录条目**，目录必须按「前缀下有文件」来校验，
#   否则会把正常打包误判成「缺目录」。
REQUIRED_FILES = [
    "05_登记表/00-最终版/文献基本信息登记表_修复.xlsx",
    "05_登记表/00-最终版/文献图像信息登记表_修复.xlsx",
]
REQUIRED_DIRS = [
    "01_原始资料",
    "02_解析结果",
    "03_文献全文",
    "04_文献图像",
    "05_登记表/00-最终版",
]

# 这批文件不能进公开仓库 —— 打包时顺手确认一下目的地
FORBIDDEN_IN_REPO = "程序/data/source/"


def collect() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    if not SRC.is_dir():
        raise SystemExit(f"[X] 没有源数据副本：{SRC}\n"
                         "    先跑：python 03/_tools/sync_source_data.py")
    for p in sorted(SRC.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(SRC).as_posix()
        items.append((p, rel))
    return items


def build(version: str, out: Path) -> dict:
    items = collect()
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for src, rel in items:
            z.write(src, f"{TOP}/{rel}")

    # ── 回读校验（打包脚本化的意义就在这）────────────────────────
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        bad = z.testzip()
        missing = [r for r in REQUIRED_FILES if f"{TOP}/{r}" not in names]
        # 目录：前缀下至少要有一个文件（zip 不存空目录条目）
        for d in REQUIRED_DIRS:
            pre = f"{TOP}/{d}/"
            if not any(n.startswith(pre) for n in names):
                missing.append(d + "/（目录为空或缺失）")
        size = out.stat().st_size
    if bad:
        raise SystemExit(f"[X] 包内文件损坏：{bad}")
    if missing:
        raise SystemExit(f"[X] 包里缺这些必备项：{missing}")

    h = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    return {"zip": str(out), "entries": len(names), "bytes": size,
            "mb": round(size / 1048576, 1), "sha256_16": h,
            "required_ok": len(REQUIRED_FILES) + len(REQUIRED_DIRS)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="3.8")
    a = ap.parse_args()
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    out = PKG_DIR / f"良渚文化原始数据包_v{a.version}.zip"
    rep = build(a.version, out)
    for k, v in rep.items():
        print(f"  {k}: {v}")
    print(f"已打包 → {out}")
    print("\n★ 提醒：这是**机构订阅件**，只在组内/校内传，别上传公开网盘或 GitHub。")
    print(f"★ 包内顶层目录：{TOP}/（解压后把里面的五个子目录放到 01-程序/data/source/）")
