# -*- coding: utf-8 -*-
"""良渚时序（BP15 首页）：出版年 × 良渚分期 × 空间分区 —— 一份现算数据。

★ 为什么单开一个端点，而不是前端去拼两个已有的
  首页的交互是「一个时间游标同时作用在年份轴、分期带、地图上」，
  前端自己拼 /api/stats/timeline-matrix + /api/stats/mapdata 会多一次往返，
  而且两边的年份口径要自己对齐（矩阵点里取 y，mapdata 的 lits 里取 year）。
  后端一次算好，前端只负责画。

★ 纪律
  · 数值一律现算，不缓存、不写死；
  · 不编造坐标：地图只给库里已有的真实点位（分区代表点 / 文献坐标点 / 候选遗址）；
  · 分期带是**登记表的时间分期标签**归一，不是考古绝对年代 → 界面上等宽排。
"""
from __future__ import annotations

import sqlite3

from . import statsapi


def atlas_payload(conn: sqlite3.Connection) -> dict:
    mx = statsapi.stats_timeline_matrix(conn)
    md = statsapi.map_data(conn)

    # ── 1) 文献：按 lit_id 归并（一篇可带多个主题族）──
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

    # ── 3) 分期带 → 跳文献页用的筛选值 ──
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
        },
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
            "上轴＝**文献出版年**（不是良渚分期年）；库里年份跨度 1984–2026，"
            "分布明显右偏（2015 年后约占六成半）。",
            "下轴＝良渚文化分期带：登记表「时间分期标签」的 9 个原文取值归一为 8 档，"
            "**等宽排列** —— 库里没有考古绝对年代，不按年代比例画宽度。",
            "「当代（申遗后/当代研究）」单列一档，不并进考古分期。",
            "主题族码（S-08xx）不在受控词表里（词表 topic 用 S-09xx），只按码前缀分组，不重编码。",
            "地图点位＝库里已有的真实坐标：空间分区代表点是区域统一坐标（±1.1 km 示意），"
            "**不是遗址实测坐标**；候选遗址多数无坐标，不画点。",
            "年份区间无法用文献页表达（后端年份是精确匹配），跳转文献页只带分期或分区。",
            "所有数值现算，随库变化。",
        ],
    }
