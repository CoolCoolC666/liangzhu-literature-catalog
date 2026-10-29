# -*- coding: utf-8 -*-
"""坐标精扫 v2：只认**真经纬度**，把噪声（图片 URL 哈希、参考文献页码、℃、秒、度角）滤掉。

★ 为什么要 v2
  v1 在 203 篇文献里捞到 67 处「候选」，人工核对后发现**九成是噪声**：
    · 图片 CDN URL 里的哈希串（`...fe11839b02...` → 被读成 "E118"）
    · 参考文献页码（`1205e1212`）
    · 培养温度（`28 °C`）、计时（`30 s`）、角度（`116°` 方位角）
  真正有效的坐标极少，但**极珍贵**——它们是"良渚遗址群的空间范围"
  与"坝体采样剖面点"，是可以写进点位表坐标依据的硬证据。

★ 判据收紧成四条（同时满足才算）
  1. 必须出现**度**符号 ° 或中文「度」，或方向词 N/S/E/W 紧跟数字
  2. 必须在**中文正文**语境里：同段落 200 字内不出现 http/cdn/<img/等图片标记
  3. 数值必须落在良渚合理的经纬框内（lat 28~32.5 / lon 118~122.5）
  4. **必须带半球方向**（N/S/E/W 或 北纬/东经），否则无法判正负

★ 产物
  data/coord_scan_v2.json  —— 命中项 + 上下文 + 归属遗址（就近匹配）
  不自动写表：回填要人确认。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT.parent
OUT = ROOT / "data" / "coord_scan_v2.json"

# ── 真坐标模式：必须带方向 ───────────────────────────────────────────
NUM = r"\d{1,3}(?:\.\d+)?"
MIN = r"\d{1,2}"
SEC = r"\d{1,2}(?:\.\d+)?"

PATS = [
    # 30°23′45″N / 30°23′N / 30°23"45"N
    ("dms_deg", re.compile(
        rf"(?P<d>{NUM})°\s*(?:(?P<m>{MIN})\s*[′'’]\s*)?"
        rf"(?:(?P<s>{SEC})\s*[\"″])?\s*"
        rf"(?P<h>[NSEWnsew])(?![A-Za-z])")),
    # 119°59'12"E
    ("dms_deg", re.compile(
        rf"(?P<d>{NUM})°\s*(?P<m>{MIN})\s*[\"″]\s*"
        rf"(?P<s>{SEC})\s*[\"″]\s*(?P<h>[NSEWnsew])(?![A-Za-z])")),
    # 30.395°N / 119.986°E
    ("dec_deg", re.compile(
        rf"(?P<d>{NUM})\s*°\s*(?P<h>[NSEWnsew])(?![A-Za-z])")),
    # 北纬30度23分45秒 / 东经119度59分12秒
    ("cn_deg", re.compile(
        rf"(?P<dir>[北南东西][纬经])\s*(?P<d>{NUM})\s*[°度]\s*"
        rf"(?:(?P<m>{MIN})\s*分(?:\s*(?P<s>{SEC})\s*秒)?)?")),
]

IMG_CTX = re.compile(r"https?://|\.jpg|\.png|cdn-mineru|<img|\]\(http", re.I)
CN_WORD = re.compile(r"[一-鿿]")
LAT_OK = (28.0, 32.5)
LON_OK = (118.0, 122.5)


def to_dec(d, m, s):
    try:
        v = float(d)
        if m:
            v += float(m) / 60.0
        if s:
            v += float(s) / 3600.0
        return v
    except Exception:
        return None


def find_md_dirs():
    out = []
    for base in (WORK.parent, WORK.parent.parent):
        if not base.exists():
            continue
        for p in base.rglob("03_文献全文"):
            if p.is_dir():
                out.append(p)
        if out:
            break
    return sorted(set(out))


def load_sites():
    import openpyxl
    p = ROOT / "data" / "遗址点位表.xlsx"
    wb = openpyxl.load_workbook(p, data_only=True)
    ws = wb["点位表"]
    names, seen = [], set()
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            s = str(cell or "").strip()
            if not s or s == "遗址名称" or "：" in s or "（" in s:
                break
            if re.fullmatch(r"[一-鿿]{2,6}", s) and s not in seen:
                seen.add(s)
                names.append(s)
            break
    return names


def scan_text(text):
    """返回真坐标候选列表。"""
    hits = []
    for kind, pat in PATS:
        for m in pat.finditer(text):
            g = m.groupdict()
            d = g.get("d")
            mi, se = g.get("m"), g.get("s")
            hemi = g.get("h")
            if kind == "cn_deg":
                axis = "lat" if g["dir"][1] == "纬" else "lon"
                hemi = {"北": "N", "南": "S", "东": "E", "西": "W"}[g["dir"][0]]
            else:
                h = hemi.upper()
                axis = "lat" if h in ("N", "S") else "lon"
                hemi = h
            dec = to_dec(d, mi, se)
            if dec is None:
                continue
            if axis == "lat" and not (LAT_OK[0] <= dec <= LAT_OK[1]):
                continue
            if axis == "lon" and not (LON_OK[0] <= dec <= LON_OK[1]):
                continue
            ctx = text[max(0, m.start() - 220): m.end() + 220]
            # 噪声过滤：图片标记附近不算
            if IMG_CTX.search(ctx):
                continue
            # 必须有中文语境，避免纯英文参考文献页码
            if not CN_WORD.search(ctx):
                continue
            hits.append({
                "kind": kind, "axis": axis, "value": round(dec, 6), "hemi": hemi,
                "raw": re.sub(r"\s+", " ", m.group(0)).strip(),
                "pos": m.start(),
                "context": re.sub(r"\s+", " ", ctx),
            })
    return hits


def main() -> int:
    sites = load_sites()
    files = []
    for base in find_md_dirs():
        files += sorted(base.rglob("*.md"))
    if not files:
        print("没找到文献全文", file=sys.stderr)
        return 1

    hits, per_site = [], {s: [] for s in sites}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        cands = scan_text(text)
        if not cands:
            continue
        pos = []
        for sname in sites:
            for mm in re.finditer(re.escape(sname), text):
                pos.append((mm.start(), sname))
        pos.sort()
        for c in cands:
            near = None
            for pp, sname in pos:
                if abs(pp - c["pos"]) <= 400:
                    near = sname
                else:
                    break
            rec = dict(c)
            rec.pop("pos")
            rec.update({"file": p.name, "near_site": near})
            hits.append(rec)
            if near:
                per_site[near].append(rec)

    out = {"sites": sites, "files_scanned": len(files),
           "total": len(hits), "hits": hits, "per_site": per_site}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"扫描 {len(files)} 篇文献，真坐标 {len(hits)} 处 → {OUT.name}", ""]
    for h in hits:
        lines.append(f"[{h['file'][:70]}]")
        lines.append(f"   {h['axis']}={h['value']}{h['hemi']}  raw={h['raw']}  near={h['near_site']}")
        lines.append(f"   ctx: {h['context'][:300]}")
        lines.append("")
    (OUT.parent / "coord_scan_v2_readable.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"真坐标 {len(hits)} 处（报告：{OUT.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
