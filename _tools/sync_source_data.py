# -*- coding: utf-8 -*-
"""把课程第一阶段的源数据**复制**进程序目录，让程序自包含。

★ 为什么要有这个脚本
------------------------------------------------------------------
`04_专题研究`（原始 PDF / MinerU 解析结果 / 文献全文 MD / 图版 / 登记表）
原先在**作业 02** 目录下，站点靠 `config.py` 相对定位找到它。
问题：程序交给别人（组员）时，对方没有那份 02 目录 → 站点跑不起来，
图版（`/media/img/`）与原文通道（PDF / Markdown）全都读不到。

做法：把源数据**复制**到 `01-程序/data/source/`，
`config.py` 优先用这份本地副本，找不到才回退到外部课程布局
（→ 旧作业照旧能用，**向后兼容**）。

★ 三条纪律
  1. **只复制，不移动、不删除源** —— 源是课程第一阶段的产物，
     别的作业也可能引用它；不可逆的操作一律不做。
  2. **脚本化 + 回读校验** —— 手工拷 2 GB 出错没人看得见。
  3. ★ **这些是机构订阅件，永远不进公开仓库**（见 `.gitignore` 与
     `make_github_release.py` 的白名单式收集）。

用法：
    python _tools/sync_source_data.py            # 复制（跳过已存在且同大小）
    python _tools/sync_source_data.py --verify   # 只校验，不复制
    python _tools/sync_source_data.py --force     # 有差异也覆盖复制
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

JOB = Path(__file__).resolve().parents[1]          # 03/
PROGRAM = JOB / "01-程序"
DEST = PROGRAM / "data" / "source"

# 要搬的子目录（整个目录树原样复制）
SUBDIRS = ["01_原始资料", "02_解析结果", "03_文献全文", "04_文献图像", "05_登记表"]

# 抽样校验的文件数（每目录抽几个算 sha256）
SAMPLE = 3


def find_source_root() -> Path:
    """定位课程原始的 04_专题研究（与 lzkg/config.py 的逻辑一致，但独立实现）。"""
    import os
    env = os.environ.get("LZKG_SOURCE_ROOT")
    if env:
        p = Path(env)
        if p.name != "04_专题研究" and (p / "04_专题研究" / "05_登记表").is_dir():
            p = p / "04_专题研究"
        if p.is_dir():
            return p
    assign = JOB.parent
    for c in [assign / "04_专题研究",
              *sorted(assign.glob("*/04_专题研究")),
              *sorted(assign.glob("02*/04_专题研究"))]:
        if (c / "05_登记表").is_dir():
            return c
    up = JOB
    for _ in range(4):
        up = up.parent
        for c in sorted(up.glob("*/04_专题研究")) + sorted(up.glob("*/02*/04_专题研究")):
            if (c / "05_登记表").is_dir():
                return c
    raise SystemExit("[X] 找不到 04_专题研究（可用环境变量 LZKG_SOURCE_ROOT 指定）")


def sha256(p: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tree_stats(root: Path) -> dict:
    files = [p for p in root.rglob("*") if p.is_file()]
    return {"count": len(files), "bytes": sum(p.stat().st_size for p in files),
            "files": files}


def copy_one(src: Path, dst: Path, force: bool) -> tuple[str, int]:
    """复制一个文件；返回 (状态, 字节数)。状态 ∈ copied / skipped / conflict"""
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return "copied", src.stat().st_size
    if dst.stat().st_size == src.stat().st_size and not force:
        return "skipped", src.stat().st_size
    shutil.copy2(src, dst)
    return "copied", src.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只校验，不复制")
    ap.add_argument("--force", action="store_true", help="有差异也覆盖复制")
    a = ap.parse_args()

    src_root = find_source_root()
    print(f"源目录 : {src_root}")
    print(f"目标   : {DEST}")
    if a.verify:
        print("模式   : 仅校验\n")
    else:
        print("模式   : 复制（★ 只复制，不动源）\n")

    report = {"source_root": str(src_root), "dest": str(DEST), "dirs": {}}
    total_copied = 0
    total_bytes = 0

    for d in SUBDIRS:
        s = src_root / d
        t = DEST / d
        if not s.is_dir():
            print(f"[X] 源目录缺 {d} —— 跳过")
            continue
        st = tree_stats(s)
        copied = skipped = 0
        conflicts = []
        for f in st["files"]:
            rel = f.relative_to(s)
            if a.verify:
                df = t / rel
                if not df.exists():
                    conflicts.append({"file": str(rel), "issue": "缺失"})
                elif df.stat().st_size != f.stat().st_size:
                    conflicts.append({"file": str(rel), "issue": "大小不一致",
                                      "src": f.stat().st_size, "dst": df.stat().st_size})
                continue
            state, _ = copy_one(f, t / rel, a.force)
            if state == "copied":
                copied += 1
            else:
                skipped += 1
        # 抽样 hash
        samples = []
        if st["files"]:
            step = max(1, len(st["files"]) // SAMPLE)
            for f in st["files"][::step][:SAMPLE]:
                rel = f.relative_to(s)
                df = t / rel
                if df.exists():
                    ok = sha256(f) == sha256(df)
                    samples.append({"file": str(rel), "same": ok})
                else:
                    samples.append({"file": str(rel), "same": False})
        report["dirs"][d] = {"src_files": st["count"], "src_bytes": st["bytes"],
                             "copied": copied, "skipped": skipped,
                             "conflicts": conflicts, "samples": samples}
        total_bytes += st["bytes"]
        total_copied += copied
        tag = "校验" if a.verify else "复制"
        print(f"  {d:<14} {tag} {copied:>5} / 跳过 {skipped:>5} / "
              f"源 {st['count']:>5} 文件 {st['bytes'] / 1048576:>8.1f} MB"
              + (f"  ★差异 {len(conflicts)}" if conflicts else ""))

    print(f"\n合计：{'校验' if a.verify else '新复制'} {total_copied} 个文件，"
          f"源总量 {total_bytes / 1048576:.1f} MB")

    bad = []
    for d, info in report["dirs"].items():
        bad += [f"{d}/{c['file']}：{c['issue']}" for c in info["conflicts"]]
        bad += [f"{d}/{s['file']}：hash 不一致" for s in info["samples"] if not s["same"]]
    if bad:
        print("\n★ 发现问题：")
        for b in bad[:30]:
            print("   -", b)
        if len(bad) > 30:
            print(f"   …另有 {len(bad) - 30} 条")
    else:
        print("校验结果：全部一致（文件齐、大小对、抽样 hash 相同）")

    (PROGRAM / "data" / "source_sync_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告 → {PROGRAM / 'data' / 'source_sync_report.json'}")

    # ── 把 config.json 的 source.root 指到本地副本 ────────────────
    #   为什么要在脚本里改（而不是让人手改）：
    #     · 站点要**自包含** —— 不依赖作业 02 目录
    #     · 改完留痕在报告里，且随时能用 `--verify` 复核
    #   组员那边：包里的 config.json **只保留 public 段**（打包时剥掉机器相关段），
    #   所以他们会自动发现 `data/source/` —— 不需要这一步。
    if not a.verify:
        cfg_path = PROGRAM / "config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                old = (cfg.get("source") or {}).get("root")
                cfg.setdefault("source", {})["root"] = str(DEST)
                cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
                print(f"config.json 的 source.root：{old}  →  {DEST}")
            except Exception as e:
                print(f"[!] config.json 更新失败（不影响数据）：{e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
