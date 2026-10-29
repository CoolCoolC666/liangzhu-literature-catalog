# -*- coding: utf-8 -*-
"""生成底图的**精简版** `data/basemap_lite/`（给对接包用）。

★ 为什么要有精简版
  完整底图 22 MB，其中区域图层（10m 湖泊 / 10m 海岸线）占了 15 MB，
  而它们真正用到的只有良渚周边那一小块。
  打包时带上完整底图会让包凭空涨 20 MB —— 对接手的人没有任何价值。
  所以：
    · **区域图层**（湖泊、海岸线）→ 裁到良渚区域框内，坐标压到 4 位小数（约 11 m）
    · **全球图层**（世界国界、省界）→ 原样保留（世界/中国视图要用）
  结果：完整版 22 MB → 精简版约 2 MB。

★ 不改代码
  包内目录名仍是 `data/basemap/`，程序读路径不变 —— 只是内容换成了精简版。

用法：python _tools/slim_basemap.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "basemap"
DST = ROOT / "data" / "basemap_lite"

# 良渚区域视野（与 basemap.draw_region 的默认 bbox 对齐，留一点余量）
REGION_BBOX = (119.5, 29.9, 121.0, 31.6)
# 台湾岛视野（50m countries 里只用到 TWN 这一个要素）
TAIWAN_BBOX = (119.8, 21.3, 122.6, 25.8)

# 文件名 → (裁剪框 or None, 坐标保留小数位)
#   None = 不裁剪，只压精度
CLIP = {
    "ne_10m_lakes.geojson": (REGION_BBOX, 4),
    "ne_10m_coastline.geojson": (REGION_BBOX, 4),
    "ne_50m_admin_0_countries.geojson": (TAIWAN_BBOX, 4),
    "ne_50m_admin_1_states_provinces.geojson": (None, None),   # 原样（重序列化反而更大）
    "ne_110m_admin_0_countries.geojson": (None, None),   # 原样（重新序列化反而更大）
    "ne_50m_ocean.geojson": (None, 4),                   # 海洋面（区域层靠它分海陆）
    "osm_qiantang_yuhang.geojson": (None, 5),
}
# 完全用不到的图层，不进精简版
DROP = {"ne_50m_lakes.geojson", "ne_50m_rivers_lake_centerlines.geojson",
        "osm_taihu_qiantang.geojson"}


def clip_bbox(lon, lat, bbox):
    x0, y0, x1, y1 = bbox
    return x0 <= lon <= x1 and y0 <= lat <= y1


def round_coords(obj, nd=4):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], (int, float)):
            return [round(obj[0], nd), round(obj[1], nd)]
        return [round_coords(o, nd) for o in obj]
    return obj


def clip_feature(geom, bbox):
    """把 feature 的坐标裁到指定框内（按点筛，不做几何求交，够用且快）。"""
    t = geom.get("type")
    if t == "Polygon":
        rings = []
        for ring in geom["coordinates"]:
            kept = [[p[0], p[1]] for p in ring
                    if clip_bbox(p[0], p[1], bbox)]
            if len(kept) >= 3:
                rings.append(kept)
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}
    if t == "MultiPolygon":
        polys = []
        for poly in geom["coordinates"]:
            rings = []
            for ring in poly:
                kept = [[p[0], p[1]] for p in ring
                        if clip_bbox(p[0], p[1], bbox)]
                if len(kept) >= 3:
                    rings.append(kept)
            if rings:
                polys.append(rings)
        if not polys:
            return None
        return {"type": "MultiPolygon", "coordinates": polys}
    if t == "LineString":
        kept = [[p[0], p[1]] for p in geom["coordinates"]
                if clip_bbox(p[0], p[1], bbox)]
        if len(kept) < 2:
            return None
        return {"type": "LineString", "coordinates": kept}
    if t == "MultiLineString":
        lines = []
        for ln in geom["coordinates"]:
            kept = [[p[0], p[1]] for p in ln if clip_bbox(p[0], p[1], bbox)]
            if len(kept) >= 2:
                lines.append(kept)
        if not lines:
            return None
        return {"type": "MultiLineString", "coordinates": lines}
    return None


def main() -> int:
    if not SRC.exists():
        print(f"缺 {SRC}", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    total_src = total_dst = 0
    report = []
    for p in sorted(SRC.glob("*.geojson")):
        total_src += p.stat().st_size
        if p.name in DROP:
            print(f"{p.name:<42} {'剔除（用不到）':<28} "
                  f"{p.stat().st_size//1024:>6} KB →        0 KB")
            continue
        out = DST / p.name
        bbox, nd = CLIP.get(p.name, (None, 5))
        if nd is None:
            # 原样复制：重新序列化 JSON 反而可能更大
            out.write_bytes(p.read_bytes())
            note = "原样"
        else:
            d = json.loads(p.read_text(encoding="utf-8"))
            feats = []
            for f in d.get("features", []):
                g = f.get("geometry") or {}
                if bbox:
                    ng = clip_feature(g, bbox)
                else:
                    ng = round_coords(g, nd) if g else None
                if ng:
                    feats.append({"type": "Feature",
                                  "properties": f.get("properties", {}),
                                  "geometry": ng})
            out.write_text(json.dumps({"type": "FeatureCollection",
                                       "features": feats},
                                      ensure_ascii=False), encoding="utf-8")
            note = f"裁剪+{nd}位" if bbox else f"压到{nd}位"
        total_dst += out.stat().st_size
        print(f"{p.name:<42} {note:<28} "
              f"{p.stat().st_size//1024:>6} KB → {out.stat().st_size//1024:>6} KB")
    m = SRC / "manifest.json"
    if m.exists():
        (DST / "manifest.json").write_text(m.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n完整 {total_src//1024} KB → 精简 {total_dst//1024} KB "
          f"（省 {100 - total_dst*100//max(total_src,1)}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
