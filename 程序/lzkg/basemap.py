# -*- coding: utf-8 -*-
"""离线底图渲染：本地 GeoJSON → matplotlib 图。**零联网、零额外安装**。

★ 数据从哪来
  `data/basemap/*.geojson`，由 `_tools/fetch_basemap.py` 一次性下载到本地。
  Natural Earth（公共域 Public Domain，免注册免署名）+ Overpass/OSM 补充要素。

★ 为什么不用 geopandas
  本机没有 geopandas/fiona/pyogrio。读 GeoJSON 只要 json + shapely（本机已有），
  画图用 matplotlib 自带的 PolyCollection/LineCollection —— **零安装**。

★ 三级视图（一张图讲清「世界 → 中国 → 遗址群」的嵌套关系）
  · world   : NE 110m 世界国界，标出良渚在世界的位置
  · china   : NE 50m **省级**拼图（中国 31 省 + 台湾），标出浙江与良渚
  · region  : 良渚区域（太湖 / 钱塘江 / 海岸线 / 余杭区界）+ 文献范围框 + 采样点

★ 合规（重要，别改坏）
  Natural Earth 的行政边界按国际普遍认知绘制，**不能当我国官方界线依据**。
  所以中国视图**刻意不画 admin_0 的国界多边形**（那条边界带争议处理），
  只用「31 个省 + 台湾」的省级拼图表达国土轮廓 —— 既标准又不触碰边界合规。
  所有视图图注固定写明数据来源与「仅作位置示意，不作界线依据」。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from matplotlib.collections import LineCollection, PolyCollection
from matplotlib import patheffects as pe
from shapely.geometry import LineString, MultiPolygon, Polygon, shape

ROOT = Path(__file__).resolve().parent.parent
BASEMAP_DIR = ROOT / "data" / "basemap"

# 配色（与站点配色一致：品牌蓝 #4C78A8，强调红 #C0392B）
C_LAND = "#F4F6F8"
C_EDGE = "#C2CBD3"
C_EDGE_STRONG = "#8A97A3"
C_WATER = "#DCEAF4"
C_RIVER = "#9CC3DE"
C_ADMIN = "#AAB6C0"
C_HL = "#F6D9A0"          # 高亮区（浙江省）
C_MARK = "#C0392B"

_cache: dict = {}


# ── 读取 ─────────────────────────────────────────────────────────────
def _fc(name: str) -> dict:
    if name in _cache:
        return _cache[name]
    p = BASEMAP_DIR / name
    if not p.exists():
        _cache[name] = {"type": "FeatureCollection", "features": []}
        return _cache[name]
    d = json.loads(p.read_text(encoding="utf-8"))
    _cache[name] = d
    return d


def _polys(name: str):
    """返回 (多边形环列表, 属性列表)——MultiPolygon 展开成多个环。"""
    rings, props = [], []
    for f in _fc(name).get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            rings.append(g["coordinates"][0])
            props.append(f.get("properties", {}))
        elif g.get("type") == "MultiPolygon":
            for poly in g["coordinates"]:
                rings.append(poly[0])
                props.append(f.get("properties", {}))
    return rings, props


def _lines(name: str):
    segs, props = [], []
    for f in _fc(name).get("features", []):
        g = f.get("geometry") or {}
        t = g.get("type")
        if t == "LineString":
            segs.append(g["coordinates"])
            props.append(f.get("properties", {}))
        elif t == "MultiLineString":
            for ln in g["coordinates"]:
                segs.append(ln)
                props.append(f.get("properties", {}))
        elif t == "Polygon":
            # relation 型水体用 polygon 表达时，取环当线画
            for ring in g["coordinates"]:
                segs.append(ring)
                props.append(f.get("properties", {}))
    return segs, props


def _clip_bbox(name: str, bbox):
    """按 bbox 裁剪图层（先裁再画，避免大图拖慢）。"""
    from shapely.geometry import box
    if bbox is None:
        return _polys(name)
    clip = box(*bbox)
    out_r, out_p = [], []
    for f in _fc(name).get("features", []):
        try:
            geom = shape(f["geometry"])
        except Exception:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        inter = geom.intersection(clip)
        if inter.is_empty:
            continue
        pr = f.get("properties", {})
        if isinstance(inter, Polygon):
            out_r.append(list(inter.exterior.coords))
            out_p.append(pr)
        elif isinstance(inter, MultiPolygon):
            for poly in inter.geoms:
                out_r.append(list(poly.exterior.coords))
                out_p.append(pr)
        elif isinstance(inter, LineString):
            out_r.append(list(inter.coords))
            out_p.append(pr)
    return out_r, out_p


# ── 三级视图 ─────────────────────────────────────────────────────────
def draw_world(ax, hl: tuple[float, float] | None = None) -> None:
    """世界视图：110m 国界。hl = 要高亮的点 (lon, lat)。"""
    rings, _ = _polys("ne_110m_admin_0_countries.geojson")
    ax.add_collection(PolyCollection(rings, facecolors=C_LAND,
                                     edgecolors=C_EDGE, linewidths=0.4, zorder=1))
    ax.set_xlim(-180, 180)
    ax.set_ylim(-60, 85)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(C_EDGE)
    if hl:
        lon, lat = hl
        ax.plot(lon, lat, marker="*", ms=13, color=C_MARK,
                mec="white", mew=0.8, zorder=5)
        ax.annotate("良渚古城遗址", (lon, lat), fontsize=9, color=C_MARK,
                    xytext=(10, 6), textcoords="offset points", zorder=6)


def draw_china(ax, hl: tuple[float, float] | None = None) -> None:
    """中国视图：**省级拼图**（31 省 + 台湾），不画 admin_0 国界 → 不涉边界争议。

    ★ 为什么台湾要单独补
      Natural Earth 的 admin_1（中国一级行政区）里**根本没有台湾要素**，
      台湾只作为 admin_0 的独立要素 TWN 存在。
      若直接用 admin_1 画，国土版图会缺一块 —— 这是底线问题。
      所以从 admin_0 取 TWN 的多边形补进拼图，**与大陆同色、同边线、
      不单独配色、不单独标注** —— 拼出来就是完整的中国版图。
      （南海诸岛等区域不取，避免触碰边界合规问题。）

    ★ 为什么不用 admin_0 的 CHN 多边形
      那条国界多边形带国际通行的争议处理，**不能当作我国官方界线依据**；
      只用「31 个省 + 台湾」的省级拼图，才是国内通行的示意画法。
    """
    rings, props = _polys("ne_50m_admin_1_states_provinces.geojson")
    cn_rings, cn_hl = [], []
    for ring, pr in zip(rings, props):
        if str(pr.get("adm0_a3", "")).upper() != "CHN":
            continue
        cn_rings.append(ring)
        cn_hl.append(str(pr.get("name_zh", "") or "").startswith("浙江"))

    # 台湾：并入拼图（同色同边线，不区分）
    tw_rings, tw_props = _polys("ne_50m_admin_0_countries.geojson")
    for ring, pr in zip(tw_rings, tw_props):
        if str(pr.get("ADM0_A3", "")).upper() == "TWN":
            cn_rings.append(ring)
            cn_hl.append(False)

    if cn_rings:
        ax.add_collection(PolyCollection(
            [r for r, h in zip(cn_rings, cn_hl) if not h],
            facecolors=C_LAND, edgecolors=C_EDGE, linewidths=0.5, zorder=1))
        ax.add_collection(PolyCollection(
            [r for r, h in zip(cn_rings, cn_hl) if h],
            facecolors=C_HL, edgecolors=C_EDGE_STRONG, linewidths=0.6, zorder=2))
    ax.set_xlim(73.0, 135.6)
    ax.set_ylim(17.7, 54.2)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(C_EDGE)
    if hl:
        lon, lat = hl
        ax.plot(lon, lat, marker="*", ms=12, color=C_MARK,
                mec="white", mew=0.8, zorder=5)
        ax.annotate("良渚古城遗址", (lon, lat), fontsize=9, color=C_MARK,
                    xytext=(9, 5), textcoords="offset points", zorder=6)


def draw_region(ax, bbox=(119.78, 30.08, 120.62, 31.42)) -> None:
    """良渚区域视图：湖泊 / 河流 / 海岸线 / 余杭区界。

    ★ bbox 必须**含太湖**：良渚在太湖东南，两者相距约 25 km。
      只按遗址群范围框取景会把太湖整片裁掉 —— 那样「遗址紧邻太湖」这个
      最重要的空间关系就画不出来了。
    """
    x0, y0, x1, y1 = bbox

    # 湖泊（10m，含太湖）
    lake_r, lake_p = _clip_bbox("ne_10m_lakes.geojson", bbox)
    if lake_r:
        ax.add_collection(PolyCollection(lake_r, facecolors=C_WATER,
                                         edgecolors="#BBD6EA",
                                         linewidths=0.4, zorder=2))
        # 给大水体加名字（加白色描边，压在标记上也能读）
        for ring, pr in zip(lake_r, lake_p):
            nm = str(pr.get("name_zh") or pr.get("name") or "")
            if "太湖" in nm or "Taihu" in nm:
                xs = [c[0] for c in ring]
                ys = [c[1] for c in ring]
                # 注记放湖心偏西南，避开湖心代表点
                cx, cy = min(xs) + (max(xs) - min(xs)) * 0.42, \
                    min(ys) + (max(ys) - min(ys)) * 0.55
                ax.text(cx, cy, "太湖", fontsize=11, color="#4C7EA0",
                        ha="center", style="italic", zorder=10,
                        path_effects=[pe.withStroke(linewidth=2.6,
                                                    foreground="white")])

    # 海岸线
    coast, _ = _clip_bbox("ne_10m_coastline.geojson", bbox)
    if coast:
        ax.add_collection(LineCollection(coast, colors="#BBD6EA",
                                         linewidths=0.7, zorder=3))

    # 钱塘江 + 余杭区界（Overpass/OSM）
    for fname in ("osm_qiantang_yuhang.geojson", "osm_taihu_qiantang.geojson"):
        if not (BASEMAP_DIR / fname).exists():
            continue
        segs, props = _lines(fname)
        riv, adm = [], []
        for seg, pr in zip(segs, props):
            nm = str(pr.get("name", ""))
            if nm == "钱塘江":
                riv.append(seg)
            elif (pr.get("admin_level") in ("8", 8)
                  or "余杭" in nm
                  or "余杭" in str(pr.get("_member_of", ""))):
                adm.append(seg)
        if riv:
            ax.add_collection(LineCollection(riv, colors=C_RIVER,
                                             linewidths=1.8, zorder=4))
        if adm:
            ax.add_collection(LineCollection(adm, colors=C_ADMIN,
                                             linewidths=1.0, zorder=5,
                                             linestyles="--"))

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.18, linestyle="--", linewidth=0.5, zorder=0)
    ax.tick_params(labelsize=7)


def scalebar(ax, lon: float, lat: float, km: float = 5.0,
             color: str = "#333") -> None:
    """画比例尺（按当前纬度把 km 换算成经度跨度）。"""
    dlon = km / 111.32 / max(math.cos(math.radians(lat)), 0.2)
    y = ax.get_ylim()[0]
    x = ax.get_xlim()[0]
    y0 = y + (ax.get_ylim()[1] - y) * 0.06
    x0 = x + (ax.get_xlim()[1] - x) * 0.06
    ax.plot([x0, x0 + dlon], [y0, y0], color=color, lw=2.4,
            solid_capstyle="butt", zorder=8)
    ax.plot([x0, x0], [y0 - (ax.get_ylim()[1] - y) * 0.012,
                       y0 + (ax.get_ylim()[1] - y) * 0.012],
            color=color, lw=1.2, zorder=8)
    ax.plot([x0 + dlon, x0 + dlon], [y0 - (ax.get_ylim()[1] - y) * 0.012,
                                     y0 + (ax.get_ylim()[1] - y) * 0.012],
            color=color, lw=1.2, zorder=8)
    ax.text(x0 + dlon / 2, y0 + (ax.get_ylim()[1] - y) * 0.022, f"{km:g} km",
            ha="center", fontsize=7.5, color=color, zorder=8)


def north_arrow(ax, x: float = 0.93, y: float = 0.86, size: float = 0.09) -> None:
    """指北针。"""
    ax.annotate("", xy=(x, y + size), xytext=(x, y),
                xycoords="axes fraction",
                arrowprops=dict(facecolor="#333", width=2.2, headwidth=7),
                zorder=9)
    ax.text(x, y + size + 0.012, "N", transform=ax.transAxes, ha="center",
            fontsize=8.5, color="#333", zorder=9)


BASEMAP_NOTE = ("底图：Natural Earth（公共域）＋ Overpass/OSM；"
                "仅作位置示意，不作界线依据")
