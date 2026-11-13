# -*- coding: utf-8 -*-
"""生成良渚文化考古分期字典（BP15 时序页播放轴的唯一真源）。

★ 为什么单独一个生成器
  考古分期的年代与特征是**学界分期 + 本站发掘简报原文**，不是从登记表字段推出来的，
  所以既不能写死在前端，也不能每次请求现算（全文 200 篇）。
  → 离线扫一次全文，把「哪篇文献谈到哪个期段、原文怎么说的」落成一个数据文件，
    运行时只读这个文件；文件改了重跑本脚本即可。

★ 纪律
  · 年代区间有学术分歧 → 每个期段都写出处（本站文献编号），分歧在 canon 字段里说明；
  · 期段归属按**原文表述**识别（良渚文化早期/中期/晚期/末期、崧泽—良渚转型、
    以及「公元前 XXXX～YYYY 年」的绝对年代），不按登记表的「时间分期标签」——
    那是教科书式的新石器时代分期，与良渚考古分期不同源，不能混用；
  · 只输出期段归属与原文摘句，不外传正文内容。

用法：python _tools/make_arch_periods.py
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

# 本脚本可以放在 01-程序/_tools/ 下（发布版也带着），程序目录按位置自动判断
_HERE = Path(__file__).resolve().parent.parent
PROGRAM = _HERE if (_HERE / "lzkg").is_dir() else _HERE / "01-程序"
JOB = PROGRAM.parent
SRC = PROGRAM / "data" / "source"
DB = PROGRAM / "data" / "lzkg.db"
OUT = PROGRAM / "lzkg" / "data" / "dict" / "lzkg_arch_periods.json"

# ── 期段表（轴上等宽？否：按真实年代比例排）────────────────────────────
# y0/y1 = 轴上占位（公元前，年份数，越大越早）；canon = 文献里的常用年代写法
PHASES = [
    {
        "id": "TRANS", "name": "崧泽—良渚转型期", "y0": 3500, "y1": 3300,
        "color": "#8E9BB3",
        "canon": "前3500–前3000",
        "note": "文献多作前3500–前3000年（含崧泽晚期向良渚的过渡），与良渚文化早期起点前3300年有重叠；轴上取不重叠切分。",
        "feature": "崧泽晚期向良渚文化过渡的礼制转型期：出现祭祀性土台，陶器与玉器并重的礼器组合（鼎、豆、壶成为固定组合），小玉璧、小玉镯与玉琮雏形出现——良渚文化礼仪制度的孕育期。",
        "sources": ["LIT-000047"],
    },
    {
        "id": "EARLY", "name": "良渚文化早期", "y0": 3300, "y1": 2850,
        "color": "#6FA8C0",
        "canon": "前3300–前2850",
        "note": "莫角山陶器类型学定为约前3300～前3000年；钟家港中段碳十四把早期晚段定在前3000～前2850年。",
        "feature": "莫角山东坡台地最早堆筑年代为前3000～前2850年（碳十四校正前3011～前2902年，东坡炭化稻谷前2940～前2840年）；古城外围大型水利系统始建约当距今5000年。台地持续扩建、水域面积萎缩，是古城核心区的形成期。",
        "sources": ["LIT-000115", "LIT-000169", "LIT-000170"],
    },
    {
        "id": "MID", "name": "良渚文化中期", "y0": 2850, "y1": 2500,
        "color": "#C7A15A",
        "canon": "前2850–前2500",
        "note": "钟家港中段第三、四阶段（碳十四前2850～前2500年）；汇观山祭坛墓葬年代与反山相近。",
        "feature": "钟家港中段第三、四阶段各类护岸遗迹并存；汇观山祭坛建于良渚文化早期或中期偏早，打破祭坛的四座墓葬年代与反山相近；玉礼器与神人兽面纹体系成型，出现按玉器随葬划分等级的高等级墓地。",
        "sources": ["LIT-000115", "LIT-000113", "LIT-000114", "LIT-000015"],
    },
    {
        "id": "LATE", "name": "良渚文化晚期", "y0": 2500, "y1": 2300,
        "color": "#C07F52",
        "canon": "前2500–前2300",
        "note": "分期研究把良渚文化年代下限推到前2500年；钟家港南段简报则把晚期主体定为前2700～前2300年（个别数据延至前2300～前2000年）——两种划法存在口径差异。",
        "feature": "钟家港南段遗存主体年代为前2700～前2300年，首次确认城内存在手工业作坊区；稻作水田（临平茅山遗址群约5.5 hm²）、外郭城与外围水利体系成型；玉礼器纹饰式微，陶器纹样转为螺旋纹与鸟形组合。",
        "sources": ["LIT-000116", "LIT-000120", "LIT-000017", "LIT-000015"],
    },
    {
        "id": "END", "name": "良渚文化末期", "y0": 2300, "y1": 2000,
        "color": "#9C8C7A",
        "canon": "前2300–前2000",
        "note": "良渚文化之后由钱山漾—肖家屋脊—乱石滩—广富林接续（肖家屋脊前2200～前1850年、乱石滩前2300～前1750年、广富林前2100～前1850年）。",
        "feature": "良渚文化之后，太湖流域进入后良渚阶段（钱山漾—肖家屋脊—乱石滩—广富林）；前2000年前后长三角发生一次大规模海侵与气候异常事件，多处良渚遗址之上覆盖淤土，古城聚落体系随之瓦解。",
        "sources": ["LIT-000198", "LIT-000196", "LIT-000116"],
    },
]

# 期段识别用的原文表述
TERMS = [
    ("TRANS", [r"崧泽[—-－]良渚转型", r"崧泽晚期向良渚", r"崧泽文化向良渚", r"崧泽—良渚"]),
    ("EARLY", [r"良渚文化早中期", r"良渚文化早期", r"良渚文化早(?:期)?偏早", r"良渚文化偏早"]),
    ("MID", [r"良渚文化中晚期", r"良渚文化中期", r"良渚文化中(?:期)?偏早", r"良渚文化中期偏晚"]),
    ("LATE", [r"良渚文化晚期", r"良渚文化偏晚", r"良渚文化晚期偏晚", r"良渚文化晚期后段"]),
    ("END", [r"良渚文化末期", r"后良渚"]),
]
DATE_RE = re.compile(r"公元前\s*([0-9]{3,4})\s*[~～—\-－至到]\s*(?:公元前)?\s*([0-9]{3,4})\s*年")
SENT_SPLIT = re.compile(r"(?<=[。；\n])")

# 期段（y0,y1）→ 绝对年代区间里的期段（重叠最大者）
AXIS = [(p["id"], p["y0"], p["y1"]) for p in PHASES]


def phase_of_span(lo: int, hi: int) -> str | None:
    """绝对年代区间 → 期段。

    ★ 只认**完全落在单个期段内**的区间：跨期的年代写法（如前2700～前2300
      横跨中晚期）说的是一个长时段，指到某一期就是错的 → 直接跳过。
    """
    for pid, y0, y1 in AXIS:
        if hi <= y0 and lo >= y1:
            return pid
    return None


def sentences(text: str):
    for s in SENT_SPLIT.split(text):
        s = s.strip()
        if s:
            yield s


def scan_doc(text: str) -> dict:
    """一篇文献 → {phases:[], evidence:{phase:摘句}, dates:[]}"""
    phases: list[str] = []
    evidence: dict[str, str] = {}
    dates: list[list[int]] = []
    sents = list(sentences(text))
    for pid, pats in TERMS:
        for p in pats:
            rx = re.compile(p)
            hit = None
            for s in sents:
                if rx.search(s):
                    hit = s
                    break
            if hit:
                if pid not in phases:
                    phases.append(pid)
                    evidence[pid] = hit[:160]
                break
    for m in DATE_RE.finditer(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo < hi:
            lo, hi = hi, lo          # 前3500～前3000 → (3000, 3500)
        if hi - lo > 2000 or hi < 1500:
            continue
        dates.append([lo, hi])
        pid = phase_of_span(lo, hi)
        if pid and pid not in phases:
            phases.append(pid)
            for s in sents:
                if m.group(0)[:8] in s:
                    evidence.setdefault(pid, s[:160])
                    break
    return {"phases": phases, "evidence": evidence, "dates": dates[:4]}


def main() -> int:
    db = sqlite3.connect(DB)
    titles = {r[0]: r[1] for r in db.execute("select lit_id, title from literatures")}
    files = sorted(SRC.glob("**/MinerU_markdown_LIT-*.md"))
    docs: dict[str, dict] = {}
    scanned = 0
    for f in files:
        m = re.search(r"LIT-\d{6}", f.name)
        if not m:
            continue
        lit_id = m.group(0)
        scanned += 1
        text = f.read_text(encoding="utf-8", errors="ignore")
        r = scan_doc(text)
        if r["phases"]:
            docs[lit_id] = r

    for p in PHASES:
        p["sources_titles"] = [{"lit_id": s, "title": titles.get(s, s)} for s in p["sources"]]
        p["n"] = sum(1 for d in docs.values() if p["id"] in d["phases"])

    out = {
        "meta": {
            "source": "01-程序/data/source/**/MinerU_markdown_LIT-*.md",
            "docs_scanned": scanned,
            "docs_matched": len(docs),
            "note": "期段按原文表述与绝对年代自动识别，非人工编目；未命中者不入期。",
        },
        "phases": PHASES,
        "docs": docs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[arch] 期段 %d 个｜扫全文 %d 篇｜命中期段 %d 篇" % (len(PHASES), scanned, len(docs)))
    for p in PHASES:
        print("   %-6s %-12s %s  命中 %d 篇" % (p["id"], p["name"], p["canon"], p["n"]))
    print("[arch] 写出 %s (%d bytes)" % (OUT, OUT.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
