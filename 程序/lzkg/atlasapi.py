# -*- coding: utf-8 -*-
"""良渚时序（BP15 首页）：良渚考古年代 × 空间分区 × 文献 —— 一份现算数据。

★ 为什么单开一个端点，而不是前端去拼两个已有的
  首页的交互是「一个时间游标同时作用在年代轴、地图上」，
  前端自己拼 /api/stats/timeline-matrix + /api/stats/mapdata 会多一次往返，
  而且两边的口径要自己对齐。后端一次算好，前端只负责画。

★ 三套时间，别混
  1) **良渚考古年代**（公元前）＝ 播放轴。分期与年代来自发掘简报与测年研究，
     离线生成到 data/dict/lzkg_arch_periods.json（见 _tools/make_arch_periods.py），
     这里是唯一的读取口。
  2) **登记表时间分期标签**（新石器时代早/中/晚、当代…）＝ 教科书式分期，
     与良渚考古分期不同源，只做筛选用，**不画成年代轴**。
  3) **文献出版年**（1984–2026）＝ 文献学时间，只做筛选用。

★ 纪律
  · 数值一律现算，不缓存、不写死；
  · 不编造坐标：地图只给库里已有的真实点位（分区代表点 / 文献坐标点 / 候选遗址）；
  · 期段归属来自原文表述的自动识别，不是人工编目 → 界面必须写明这个口径。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import statsapi

PACKAGE_DIR = Path(__file__).resolve().parent
ARCH_REL = Path("data") / "dict" / "lzkg_arch_periods.json"


def arch_dict() -> dict:
    """考古分期字典。文件缺失时返回空结构（页面照常，只是没有年代轴内容）。"""
    p = PACKAGE_DIR / ARCH_REL
    if not p.exists():
        return {"meta": {}, "phases": [], "docs": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {}, "phases": [], "docs": {}}


def atlas_payload(conn: sqlite3.Connection) -> dict:
    mx = statsapi.stats_timeline_matrix(conn)
    md = statsapi.map_data(conn)
    arch = arch_dict()
    adocs = arch.get("docs") or {}

    # ── 1) 文献：按 lit_id 归并（一篇可带多个主题族、可跨多个考古期段）──
    papers: list[dict] = []
    by_id: dict[str, dict] = {}
    for pt in mx["points"]:
        p = by_id.get(pt["lit_id"])
        if p is None:
            p = by_id[pt["lit_id"]] = {
                "lit_id": pt["lit_id"], "title": pt["title"], "year": pt["year"],
                "y": pt["y"], "doc_type": pt["doc_type"],
                "period": pt["period"], "period_raw": pt.get("period_raw", ""),
                "region": pt["region"], "img_count": pt["img_count"],
                "families": [],
                "phases": list((adocs.get(pt["lit_id"]) or {}).get("phases") or []),
            }
            papers.append(p)
        p["families"].append(pt["family"])

    # ── 2) 年份刻度：逐年（缺年也列出来，轴才连续）──
    cnt: dict[int, int] = {}
    for p in papers:
        if p["y"]:
            cnt[p["y"]] = cnt.get(p["y"], 0) + 1
    ys = sorted(cnt)
    ymin, ymax = (ys[0], ys[-1]) if ys else (0, 0)
    years: list[dict] = []
    cum = 0
    for y in range(ymin, ymax + 1):
        n = cnt.get(y, 0)
        cum += n
        years.append({"year": y, "n": n, "cum": cum})

    # ── 3) 登记表分期带 → 跳文献页用的筛选值 ──
    #   挑该档在登记表里的**原文**：能命中整档篇数的那一条（LIKE 是包含匹配，
    #   所以「新石器时代晚期」一条就把「新石器时代晚期（申遗后）」也带上了）。
    bands_out = []
    for b in mx["x"]:
        raws = b.get("raws") or []
        val = ""
        for r in raws:
            c = sum(1 for p in papers if r and r in (p["period_raw"] or ""))
            if c == b["n"]:
                val = r
                break
        if not val:
            val = ",".join(raws)
        bb = dict(b)
        bb["filter_value"] = val
        bands_out.append(bb)

    # ── 4) 考古期段：分档 + 命中文献 + 原文依据 ──
    phases_out = []
    for ph in arch.get("phases") or []:
        pid = ph.get("id")
        ids = sorted(k for k, v in adocs.items() if pid in (v.get("phases") or []))
        ev = []
        for lid in ids:
            sn = ((adocs.get(lid) or {}).get("evidence") or {}).get(pid) or ""
            ev.append({"lit_id": lid, "title": (by_id.get(lid) or {}).get("title", lid),
                       "snippet": sn[:200]})
        # 有原文摘句的排前面（面板上先显示能追溯的）
        ev.sort(key=lambda x: (0 if x["snippet"] else 1, x["lit_id"]))
        item = dict(ph)
        item["lit_ids"] = ids
        item["n"] = len(ids)
        item["evidence"] = ev[:8]
        phases_out.append(item)

    ameta = arch.get("meta") or {}

    return {
        "meta": {
            "papers": len(papers),
            "points": mx["meta"]["points"],
            "year_min": ymin, "year_max": ymax,
            "no_year": sum(1 for p in papers if not p["y"]),
            "bands": len(mx["x"]), "families": len(mx["y"]),
            "img_total": mx["meta"]["img_total"],
            "papers_with_img": mx["meta"]["papers_with_img"],
            "lit_total": md["counts"]["lit"], "img_count": md["counts"]["img"],
            "regions": len(md["regions"]),
            "regions_on_map": sum(1 for r in md["regions"]
                                  if r.get("on_map") and r.get("lat") is not None),
            "arch_phases": len(phases_out),
            "arch_docs": len(adocs),
            "arch_scanned": ameta.get("docs_scanned", 0),
            # 轴左端＝最早（公元前数最大），轴右端＝最晚
            "arch_year_start": max((p["y0"] for p in phases_out), default=3500),
            "arch_year_end": min((p["y1"] for p in phases_out), default=2000),
        },
        "phases": phases_out,
        "bands": bands_out,
        "families": mx["y"],
        "years": years,
        "papers": papers,
        "map": {
            "site_group": md["site_group"],
            "regions": md["regions"],
            "points": md["points"],
            "sites": md["sites"],
        },
        "notes": [
            "**下轴＝良渚考古年代轴（公元前 3500→2000，按真实年代比例排）**，是本页的播放轴；"
            "期段与年代取自本站发掘简报与测年研究，各期段出处见右侧「原文依据」。",
            "**右轴＝文献出版年**（1984–2026，竖排：小在上、大在下），属文献学时间，"
            "与考古年代不是同一套时间体系，只做「截至该年」筛选。",
            "登记表的「时间分期标签」（新石器时代早/中/晚、当代…）是教科书式分期，"
            "与良渚考古分期不同源 → 只在右栏做筛选，不画成年代轴。",
            "期段归属由文献原文表述与绝对年代**自动识别**（{matched}/{scanned} 篇命中），"
            "非人工编目；未命中者不入期。",
            "考古分期年代存在学术分歧（如晚期下限前2500 与钟家港南段简报的前2700～前2300），"
            "本轴取常见切分并在各期段「口径」中标注差异，不做唯一解。",
            "地图点位＝库里已有的真实坐标：空间分区代表点是区域统一坐标（±1.1 km 示意），"
            "**不是遗址实测坐标**；候选遗址多数无坐标，不画点。",
            "年份区间无法用文献页表达（后端年份是精确匹配），跳转文献页只带登记分期或分区。",
            "所有数值现算，随库变化。",
        ],
    }
