# -*- coding: utf-8 -*-
"""生成受控词表的**唯一码位表** `data/controlled_vocab_codes.json`。

★ 为什么要有这一步
  库里那 5 个「一码多义」码位（S-0801/S-0802/S-0803/S-0804/R-0199）底下
  藏着 54 个不同名称 —— 那些码是**族系代号**，不是标签代号。
  受控词表要求「一词一码、一码一词」，所以要把这 54 个词各自拆成唯一码。

★ 分配规则（写死，别改成自动猜测）
  · 原先**已唯一且可靠**的码位 → 原样保留（R-0000 / R-0101 / R-0102 / R-0399 …）
  · 原先一码多义的码位 → 该码位**不再使用**，其下每个名称另分配唯一码
  · 新码分段：region 用 R-02xx，topic 用 S-09xx
    （刻意避开 S-0801~0804 / R-0199 这几个族系代号段，
      一眼能看出「这个码是校准后新分配的」）
  · 分配顺序按名称 Unicode 排序 → **可复现**，重跑结果一致

★ 产物
  data/controlled_vocab_codes.json    机读：{dim: {name: code}}
  data/受控词表-码位对照.txt           人看的「旧码 → 新码」对照表

用法：python _tools/make_code_table.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "lzkg.db"
OUT = ROOT / "data" / "controlled_vocab_codes.json"
OUT_TXT = ROOT / "data" / "受控词表-码位对照.txt"

sys.path.insert(0, str(ROOT))
from lzkg import vocab  # noqa: E402

# 新码分段
SEGMENT = {"region": ("R-02", 1), "topic": ("S-09", 1)}


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "select dim, code, name, lit_count, img_count, code_reliable from tags"))

    # ── 1. 归并后的最终受控词集合 ────────────────────────────────────
    final: dict[str, set[str]] = defaultdict(set)
    old_code: dict[tuple[str, str], tuple[str, str]] = {}
    for r in rows:
        for name in vocab.canonical(r["dim"], r["name"]):
            final[r["dim"]].add(name)
            old_code.setdefault((r["dim"], name), (r["code"] or "", r["name"]))

    # ── 2. 找出一码多义的码位（要拆的） ───────────────────────────────
    by_code: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        if r["code"]:
            by_code[(r["dim"], r["code"])].add(r["name"])
    multi = {k for k, v in by_code.items() if len(v) > 1}

    # ── 3. 分配唯一码 ────────────────────────────────────────────────
    out: dict[str, dict[str, str]] = defaultdict(dict)
    lines = ["受控词表 · 码位对照（旧码 → 唯一码）", "",
             "★ 一码多义的族系代号不再使用；其下每个词另分配唯一码。", ""]

    for dim in sorted(final):
        names = sorted(final[dim])
        # 已唯一可靠的码原样保留；原码为空的维度（本就没码）保持为空
        keep: dict[str, str] = {}
        for nm in names:
            oc, onm = old_code.get((dim, nm), ("", nm))
            if oc and (dim, oc) not in multi:
                keep[nm] = oc
        # 只给「原属一码多义码位」的词另分配唯一码
        need = [nm for nm in names
                if nm not in keep and (dim, old_code.get((dim, nm), ("", ""))[0]) in multi]
        seg, n = SEGMENT.get(dim, ("X-90", 1))
        for nm in need:
            keep[nm] = f"{seg}{n:02d}"
            n += 1
        out[dim] = keep

        lines.append(f"=== {dim}（{len(names)} 个受控词） ===")
        for nm in names:
            oc, onm = old_code.get((dim, nm), ("", nm))
            flag = ""
            if oc and (dim, oc) in multi:
                flag = "  ← 原属一码多义码位"
            elif oc and oc != onm:
                flag = f"  ← 原名「{onm}」（已归并）"
            lines.append(f"  {keep.get(nm, '—'):<8} {nm}{flag}")
        lines.append("")

    OUT.write_text(json.dumps(dict(out), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")

    total = sum(len(v) for v in out.values())
    print(f"受控词 {total} 条 → {OUT.name}")
    for dim, v in sorted(out.items()):
        print(f"  {dim}: {len(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
