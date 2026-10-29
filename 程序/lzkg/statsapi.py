# -*- coding: utf-8 -*-
"""P2 统计 / 地图 / 遗址点位 / AI 问答 + P3 体检。

纪律
----
1. **不编造坐标**：`data/遗址点位表.xlsx` 是空表 + 候选清单，等人工补坐标。
   坐标补齐前，`/api/stats/map.png` **自动退化成「空间分区文献数」柱状图**，
   图上注明「坐标待补，当前显示分区统计」。
2. **不假装有 AI**：没配 API key 就**降级**成「本地 FTS5 检索结果摘要」，
   `degraded=true`，并在 answer 里写明这是检索结果、不是大模型生成的回答。
3. **不写死活口径**：条数、依赖数等一律现算。
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from .common import now_str
from .config import get_config
from .db import MIN_FTS_LEN, count, fts_query, like_clause, needs_like

# matplotlib 必须用 Agg（无显示环境）
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

C0392B = "#C0392B"   # 强调红（与前端一致）


def _setup_font() -> str:
    """挑一个本机有的中文字体；挑不到就返回空（不静默出方块字）。"""
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "SimSun"):
        if name in have:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return ""


# ── 统计 ────────────────────────────────────────────────────────────────
def stats_timeline(conn: sqlite3.Connection) -> dict:
    """时间线：分期 / 出版年 / 文献类型。**现算**。"""
    periods = [{"k": r["name"], "n": r["n"]} for r in conn.execute(
        "SELECT name, SUM(lit_count) n FROM tags WHERE dim='period' "
        "GROUP BY name ORDER BY n DESC, name")]
    years = [{"k": r["year"], "n": r["n"]} for r in conn.execute(
        "SELECT year, COUNT(*) n FROM literatures WHERE year IS NOT NULL AND year<>'' "
        "GROUP BY year ORDER BY year")]
    doc_types = [{"k": r["doc_type"], "n": r["n"]} for r in conn.execute(
        "SELECT doc_type, COUNT(*) n FROM literatures GROUP BY doc_type "
        "ORDER BY n DESC, doc_type")]
    return {"periods": periods, "years": years, "doc_types": doc_types,
            "note": "分期按 tags.dim='period' 的文献数；出版年为文献出版年，非良渚分期年。"}


def stats_region(conn: sqlite3.Connection) -> dict:
    """各空间分区文献数。"""
    rows = [{"k": r["name"], "n": r["n"]} for r in conn.execute(
        "SELECT name, SUM(lit_count) n FROM tags WHERE dim='region' "
        "GROUP BY name ORDER BY n DESC, name")]
    return {"items": rows, "total": sum(r["n"] for r in rows),
            "note": "按标签表 dim='region' 统计；一篇文献可带多个分区标签，故合计可能大于文献总数。"}


# ── 遗址点位表 ──────────────────────────────────────────────────────────
def load_sites(path: Path) -> list[dict]:
    """读 `data/遗址点位表.xlsx`。

    ★ 表里只有**人工补好的**坐标才算数；没有坐标的行照样列出（坐标给 None），
      便于界面显示「待补」，但绝不编造经纬度。
    """
    if not path or not Path(path).exists():
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    if not rows:
        return []
    # 表头按**列名**定位：找含「遗址名称 / 纬度 / 经度」这类**列名**的那一行。
    # ⚠️ 不能只按「含『遗址』」找 —— 第 1 行标题「良渚文化遗址点位表…」也含这两个字，
    #   会把标题行误当表头 → 整表读不出数据。
    KEY_NAMES = ("site_name", "lat", "lon", "遗址名称", "纬度", "经度")
    hidx = None
    for i, r in enumerate(rows[:10]):
        names = [str(v or "").strip().lower() for v in r]
        if sum(1 for n in names if any(k.lower() in n for k in KEY_NAMES)) >= 2:
            hidx = i
            break
    if hidx is None:
        return []
    header = [str(v or "").strip() for v in rows[hidx]]

    def col(*keys):
        for i, h in enumerate(header):
            for k in keys:
                if k in h:
                    return i
        return None

    i_name = col("遗址名称", "site_name", "名称")
    i_lat = col("纬度", "lat")
    i_lon = col("经度", "lon", "lng")
    i_lit = col("坐标出处文献", "source_lit_id", "文献标识符")
    i_note = col("坐标出处说明", "备注", "note")

    def cell(r, idx):
        if idx is None or idx >= len(r):
            return None
        v = r[idx]
        return v

    out = []
    for r in rows[hidx + 1:]:
        if not any(v not in (None, "") for v in r):
            continue
        name = cell(r, i_name)
        if name in (None, ""):
            continue
        lat, lon = cell(r, i_lat), cell(r, i_lon)
        out.append({
            "site_name": str(name).strip(),
            "lat": float(lat) if isinstance(lat, (int, float)) else None,
            "lon": float(lon) if isinstance(lon, (int, float)) else None,
            "source_lit_id": (str(cell(r, i_lit)).strip()
                              if cell(r, i_lit) not in (None, "") else ""),
            "note": (str(cell(r, i_note)).strip()
                     if cell(r, i_note) not in (None, "") else ""),
        })
    return out


def sites_payload(conn: sqlite3.Connection) -> dict:
    cfg = get_config()
    items = load_sites(cfg.sites_path)
    regions = load_regions(cfg.sites_path)
    with_coord = [s for s in items if s["lat"] is not None and s["lon"] is not None]
    region_pts = [r for r in regions if r["on_map"] and r["lat"] is not None]
    return {"items": items, "total": len(items),
            "with_coordinates": len(with_coord),
            "regions": regions,
            "regions_total": len(regions),
            "regions_with_points": len(region_pts),
            # ★ 只给**文件名**，不给绝对路径 —— 这个端点是公开可读的，
            #   回绝对路径等于给公网访客送一张本机目录地图
            #   （同 `/api/health/detail` 那条安全线）。
            "source_file": Path(cfg.sites_path).name,
            "note": ("遗址坐标由人工补录；未补的行坐标为空，不做任何推测填充。"
                     "空间分区用**区域代表点**（示意精度约 ±1.1 km，非遗址实测坐标）；"
                     "主题类与范围类分区不配点，不上图。")}


def load_regions(path: Path) -> list[dict]:
    """读 `data/遗址点位表.xlsx` 的「区域代表点」工作表。

    ★ 区域代表点是**区域区块的统一坐标**（示意精度，两位小数 ≈ ±1.1 km），
      不是遗址实测坐标。只有「是否上图=是」且有坐标的才画到图上——
      主题类（文化创意/数字化/参考案例）与范围类（中国综合/浙江综合/跨遗址）
      分区**不配点**：给它们一个点就是在编造位置。
    """
    if not path or not Path(path).exists():
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = None
        for name in wb.sheetnames:
            if "区域" in name:
                ws = wb[name]
                break
        if ws is None:
            return []
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    if not rows:
        return []

    KEY = ("区域名", "纬度", "经度", "是否上图")
    hidx = None
    for i, r in enumerate(rows[:10]):
        names = [str(v or "").strip() for v in r]
        if sum(1 for n in names if any(k in n for k in KEY)) >= 3:
            hidx = i
            break
    if hidx is None:
        return []
    header = [str(v or "").strip() for v in rows[hidx]]

    def col(*keys):
        for i, h in enumerate(header):
            for k in keys:
                if k in h:
                    return i
        return None

    i_name = col("区域名", "区域", "name")
    i_lat = col("纬度", "lat")
    i_lon = col("经度", "lon", "lng")
    i_map = col("是否上图", "上图", "on_map")
    i_basis = col("坐标依据", "依据", "basis")
    i_note = col("备注", "note")

    def cell(r, idx):
        if idx is None or idx >= len(r):
            return None
        return r[idx]

    out = []
    for r in rows[hidx + 1:]:
        name = cell(r, i_name)
        if name in (None, ""):
            continue
        lat, lon = cell(r, i_lat), cell(r, i_lon)
        on_map = str(cell(r, i_map) or "").strip()
        out.append({
            "region": str(name).strip(),
            "lat": float(lat) if isinstance(lat, (int, float)) else None,
            "lon": float(lon) if isinstance(lon, (int, float)) else None,
            "on_map": on_map == "是",
            "basis": str(cell(r, i_basis) or "").strip(),
            "note": str(cell(r, i_note) or "").strip(),
        })
    return out


def load_geo() -> dict:
    """读机读坐标 `data/遗址坐标.json`（地图与 API 的单一事实来源）。

    这份 JSON 由 `_tools/make_coord_ledger.py` 从**文献证据台账**生成，
    每条坐标都带出处（文献编号或「公开权威资料」）。
    """
    p = Path(__file__).resolve().parent.parent / "data" / "遗址坐标.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 坐标点 → 文献 的关键词口径（模块级，map_data 与 lit_map_data 共用同一份）──
# ★ 关键词必须**精确到这一篇/这一类**，不能图省事回退到「良渚」这种宽泛词 ——
#   回退过一次，结果「MFL 高坝剖面点」关联出 120 篇（其实只有 1~2 篇相关），
#   那是**假关联**，比不关联更坏。
# ★ 映射的键必须是**点名字里真实出现的片段**（子串匹配），
#   不是随手起的内部代号 —— 用代号会一个都匹配不上（踩过）。
POINT_KEYS = {
    "MFL2019": ["MFL2019", "蜜蜂垄", "坝体", "水利工程环境响应"],
    "MFL 高坝": ["MFL profile", "Intensification of rice farming", "reservoir"],
    "卫星影像": ["遥感", "卫星", "CORONA", "航片", "历史影像", "深度学习"],
    "良渚古城遗址整体": ["良渚古城", "良渚遗址群", "古城遗址", "良渚遗址",
                         "余杭良渚"],
}


def point_keys_for(point_name: str) -> list[str]:
    """按**点名里真实出现的片段**取该点的文献匹配关键词；取不到就是空列表。"""
    for k, kk in POINT_KEYS.items():
        if k in (point_name or ""):
            return list(kk)
    return []


def map_data(conn: sqlite3.Connection) -> dict:
    """交互地图的**点级数据**：地图上每个东西对应哪些文献。

    ★ 为什么要这个端点
      之前地图是后端出的静态 PNG —— 前端拿到的是「已经定死的图」，
      点不动、悬停不了、也没法按点筛文献。
      交互的前提是**前端能拿到点级数据并自己决定画什么**，
      所以这里把「点 + 每个点关联的文献」现算给前端。

    ★ 口径全部写明，不含糊
      · 分区代表点 → 按 `tags.dim='region'` 的名称关联（精确匹配）
      · 文献坐标点 / 候选遗址 → 按**题名/摘要/关键词/刊名的文本命中**关联
      · 数值一律现算
    """
    cfg = get_config()
    geo = load_geo()
    grp = geo.get("site_group") or {}

    lits = {}
    for r in conn.execute("SELECT lit_id, title, year, abstract, keywords, venue "
                          "FROM literatures"):
        lits[r["lit_id"]] = {
            "lit_id": r["lit_id"], "title": r["title"] or "",
            "year": r["year"] or "", "venue": r["venue"] or "",
            "abstract": r["abstract"] or "", "keywords": r["keywords"] or "",
        }

    def text_of(l):
        return " ".join([l["title"], l["abstract"], l["keywords"], l["venue"]])

    def match_lits(keys: list[str]) -> list[dict]:
        """按关键词命中关联文献（题名/摘要/关键词/刊名）。"""
        out = []
        for l in lits.values():
            t = text_of(l)
            if any(k and k in t for k in keys):
                out.append({"lit_id": l["lit_id"], "title": l["title"],
                            "year": l["year"]})
        out.sort(key=lambda x: (-int(x["year"] or 0) if str(x["year"]).isdigit() else 0,
                                 x["lit_id"]))
        return out

    # ── 文献坐标点（4 个）：每个点带关键词口径 ──
    points = []
    for p in geo.get("points", []):
        keys = point_keys_for(p.get("name", ""))
        rel = match_lits(keys)
        points.append({
            "id": "pt-" + str(len(points) + 1),
            "name": p.get("name", ""), "lon": p.get("lon"), "lat": p.get("lat"),
            "kind": p.get("kind", ""), "source": p.get("source", ""),
            "basis": p.get("basis", ""),
            "lit_count": len(rel), "lits": rel[:50],
            "match_keys": keys,
            "linked": bool(keys),
        })

    # ── 空间分区（12 个，6 个上图）──
    regions = []
    for r in load_regions(cfg.sites_path):
        rows = list(conn.execute(
            "SELECT t.lit_id FROM lit_tags t JOIN tags g ON g.tag_id=t.tag_id "
            "WHERE g.dim='region' AND g.name=?", (r["region"],)))
        rel = [{"lit_id": x["lit_id"], "title": lits.get(x["lit_id"], {}).get("title", ""),
                "year": lits.get(x["lit_id"], {}).get("year", "")} for x in rows]
        rel = [x for x in rel if x["lit_id"]]
        regions.append({
            "name": r["region"], "lat": r["lat"], "lon": r["lon"],
            "on_map": r["on_map"], "basis": r["basis"], "note": r["note"],
            "lit_count": len(rel), "lits": rel[:50],
        })

    # ── 18 个候选遗址（单遗址坐标多数为空，只给关联文献数）──
    site_lit = {}
    for s in load_sites(cfg.sites_path):
        rel = match_lits([s["site_name"]])
        site_lit[s["site_name"]] = len(rel)
    sites = []
    for s in load_sites(cfg.sites_path):
        sites.append({
            "name": s["site_name"], "lat": s["lat"], "lon": s["lon"],
            "note": s.get("note", ""),
            "lit_count": site_lit.get(s["site_name"], 0),
        })

    return {
        "site_group": {
            "name": grp.get("name", "良渚遗址群"),
            "lon_min": grp.get("lon_min"), "lon_max": grp.get("lon_max"),
            "lat_min": grp.get("lat_min"), "lat_max": grp.get("lat_max"),
            "center_lon": grp.get("center_lon"), "center_lat": grp.get("center_lat"),
            "source": grp.get("source", ""), "source_public": grp.get("source_public", ""),
            "quote": grp.get("quote", ""),
        },
        "points": points,
        "regions": regions,
        "sites": sites,
        "counts": {
            "lit": count(conn, "literatures"),
            "img": count(conn, "images"),
        },
        "notes": [
            "文献坐标点：出处见「坐标证据台账」，每条带文献标识符",
            "分区代表点：区域区块统一坐标，精度约 ±1.1 km，**不是遗址实测坐标**",
            "关联文献口径：分区按标签精确关联；坐标点与候选遗址按题名/摘要/关键词/刊名文本命中",
            "所有数值现算，随库变化",
        ],
    }


# ── 坐标标记的四档精度（详情页「地图标记」用）──
# ★ 为什么要分档：坐标的**精度差别极大** ——
#   本篇给出的剖面点（米级） / 公开资料的遗址整体坐标（百米级） /
#   分区代表点（±1.1 km，区块统一坐标）。混在一起画在图上会让人以为
#   「每篇文献都有一个精确位置」—— 那是骗人。所以每条标记都必须自带精度标签。
TIER_LABEL = {
    "lit_coord": "文献坐标",
    "public": "公开权威坐标",
    "region": "分区代表点",
    "none": "未定位",
}
TIER_ORDER = ["lit_coord", "public", "region"]


def lit_map_data(conn: sqlite3.Connection, lit_id: str) -> dict:
    """**按文献**返回它的地图标记，并**分档标精度**。

    ★ 为什么要有这个端点
      `/api/stats/mapdata` 是「点 → 哪些文献」的**反向**索引（地图页用）。
      详情页要的是**正向**的：「这一篇文献在地图上落在哪里、那个坐标有多准」。
      两边口径必须同一份关键词表，所以 `POINT_KEYS` 已提到模块级共用。

    ★ 四档
      ① `lit_coord` 文献坐标 —— 坐标由**文献**给出（带出处文献号）
         · `from_this_paper=true`：出处就是**本篇** → 精度最高
         · `from_this_paper=false`：出处是**他篇**，本篇只是文本命中 → 精度依来源文献
      ② `public` 公开权威坐标 —— 出处是公开权威资料（如良渚古城遗址整体）
      ③ `region` 分区代表点 —— 本篇带空间分区标签，落在分区代表点上
         （区块统一坐标，**示意精度约 ±1.1 km**，不是遗址实测坐标）
      ④ `none` 未定位 —— 以上都没有；**不推测填充**

    ★ 纪律
      · 匹配不到就是匹配不到 → 报「未定位」，不给假位置
      · 主题类分区（数字化/文化创意/参考案例/跨遗址…）**不配点** ——
        给它们一个点就是在编造位置
      · 数值一律现算
    """
    cfg = get_config()
    geo = load_geo()
    grp = geo.get("site_group") or {}

    row = conn.execute("SELECT lit_id, title, year, venue, abstract, keywords "
                       "FROM literatures WHERE lit_id = ?", (lit_id,)).fetchone()
    if row is None:
        return {"lit_id": lit_id, "found": False, "markers": [], "level": "none"}

    lit = {"lit_id": row["lit_id"], "title": row["title"] or "",
           "year": row["year"] or "", "venue": row["venue"] or "",
           "abstract": row["abstract"] or "", "keywords": row["keywords"] or ""}
    text = " ".join([lit["title"], lit["abstract"], lit["keywords"], lit["venue"]])

    def title_of(lid: str) -> str:
        if not lid:
            return ""
        r = conn.execute("SELECT title FROM literatures WHERE lit_id = ?",
                         (lid,)).fetchone()
        return (r["title"] or "") if r else ""

    markers: list[dict] = []

    # ── ① / ② 文献坐标点与公开权威坐标 ──
    for p in geo.get("points", []):
        name = p.get("name", "")
        src = str(p.get("source") or "")
        keys = point_keys_for(name)
        hit = [k for k in keys if k and k in text]
        is_public = ("公开权威" in src) or (not src.startswith("LIT-"))
        if src == lit["lit_id"]:
            tier = "public" if is_public else "lit_coord"
            why = "命中依据：本篇即该坐标的出处文献"
        elif is_public:
            if not hit:
                continue
            tier = "public"
            why = "命中依据：正文/题名/关键词/刊名中出现「%s」" % "、".join(hit[:4])
        else:
            if not hit:
                continue
            tier = "lit_coord"
            why = "命中依据：正文/题名/关键词/刊名中出现「%s」" % "、".join(hit[:4])
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            continue
        from_self = (src == lit["lit_id"])
        if tier == "lit_coord":
            precision = ("本篇给出的点级坐标（出处文献即本篇）" if from_self else
                         "依来源文献《%s》给出的点级坐标" % (title_of(src) or src))
        else:
            precision = "公开权威资料的遗址整体坐标"
        markers.append({
            "kind": "point", "name": name, "lat": lat, "lon": lon,
            "tier": tier, "tier_label": TIER_LABEL[tier],
            "from_this_paper": from_self,
            "source": src, "source_title": title_of(src) if src.startswith("LIT-") else src,
            "basis": p.get("basis", ""),
            "precision": precision,
            "why": why,
        })

    # ── ③ 分区代表点（只取「是否上图=是」且有坐标的分区）──
    reg_rows = list(conn.execute(
        "SELECT t.name FROM lit_tags tt JOIN tags t ON t.tag_id = tt.tag_id "
        "WHERE tt.lit_id = ? AND t.dim = 'region'", (lit["lit_id"],)))
    reg_names = {r["name"] for r in reg_rows}
    for r in load_regions(cfg.sites_path):
        if r["region"] not in reg_names:
            continue
        if not r["on_map"] or r["lat"] is None or r["lon"] is None:
            continue
        markers.append({
            "kind": "region", "name": r["region"], "lat": r["lat"], "lon": r["lon"],
            "tier": "region", "tier_label": TIER_LABEL["region"],
            "from_this_paper": True,
            "source": r["basis"] or "空间分区代表点",
            "source_title": r["basis"] or "",
            "basis": r["basis"], "note": r["note"],
            "precision": "区域区块统一坐标，示意精度约 ±1.1 km（非遗址实测坐标）",
            "why": "命中依据：本篇带空间分区标签「%s」" % r["region"],
        })

    # 精度排序：文献坐标 → 公开权威 → 分区代表点
    markers.sort(key=lambda m: (TIER_ORDER.index(m["tier"]), m["kind"], m["name"]))

    # ── 候选遗址（坐标多数为空 → 只登记名称与「坐标待补」，不配点）──
    pending = []
    for s in load_sites(cfg.sites_path):
        if s["site_name"] and s["site_name"] in text:
            pending.append({"name": s["site_name"],
                            "lat": s.get("lat"), "lon": s.get("lon"),
                            "note": s.get("note", "")})

    level = markers[0]["tier"] if markers else "none"
    return {
        "lit_id": lit["lit_id"], "found": True, "title": lit["title"],
        "year": lit["year"],
        "level": level, "level_label": TIER_LABEL.get(level, TIER_LABEL["none"]),
        "markers": markers,
        "pending_sites": pending,
        "site_group": {
            "name": grp.get("name", "良渚遗址群"),
            "lon_min": grp.get("lon_min"), "lon_max": grp.get("lon_max"),
            "lat_min": grp.get("lat_min"), "lat_max": grp.get("lat_max"),
            "center_lon": grp.get("center_lon"), "center_lat": grp.get("center_lat"),
            "source": grp.get("source", ""),
        },
        "notes": [
            "坐标一律以文献为准（WGS84），未做任何推测填充",
            "分区代表点是区块统一坐标，示意精度约 ±1.1 km，不是遗址实测坐标",
            "主题类分区不配点；候选遗址坐标待人工补录",
            "关联口径与「统计与地图」页共用同一份关键词表",
        ],
    }


def map_png(conn: sqlite3.Connection) -> tuple[bytes, str]:
    """后端离线出图：**三联图**（世界 / 中国 / 良渚区域）。→ (png 字节, 图上说明)。

    ★ 为什么是三联
      单看良渚的散点，读者不知道它在世界上、在中国的什么位置。
      三张图一气呵成：世界（点在哪）→ 中国（浙江省余杭区）→ 区域（太湖与遗址群）。

    ★ 底图从哪来
      `data/basemap/*.geojson`（Natural Earth 公共域 + Overpass/OSM），
      **本地读取、零联网**。中国视图用「省级拼图」，不画 admin_0 国界 → 不涉边界争议。

    ★ 坐标一律不推测填充
      表里没有就是没有：只有文献直给的点、公开权威坐标点、
      以及从文献抄来的遗址群范围框会被画出来。
    """
    font = _setup_font()
    cfg = get_config()
    sites = load_sites(cfg.sites_path)
    with_coord = [s for s in sites if s["lat"] is not None and s["lon"] is not None]
    regions = [r for r in load_regions(cfg.sites_path)
               if r["on_map"] and r["lat"] is not None and r["lon"] is not None]
    geo = load_geo()
    grp = geo.get("site_group") or {}
    pts = geo.get("points") or []

    from . import basemap

    # 布局：世界横条在上（世界地图天然 2.5:1），中国与良渚区域并排在下
    fig = plt.figure(figsize=(16.5, 9.2), dpi=140)
    gs = fig.add_gridspec(2, 2, height_ratios=[0.62, 1.0],
                          width_ratios=[1.0, 0.58])
    ax_w = fig.add_subplot(gs[0, :])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_r = fig.add_subplot(gs[1, 1])

    center = (grp.get("center_lon"), grp.get("center_lat")) if grp else None
    if center and center[0] is None:
        center = None

    # ── 世界 ──
    basemap.draw_world(ax_w, hl=center)
    ax_w.set_title("① 世界：良渚古城遗址在世界的位置", fontsize=11, pad=6)

    # ── 中国 ──
    basemap.draw_china(ax_c, hl=center)
    ax_c.set_title("② 中国：浙江省杭州市余杭区（省级拼图 · 边界仅作位置示意）",
                   fontsize=10.5, pad=8)

    # ── 良渚区域 ──
    # 视野固定含太湖：良渚在太湖东南，两者相距约 25 km，
    # 只按遗址群范围框取景会把太湖整片裁掉。
    bbox = (119.78, 30.08, 120.62, 31.42)
    basemap.draw_region(ax_r, bbox=bbox)

    # 文献范围框（虚线矩形）
    if grp:
        ax_r.plot([grp["lon_min"], grp["lon_max"], grp["lon_max"],
                   grp["lon_min"], grp["lon_min"]],
                  [grp["lat_min"], grp["lat_min"], grp["lat_max"],
                   grp["lat_max"], grp["lat_min"]],
                  ls="--", lw=1.3, color=C0392B, zorder=7,
                  label="良渚遗址群范围（据文献调查简报）")

    # 文献/公开坐标点 —— **同址点先合并**再标注。
    # 这 4 个点（两个坝体剖面 + 影像中心 + 遗址整体）落在同一方圆里，
    # 不合并的话四个标签会横着叠成一团黑，反而什么都读不出来。
    merged: list[dict] = []
    for p in pts:
        hit = None
        for g in merged:
            if (abs(g["lon"] - p["lon"]) <= 0.04 and abs(g["lat"] - p["lat"]) <= 0.04):
                hit = g
                break
        if hit is None:
            merged.append({"lon": p["lon"], "lat": p["lat"], "names": [p["name"]]})
        else:
            hit["names"].append(p["name"])
    offsets = [(10, 8), (-10, -14), (10, -13), (11, 10)]
    for i, g in enumerate(merged):
        ax_r.plot(g["lon"], g["lat"], "o", ms=6, color=C0392B,
                  mec="white", mew=0.8, zorder=8)
        dx, dy = offsets[i % len(offsets)]
        ax_r.annotate("\n".join(g["names"]), (g["lon"], g["lat"]), fontsize=7.0,
                      color="#333", xytext=(dx, dy), textcoords="offset points",
                      zorder=9, ha="left" if dx > 0 else "right",
                      bbox=dict(boxstyle="round,pad=0.18", fc="white",
                                ec="none", alpha=0.75))

    # 区域代表点（示意，星形）
    if regions:
        groups: list[dict] = []
        for r in regions:
            hit = None
            for g in groups:
                if abs(g["lat"] - r["lat"]) <= 0.05 and abs(g["lon"] - r["lon"]) <= 0.05:
                    hit = g
                    break
            if hit is None:
                groups.append({"lat": r["lat"], "lon": r["lon"], "names": [r["region"]]})
            else:
                hit["names"].append(r["region"])
        ax_r.plot([g["lon"] for g in groups], [g["lat"] for g in groups],
                  "*", ms=15, color="#E08A1E", mec="white", mew=0.7, zorder=9,
                  label="空间分区代表点（示意，±1.1 km）")

    # 人工补录的遗址实测坐标
    if with_coord:
        ax_r.plot([s["lon"] for s in with_coord], [s["lat"] for s in with_coord],
                  "v", ms=9, color="#1F7A5C", mec="white", mew=0.8, zorder=9,
                  label="遗址实测坐标（人工补录）")

    basemap.scalebar(ax_r, 119.9, 30.1, km=5.0)
    basemap.north_arrow(ax_r, x=0.92, y=0.80, size=0.10)
    ax_r.set_title("③ 良渚区域：遗址群、太湖与钱塘江", fontsize=10.5, pad=8)
    leg = ax_r.legend(loc="lower left", fontsize=7.4, framealpha=0.92,
                      borderpad=0.5, labelspacing=0.4)

    mode_parts = ["triptych", "world+china+region",
                  f"range:{int(bool(grp))}", f"points:{len(pts)}",
                  f"sites:{len(with_coord)}", f"region_points:{len(regions)}"]
    fig.suptitle("良渚文化文献的空间分布：世界 → 中国 → 遗址群\n"
                 f"出图时间 {now_str()}", fontsize=12.5, y=0.985)
    fig.text(0.5, 0.008,
             basemap.BASEMAP_NOTE + "；坐标：WGS84 十进制度，出处见「坐标证据台账」",
             ha="center", fontsize=8.5, color="#666")
    fig.tight_layout(rect=(0, 0.022, 1, 0.945))
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue(), ":".join(mode_parts)


# ── AI 问答 ─────────────────────────────────────────────────────────────
def _query_terms(question: str) -> list[str]:
    """把一个**自然语言问题**拆成检索词。

    ★ 为什么不能把整句当一个词
    ----------------------------
    trigram 索引里 "良渚玉琮的分期研究" 是**一个短语**，要求原文里逐字出现这一整串。
    自然语言问题几乎不可能逐字出现 → FTS 必然零命中；
    退回 LIKE 时用整句做 '%整句%' 也匹配不上 → 结果永远是「没检索到」。

    中文没有空格分词，所以用**滑窗 3-gram**（shingle）近似切词：
    "良渚玉琮的分期研究" → 良渚玉 / 渚玉琮 / 玉琮的 / 琮的分 / 的分期 / 分期研 / 期研究
    英文与数字仍按空白切。这样 FTS 的 OR 查询能召回**包含其中任意一段**的文献。
    """
    import re
    q = re.sub(r"[？?！!。，,；;：:、“”\"'（）()【】\[\]…—\-]+", " ", question or "")
    terms: list[str] = []
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,}", q):
        terms.append(w)
    for run in re.findall(r"[一-鿿]{2,}", q):
        if len(run) <= 3:
            terms.append(run)
        else:
            for i in range(len(run) - 2):
                terms.append(run[i:i + 3])
    out, seen = [], set()
    for t in terms:
        t = t.strip()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:16]


def _fts_search(conn: sqlite3.Connection, question: str, top_k: int) -> tuple[list, str]:
    """本地 FTS5 检索，返回 (命中列表, 检索模式)。

    ★ 返回值**永远是二元组**。
      之前这里有两处 `return []`（列表），调用方 `results, mode = _fts_search(...)`
      一解包就 ValueError → 整个问答端点 500。自然语言问题零命中是常态，
      所以这条路径几乎必踩。
    """
    import re as _re
    terms = _query_terms(question)
    if not terms:
        return [], "empty"
    long_terms = [t for t in terms if len(t) >= MIN_FTS_LEN]
    short_terms = [t for t in terms if len(t) < MIN_FTS_LEN]

    hits: dict[str, str] = {}
    mode = "like"
    # ── FTS5：≥3 字的词用 OR 连起来（bm25 会偏向命中词更多的文档）──
    if long_terms:
        expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
        try:
            for r in conn.execute(
                    "SELECT lit_id, snippet(docs_fts, 1, '', '', ' … ', 24) s "
                    "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?",
                    (expr, max(top_k * 8, 40))):
                hits[r["lit_id"]] = (r["s"] or "").replace("\n", " ")
            mode = "fts5"
        except sqlite3.OperationalError:
            hits = {}
    # ── 兜底：两字词（trigram 索引不了）走 LIKE ──
    if not hits and short_terms:
        where, params = like_clause(short_terms, ["content"])
        for r in conn.execute(
                f"SELECT lit_id, substr(content, max(instr(content, ?), 1) - 40, 160) s "
                f"FROM docs_fts WHERE {where} LIMIT ?",
                [short_terms[0]] + params + [max(top_k * 8, 40)]):
            frag = (r["s"] or "").replace("\n", " ")
            hits[r["lit_id"]] = ("…" + frag + "…").strip()
        mode = "like"
    if not hits:
        return [], mode
    ids = list(hits.keys())[:max(top_k * 8, 40)]
    q = (f"SELECT lit_id, title, year, doc_type, open_type FROM literatures "
         f"WHERE lit_id IN ({','.join('?' * len(ids))})")
    meta = {r["lit_id"]: dict(r) for r in conn.execute(q, ids)}
    out = []
    for lid in ids:
        if lid not in meta:
            continue
        m = meta[lid]
        out.append({"lit_id": lid, "title": m.get("title") or "",
                    "year": m.get("year") or "", "doc_type": m.get("doc_type") or "",
                    "open_type": m.get("open_type") or "",
                    "snippet": hits.get(lid, "")})
    return out[:top_k], mode


def ai_ask(conn: sqlite3.Connection, question: str, top_k: int = 5,
           client_cfg: dict | None = None) -> dict:
    """AI 问答。

    ★ 配置来源有优先级：**浏览器传来的**（每人自己的 key）→ 服务端 `config.json`。
      浏览器那套是主力：每人各填各的，费用与额度各归各人，服务端不落盘 key。
    ★ 没配好 → **降级**：不报错、不编答案，返回本地 FTS5 检索摘要，
      `degraded=true`，answer 里写明这是检索结果、不是模型生成的回答。
    """
    from . import ai_providers

    cfg = get_config()
    server_ai = cfg.ai_cfg or {}
    question = (question or "").strip()
    if not question:
        return {"answer": "问题为空：请说明想查什么（例如「良渚玉器的考古发现」）。",
                "citations": [], "provider": "none", "degraded": True,
                "note": "空问题未发起检索。"}

    results, mode = _fts_search(conn, question, max(1, min(int(top_k or 5), 20)))

    # 浏览器配置优先；没有就用服务端配置
    if client_cfg:
        ai = ai_providers.normalize(client_cfg)
    else:
        ai = ai_providers.normalize({
            "provider": server_ai.get("provider") or "openai",
            "api_base": server_ai.get("api_base") or "",
            "api_key": server_ai.get("api_key") or "",
            "model": server_ai.get("model") or "",
            "timeout": server_ai.get("timeout") or 90,
        })
    enabled = bool(server_ai.get("enabled", True))

    if not (enabled and ai_providers.is_ready(ai)):
        # ── 降级模式：本地检索摘要 ──────────────────────────────
        src = "浏览器里的 AI 设置" if client_cfg else "config.json 的 ai.api_key"
        if not results:
            answer = (f"未配置 AI 接口（{src} 为空），"
                      f"已降级为本地全文检索；本次检索「{question}」**没有命中文献**。\n"
                      f"⚠️ 以下（无）内容是**本地 FTS5 检索结果**，"
                      f"**不是大模型生成的回答**。")
            return {"answer": answer, "citations": [], "provider": "local-fts5",
                    "degraded": True, "mode": mode,
                    "note": "未配置 AI 接口 → 降级为本地检索结果摘要。"}
        lines = [f"未配置 AI 接口（{src} 为空），已降级为本地全文检索。",
                 f"检索到 {len(results)} 篇相关文献，主要内容摘要如下："]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. [{r['lit_id']}] {r['title']}"
                f"（{r['year'] or '年份不详'}，{r['doc_type'] or '类型未标注'}）"
                f"{r['snippet']}")
        lines.append("⚠️ 以上为**本地 FTS5 检索命中的原文片段摘要**，"
                     "**不是大模型生成的回答**，也不构成综述或结论。")
        return {"answer": "\n".join(lines),
                "citations": [{"lit_id": r["lit_id"], "title": r["title"],
                               "snippet": r["snippet"]} for r in results],
                "provider": "local-fts5", "degraded": True, "mode": mode,
                "note": "未配置 AI 接口 → 降级为本地检索结果摘要（非模型生成）。"}

    # ── 真配了：按协议调模型 ──────────────────────────────────
    context = "\n\n".join(
        f"[{i}] 文献标识符：{r['lit_id']}\n题名：{r['title']}\n"
        f"年份：{r['year']}\n类型：{r['doc_type']}\n片段：{r['snippet']}"
        for i, r in enumerate(results, 1))
    prompt = (
        "你是考古文献目录站的检索助手。**只依据下面给出的文献片段作答**，"
        "不要补充片段之外的知识；每个结论后面用 [序号] 标注依据的片段。"
        "若片段不足以回答，就直接说「现有片段不足以回答」。\n\n"
        f"问题：{question}\n\n文献片段：\n{context}\n")
    try:
        text = ai_providers.call_llm(ai, prompt)
        return {"answer": text,
                "citations": [{"lit_id": r["lit_id"], "title": r["title"],
                               "snippet": r["snippet"]} for r in results],
                "provider": ai["provider"], "model": ai["model"],
                "degraded": False, "mode": mode,
                "note": "模型回答仅依据给定片段；引用编号见方括号。"}
    except Exception as e:
        # 接口挂了就退回检索摘要，**但不假装成功**
        msg = ai_providers._scrub(f"{type(e).__name__}: {e}")
        lines = [f"AI 接口调用失败（{msg}），已退回本地检索摘要。",
                 f"检索到 {len(results)} 篇相关文献，主要内容摘要如下："]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r['lit_id']}] {r['title']} {r['snippet']}")
        lines.append("⚠️ 以上为**本地 FTS5 检索命中片段**，**不是大模型生成的回答**。")
        return {"answer": "\n".join(lines),
                "citations": [{"lit_id": r["lit_id"], "title": r["title"],
                               "snippet": r["snippet"]} for r in results],
                "provider": "local-fts5", "degraded": True, "mode": mode,
                "note": f"AI 接口调用失败：{msg}"}

# ── P3 体检 ─────────────────────────────────────────────────────────────
def health_detail(conn: sqlite3.Connection) -> dict:
    """完整信息：库路径、源路径、各表计数、布局版本、依赖版本、磁盘占用、上次迁移时间。"""
    import shutil
    import importlib
    cfg = get_config()
    from .db import get_meta
    meta = get_meta(conn)
    counts = {t: count(conn, t) for t in (
        "literatures", "images", "tags", "lit_tags", "img_tags", "raw_json",
        "docs_fts", "graph_nodes", "graph_edges", "staging", "import_log",
        "users", "sessions", "favorites", "access_log")}
    layout = conn.execute(
        "SELECT * FROM graph_layout ORDER BY created_at DESC LIMIT 1").fetchone()
    deps = {}
    for m in ("fastapi", "uvicorn", "networkx", "pyvis", "openpyxl", "matplotlib"):
        try:
            mod = importlib.import_module(m)
            deps[m] = getattr(mod, "__version__", "unknown")
        except ImportError:
            deps[m] = "MISSING"
    try:
        du = shutil.disk_usage(str(cfg.db_path.parent))
        disk = {"total_bytes": du.total, "used_bytes": du.used, "free_bytes": du.free}
    except Exception:
        disk = {}
    db_size = cfg.db_path.stat().st_size if cfg.db_path.exists() else 0
    return {
        "ok": True,
        "db": str(cfg.db_path),
        "db_size_bytes": db_size,
        "source_root": str(cfg.source_root),
        "schema_version": meta.get("schema_version"),
        "migrated_at": meta.get("migrated_at"),
        "counts": counts,
        "layout": dict(layout) if layout else None,
        "dependencies": deps,
        "disk": disk,
        "auth_enabled": cfg.auth_enabled,
        "ai_configured": bool((cfg.ai_cfg or {}).get("api_key")),
        "checked_at": now_str(),
    }


def diagnose(conn: sqlite3.Connection) -> list[dict]:
    """一组**可执行的**体检项：每项带 ok / detail / fix。"""
    from pathlib import Path as _P
    from .db import get_meta
    from .registry import VARIANT_FULL, columns, kind
    from .templates import build_template_bytes
    cfg = get_config()
    meta = get_meta(conn)
    items: list[dict] = []

    # 1. 库是否存在
    ok_db = cfg.db_path.exists()
    items.append({
        "id": "db_exists", "name": "数据库文件存在",
        "ok": ok_db,
        "detail": (f"{cfg.db_path}（{cfg.db_path.stat().st_size} 字节）" if ok_db
                   else f"找不到 {cfg.db_path}"),
        "fix": "" if ok_db else "先跑 python -m lzkg migrate（会从源登记表重建库）。"})

    # 2. 源目录是否可达
    ok_src = cfg.source_root.is_dir()
    items.append({
        "id": "source_reachable", "name": "源数据目录可达",
        "ok": ok_src,
        "detail": (str(cfg.source_root) if ok_src else f"目录不存在：{cfg.source_root}"),
        "fix": "" if ok_src else "python -m lzkg set-source <04_专题研究 路径>"})

    # 3. 登记表指针列是否都指向存在的 MD
    # ★ `md_path` 存的是**相对 04_专题研究 的上级目录**的路径
    #   （形如 `04_专题研究/03_文献全文/xxx.md`），而 `cfg.source_root`
    #   本身就是 `04_专题研究` → 直接拼会多一层「04_专题研究」。
    #   所以这里**两种基准都试**：先按上级目录拼，再按 source_root 拼。
    bad = []
    total_ptr = 0
    bases = [cfg.source_root.parent, cfg.source_root]
    for r in conn.execute("SELECT lit_id, md_path FROM literatures WHERE md_path<>''"):
        total_ptr += 1
        rel = (r["md_path"] or "").replace("\\", "/").lstrip("/")
        if not any((b / rel).exists() for b in bases):
            bad.append(f"{r['lit_id']} → {r['md_path']}")
    items.append({
        "id": "pointer_md", "name": "登记表指针列指向的 MD 都存在",
        "ok": not bad,
        "detail": (f"指针 {total_ptr} 条，失效 {len(bad)} 条"
                   + (f"：{'；'.join(bad[:5])}" if bad else "")),
        "fix": "" if not bad else
        "指针列（col19）必须指向 Markdown 而不是 JSON；逐条改正后重跑 migrate。"})

    # 4. FTS 是否可检索
    fts_ok, fts_detail = True, ""
    try:
        n = conn.execute("SELECT COUNT(*) c FROM docs_fts").fetchone()["c"]
        hit = conn.execute(
            "SELECT COUNT(*) c FROM docs_fts WHERE docs_fts MATCH ?",
            ('"良渚"',)).fetchone()["c"]
        fts_detail = f"docs_fts {n} 行；检索「良渚」命中 {hit} 行"
        fts_ok = n > 0
    except sqlite3.Error as e:
        fts_ok, fts_detail = False, f"检索失败：{e}"
    items.append({
        "id": "fts_searchable", "name": "全文索引可检索",
        "ok": fts_ok, "detail": fts_detail,
        "fix": "" if fts_ok else "重跑 python -m lzkg migrate 以重建 FTS5 索引。"})

    # 5. 布局坐标是否齐全
    n_node = conn.execute("SELECT COUNT(*) n FROM graph_nodes").fetchone()["n"]
    n_pos = conn.execute(
        "SELECT COUNT(*) n FROM graph_nodes WHERE x IS NOT NULL AND y IS NOT NULL"
    ).fetchone()["n"]
    lay = conn.execute(
        "SELECT * FROM graph_layout ORDER BY created_at DESC LIMIT 1").fetchone()
    items.append({
        "id": "layout_complete", "name": "图谱布局坐标齐全",
        "ok": n_node > 0 and n_pos == n_node,
        "detail": (f"{n_pos}/{n_node} 个节点有坐标"
                   + (f"；布局版本 {lay['version']}（{lay['created_at']}）" if lay else "")),
        "fix": "" if (n_node > 0 and n_pos == n_node)
        else "python -m lzkg rebuild-layout --seed 42"})

    # 6. 模板与登记表列是否一致
    import openpyxl
    mism = []
    for t in ("literatures", "images"):
        try:
            data = build_template_bytes(t, VARIANT_FULL)
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
            try:
                ws = wb.worksheets[0]
                rows = list(ws.iter_rows(min_row=4, max_row=4, values_only=True))
            finally:
                wb.close()
            got = [str(v).strip() for v in (rows[0] if rows else []) if v]
            want = [c["name"] for c in columns(t, VARIANT_FULL)]
            if got != want:
                mism.append(f"{t}: 模板 {len(got)} 列 ≠ 列定义 {len(want)} 列")
        except Exception as e:
            mism.append(f"{t}: 模板生成失败 {type(e).__name__}")
    items.append({
        "id": "template_columns", "name": "模板与登记表列定义一致",
        "ok": not mism, "detail": "；".join(mism) if mism else "文献 34 列 / 图像 26 列，模板与列定义一致",
        "fix": "" if not mism else "以 lzkg/schema/registry_columns.json 为准修列定义。"})

    # 7. 迁移时间
    items.append({
        "id": "migrated_at", "name": "数据迁移时间已记录",
        "ok": bool(meta.get("migrated_at")),
        "detail": str(meta.get("migrated_at") or "无"),
        "fix": "" if meta.get("migrated_at") else "跑 python -m lzkg migrate"})

    return items
