# -*- coding: utf-8 -*-
"""下载离线底图数据到本地（一次性联网，之后完全离线）。

★ 数据源
  Natural Earth（北美制图信息协会 NACIS 维护，**公共域 Public Domain**，
  免注册、免署名、学术与商用皆可）。直链与镜像：
    官方  https://naturalearthdata.com/downloads/110m-physical.html
    S3    https://naturalearth.s3.amazonaws.com/<scale>_<cat>/<name>.zip
    GeoJSON 镜像（免装 geopandas/fiona，纯 JSON + shapely 即可读）
           https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/<name>.geojson

★ 为什么存 GeoJSON 而不是 shapefile
  本机没有 geopandas / fiona / pyogrio，读 .shp 需要额外依赖；
  GeoJSON 是纯 JSON，shapely 直接能吃 → **零安装、零依赖、离线可跑**。

★ 合规（重要）
  Natural Earth 的行政边界按国际普遍认知绘制，**不能当作我国官方界线依据**。
  所以：
    · 世界/中国视图只作**位置示意**，图注固定写明数据来源与「不作界线依据」
    · 中国视图的取景范围**避开南海诸岛与西部争议区域**，不触碰边界合规问题

用法：python _tools/fetch_basemap.py
"""
from __future__ import annotations

import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "basemap"
JSDELIVR = "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/"

# (文件名, 说明, 用途)
LAYERS = [
    ("ne_110m_admin_0_countries.geojson", "世界国界 110m（概览）", "world"),
    ("ne_50m_admin_0_countries.geojson", "世界国界 50m（中等）", "world"),
    ("ne_50m_admin_1_states_provinces.geojson", "一级行政区界（省界）", "china"),
    ("ne_50m_lakes.geojson", "湖泊 50m", "region"),
    ("ne_10m_lakes.geojson", "湖泊 10m（精细）", "region"),
    ("ne_50m_rivers_lake_centerlines.geojson", "河流 50m", "region"),
    ("ne_10m_coastline.geojson", "海岸线 10m", "region"),
    # ★ 海洋面：区域层要靠它区分「海」与「陆」——
    #   没有它，放大到区域层时整片浅蓝（海），陆地是空的，看着像地图坏了。
    ("ne_50m_ocean.geojson", "海洋面 50m（区域层靠它分海陆）", "region"),
]

# Overpass 补充：钱塘江的精确几何 + 余杭区界
# ★ `(._;>;)` 是关键：relation（行政区界）本身没有几何，
#   要把它的 member way 一起输出，才能拿到边界线。
#   太湖不用 Overpass —— Natural Earth 10m 湖泊图层里已有太湖多边形，更完整。
OVERPASS = {
    "osm_qiantang_yuhang.geojson": """
[out:json][timeout:120];
(
  way["waterway"="river"]["name"~"钱塘江|Qiantang"];
  relation["boundary"="administrative"]["admin_level"="8"]["name"~"余杭"];
);
(._;>;);
out geom;
""",
}


def fetch(url: str, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=120,
                             headers={"User-Agent": "lzzkg-basemap/1.0"})
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
    raise last


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "Natural Earth (public domain) via jsDelivr",
                "licence": "Public Domain - free for academic and commercial use, "
                           "no attribution required (attribution appreciated)",
                "layers": []}

    for name, desc, use in LAYERS:
        dest = DEST / name
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"[skip] {name} ({dest.stat().st_size//1024} KB)")
        else:
            try:
                data = fetch(JSDELIVR + name)
                dest.write_bytes(data)
                print(f"[ok]   {name}  {len(data)//1024} KB")
            except Exception as e:
                print(f"[FAIL] {name}: {e}")
                continue
        manifest["layers"].append({"file": name, "desc": desc, "use": use,
                                    "bytes": dest.stat().st_size if dest.exists() else 0})

    # Overpass 要素 → 自己转 GeoJSON
    for name, q in OVERPASS.items():
        dest = DEST / name
        try:
            if dest.exists() and dest.stat().st_size > 1000:
                print(f"[skip] {name}")
            else:
                r = requests.post("https://overpass-api.de/api/interpreter",
                                  data={"data": q}, timeout=240,
                                  headers={"User-Agent": "lzzkg-basemap/1.0"})
                r.raise_for_status()
                els = r.json().get("elements", [])

                # relation（行政区界）本身没有几何，几何在它的 member way 上。
                # 要把父 relation 的属性传给 member way，否则画出来认不出是余杭区界。
                rel_attr: dict[int, dict] = {}
                for e in els:
                    if e.get("type") == "relation":
                        t = dict(e.get("tags") or {})
                        for m in e.get("members", []) or []:
                            ref = m.get("ref")
                            if ref is not None:
                                rel_attr.setdefault(ref, {}).update(t)

                feats = []
                for e in els:
                    geom = e.get("geometry")
                    if not geom:
                        continue
                    coords = [[p["lon"], p["lat"]] for p in geom]
                    tags = dict(e.get("tags") or {})
                    if e.get("id") in rel_attr:
                        inherited = rel_attr[e["id"]]
                        for k, v in inherited.items():
                            tags.setdefault(k, v)
                        tags["_member_of"] = inherited.get("name", "")
                    tags["_overpass_type"] = e.get("type")
                    if e.get("type") == "relation":
                        feats.append({"type": "Feature", "properties": tags,
                                      "geometry": {"type": "MultiLineString",
                                                   "coordinates": [coords]}})
                    elif geom[0] == geom[-1] and len(coords) > 3:
                        feats.append({"type": "Feature", "properties": tags,
                                      "geometry": {"type": "Polygon",
                                                   "coordinates": [coords]}})
                    else:
                        feats.append({"type": "Feature", "properties": tags,
                                      "geometry": {"type": "LineString",
                                                   "coordinates": coords}})
                dest.write_text(json.dumps(
                    {"type": "FeatureCollection", "features": feats},
                    ensure_ascii=False), encoding="utf-8")
                print(f"[ok]   {name}  {len(feats)} features")
            manifest["layers"].append({
                "file": name, "desc": "Overpass/OSM：钱塘江水系、余杭区界",
                "use": "region", "bytes": dest.stat().st_size})
        except Exception as e:
            print(f"[FAIL] {name}: {e}")

    (DEST / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p.stat().st_size for p in DEST.glob("*.geojson"))
    print(f"\n底图就位：{DEST}  合计 {total//1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
