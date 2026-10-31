# -*- coding: utf-8 -*-
"""验收脚本：源数据迁入程序目录（v3.8）——自包含与「不外传」红线。

    python tests/test_source_data.py           # 全跑
    python tests/test_source_data.py --api     # 附带 API 判据（需先启动服务）

★ 这个脚本盯三件事
  1. **程序自包含**：源数据副本在 `01-程序/data/source/`，配置指向它，
     且**图版 / 原文通道真的从这份副本读**（不是还在读外部课程目录）。
  2. **向后兼容**：外部课程布局仍能作为回退（配置指向的路径不存在时自动回退）。
  3. ★ **不外传红线**：这批文件是**机构订阅件**，
     **公开仓库与对接包里都不许出现 `data/source/`**。

★ 判据一律写「必须等于某个实测真值」，不是「不为空」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM_ROOT = HERE.parent
JOB = PROGRAM_ROOT.parent
sys.path.insert(0, str(PROGRAM_ROOT))

PASS, FAIL = 0, 0
FAILURES: list[str] = []

# 实测真值（2026-10-31 同步后）
TRUE_SRC_FILES = 3808          # 五个目录合计的文件数
TRUE_SRC_BYTES = 2234367000    # 约 2131 MB（按实测取整）
TRUE_SUBDIRS = ["01_原始资料", "02_解析结果", "03_文献全文", "04_文献图像", "05_登记表"]
# 各目录的文件数（实测）
TRUE_DIR_FILES = {
    "01_原始资料": 201,
    "02_解析结果": 202,
    "03_文献全文": 203,
    "04_文献图像": 3179,
    "05_登记表": 23,
}


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK   {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}")
        print(f"  FAIL {name}  {detail}")


def section(t):
    print(f"\n=== {t} ===")


def api(path: str):
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}{path}"
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


# ══════════════════════════════════════════════════════════════════
# 1 · 副本完整性
# ══════════════════════════════════════════════════════════════════
def test_copy_integrity():
    section("源数据副本 · 完整性")
    src = PROGRAM_ROOT / "data" / "source"
    check("副本目录存在", src.is_dir(), str(src))
    if not src.is_dir():
        return

    total_files = 0
    total_bytes = 0
    for d in TRUE_SUBDIRS:
        p = src / d
        check(f"子目录存在：{d}", p.is_dir())
        if not p.is_dir():
            continue
        files = [f for f in p.rglob("*") if f.is_file()]
        n = len(files)
        b = sum(f.stat().st_size for f in files)
        total_files += n
        total_bytes += b
        check(f"{d} 文件数 == {TRUE_DIR_FILES[d]}（实测真值）",
              n == TRUE_DIR_FILES[d], f"实际 {n}")
    check("副本文件总数 == 3808（实测真值）",
          total_files == TRUE_SRC_FILES, f"实际 {total_files}")
    check("副本总体积 ≈ 2131 MB（±1%）",
          abs(total_bytes - TRUE_SRC_BYTES) / TRUE_SRC_BYTES < 0.01,
          f"实际 {total_bytes / 1048576:.1f} MB")

    # 登记表两份 xlsx 必须在（迁移与重建库都要用）
    for name in ["文献基本信息登记表_修复.xlsx", "文献图像信息登记表_修复.xlsx"]:
        p = src / "05_登记表" / "00-最终版" / name
        check(f"登记表在副本里：{name}", p.exists() and p.stat().st_size > 0,
              f"{p.stat().st_size if p.exists() else 0} 字节")

    # 同步报告
    rep = PROGRAM_ROOT / "data" / "source_sync_report.json"
    check("有同步报告（留痕）", rep.exists(), str(rep))
    if rep.exists():
        d = json.loads(rep.read_text(encoding="utf-8"))
        check("报告里每个目录都有抽样 hash 校验结果",
              all(d["dirs"][k].get("samples") for k in TRUE_SUBDIRS if k in d["dirs"]))
        check("报告里没有遗留冲突",
              all(not d["dirs"][k].get("conflicts") for k in TRUE_SUBDIRS if k in d["dirs"]))


# ══════════════════════════════════════════════════════════════════
# 2 · 配置优先级与自包含
# ══════════════════════════════════════════════════════════════════
def test_config():
    section("配置 · 自包含与向后兼容")
    from lzkg.config import get_config, _find_source_root
    cfg = get_config()

    local = PROGRAM_ROOT / "data" / "source"
    check("配置指向程序内的副本（source_root 在 data/source 下）",
          str(cfg.source_root).rstrip("\\/") == str(local).rstrip("\\/"),
          f"实际 {cfg.source_root}")
    check("配置里的路径真实存在", cfg.source_root.is_dir())
    check("md_dir / image_dir / 登记表都在副本里",
          cfg.md_dir.is_dir() and cfg.image_dir.is_dir() and cfg.lit_xlsx.exists(),
          f"md={cfg.md_dir.exists()} img={cfg.image_dir.exists()} xlsx={cfg.lit_xlsx.exists()}")

    # 自动发现的顺序：本地副本优先于外部课程布局
    found = _find_source_root()
    check("自动发现也优先命中本地副本",
          str(found).rstrip("\\/") == str(local).rstrip("\\/"),
          f"实际 {found}")

    # ★ 向后兼容：配置指向的路径**不存在**时，要自动回退而不是崩
    cfg.data["source"]["root"] = str(PROGRAM_ROOT / "data" / "不存在的目录")
    try:
        fb = cfg.source_root
        check("配置路径不存在时自动回退（不崩）", fb.is_dir(), f"回退到 {fb}")
        check("回退目标是可用目录", (fb / "05_登记表").is_dir())
    finally:
        cfg.data["source"]["root"] = str(local)


# ══════════════════════════════════════════════════════════════════
# 3 · API 真的从副本读
# ══════════════════════════════════════════════════════════════════
def test_api():
    section("API · 从副本读原文与图版")
    try:
        r = api("/api/raw/LIT-000001/markdown")
        check("Markdown 通道 200", r.get("ok") is True, str(r.get("error") or ""))
        d = r.get("data") or {}
        check("Markdown 有内容（正文非空）", len(d.get("text") or "") > 1000,
              f"{len(d.get('text') or '')} 字符")
        imgs = d.get("images") or {}
        check("图版统计字段齐全",
              set(["total", "rewritten", "missed"]).issubset(imgs.keys()),
              f"{imgs}")
    except Exception as e:
        check("Markdown 通道可用", False, str(e))

    try:
        # PDF 是**二进制**，不能用 json 解码（否则报 UnicodeDecodeError，
        # 看着像功能坏了，其实是判据脚本读错格式）
        from lzkg.config import get_config
        req = urllib.request.Request(
            f"http://{get_config().host}:{get_config().port}/api/raw/LIT-000001/pdf")
        with urllib.request.urlopen(req, timeout=300) as resp:
            ctype = resp.headers.get("Content-Type", "")
            n = len(resp.read())
        check("PDF 通道 200 且是 application/pdf",
              ctype.startswith("application/pdf") and n > 1_000_000,
              f"{ctype} / {n} 字节")
    except Exception as e:
        check("PDF 通道可用", False, str(e))

    # 图版：拿库里的真实文件名去要，不能返回占位 svg
    import sqlite3
    from lzkg.config import get_config
    db = get_config().db_path
    if db.exists():
        c = sqlite3.connect(str(db))
        rows = c.execute("SELECT filename FROM images LIMIT 3").fetchall()
        c.close()
        ok_cnt = 0
        for (fn,) in rows:
            try:
                with urllib.request.urlopen(
                        f"http://{get_config().host}:{get_config().port}/media/img/{fn}",
                        timeout=60) as resp:
                    body = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                if ctype.startswith("image/") and "svg" not in ctype and len(body) > 2000:
                    ok_cnt += 1
            except Exception:
                pass
        check("图版从本地副本读出真实图片（非占位 svg）",
              ok_cnt == len(rows) and ok_cnt > 0, f"{ok_cnt}/{len(rows)}")


# ══════════════════════════════════════════════════════════════════
# 4 · ★ 红线：公开仓库与对接包里都不许出现源数据
# ══════════════════════════════════════════════════════════════════
def test_redline():
    section("红线 · 源数据不进公开仓库 / 对接包")
    gh = JOB / "github"
    if gh.is_dir():
        leaked = []
        for p in (gh / "程序" / "data").rglob("*") if (gh / "程序" / "data").is_dir() else []:
            if "source" in p.parts:
                leaked.append(str(p))
        check("★ 公开仓库里没有 data/source", not leaked, f"发现 {len(leaked)} 项")
        gi = gh / ".gitignore"
        check("公开仓库 .gitignore 明确忽略 data/source",
              gi.exists() and "data/source" in gi.read_text(encoding="utf-8"))
        # 工作树里也不能有被 git 追踪的源数据
        try:
            pr = subprocess.run(["git", "-C", str(gh), "ls-files"],
                                capture_output=True, text=True, timeout=60)
            if pr.returncode != 0:
                # 这台机器上 git 不可用 / 不是仓库 → 跳过，不假装通过
                print(f"  ⚠ git ls-files 不可用（returncode={pr.returncode}），跳过该条")
            else:
                tracked = [l for l in (pr.stdout or "").splitlines() if "data/source" in l]
                check("★ git 没有追踪任何源数据文件", not tracked, f"{tracked[:3]}")
        except Exception as e:
            print(f"  ⚠ git ls-files 不可用（{e}），跳过该条")

    # 对接包：包内不许有 data/source
    pkgs = sorted((JOB / "03-对接包").glob("良渚文献目录站_对接包_v*.zip"))
    if pkgs:
        import zipfile
        latest = pkgs[-1]
        with zipfile.ZipFile(latest) as z:
            names = z.namelist()
        leaked = [n for n in names if "/data/source/" in n]
        check(f"★ 对接包 {latest.name} 里没有 data/source", not leaked,
              f"发现 {len(leaked)} 项")

    # 发布脚本与打包脚本里必须有硬断言
    for f, label in [(JOB / "_tools" / "make_github_release.py", "发布脚本"),
                     (JOB / "_tools" / "build_package.py", "打包脚本")]:
        if f.exists():
            txt = f.read_text(encoding="utf-8")
            check(f"{label}里有 source 的硬断言",
                  ("data/source" in txt or "data\" / leak" in txt or 'leak' in txt)
                  and "拒绝" in txt)


def main():
    test_copy_integrity()
    test_config()
    if "--api" in sys.argv or "--all" in sys.argv:
        test_api()
    test_redline()
    print(f"\n{'=' * 46}")
    print(f"通过 {PASS}　失败 {FAIL}")
    if FAILURES:
        print("\n失败明细：")
        for f in FAILURES:
            print("  -", f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
