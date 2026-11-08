# -*- coding: utf-8 -*-
"""良渚古城核心区卫星影像瓦片缓存（一次性下载，之后站点完全离线）。

★ 为什么需要它：项目硬约束是「零外链、禁 CDN、断网可用」，
  而卫星影像瓦片天然是在线服务 → 只能本地切片。
★ 范围：良渚古城核心区 4 km x 4 km（经度 119.982-120.018 / 纬度 30.382-30.418），
  层级 z14-z18（0.52-8.24 m/像素），约 1200 张、15-18 MB。
★ 数据源：Esri World Imagery（server.arcgisonline.com），匿名可取，无需 key。
  ★ 许可未核实 → 瓦片只放组内离线对接包，**不进公开 GitHub / 公开 Release**；
    页面须保留署名位「影像：Esri World Imagery」。
★ 断点续跑：已存在的文件直接跳过，中断后重跑即可。
"""
import json, math, os, sys, time, urllib.request, urllib.error

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "basemap_tiles")
SRC = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
BBOX = dict(lon_min=119.982, lon_max=120.018, lat_min=30.382, lat_max=30.418)
ZOOMS = [14, 15, 16, 17, 18]
UA = {"User-Agent": "Mozilla/5.0 (liangzhu-literature-catalog course project)"}


def ll2t(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return x, y


def tile_range(z):
    x0, y1 = ll2t(BBOX["lon_min"], BBOX["lat_min"], z)
    x1, y0 = ll2t(BBOX["lon_max"], BBOX["lat_max"], z)
    return x0, x1, y0, y1


def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 500:
                raise ValueError("too small: %d bytes" % len(data))
            return data
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def save_manifest(manifest):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def main():
    total = done = 0
    manifest = {"source": "Esri World Imagery (server.arcgisonline.com)", "bbox": BBOX,
                "attribution": "影像：Esri World Imagery",
                "note": "影像时相未标注；许可未核实 → 不进公开仓库/公开 Release", "levels": []}
    save_manifest(manifest)   # ★ 先落一份：即使中途断网，前端也能拿到 bbox 正常降级
    for z in ZOOMS:
        x0, x1, y0, y1 = tile_range(z)
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        got = 0
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                total += 1
                d = os.path.join(OUT, str(z), str(x))
                p = os.path.join(d, "%d.jpg" % y)
                if os.path.exists(p) and os.path.getsize(p) > 500:
                    done += 1; got += 1; continue
                try:
                    data = fetch(SRC.format(z=z, y=y, x=x))
                except Exception as e:
                    print("FAIL z%d/%d/%d %s" % (z, x, y, e), flush=True)
                    continue
                os.makedirs(d, exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
                done += 1; got += 1
                if done % 100 == 0:
                    print("  z%d %d/%d  (%d/%d)" % (z, got, nx * ny, done, total), flush=True)
        manifest["levels"].append({"z": z, "x": [x0, x1], "y": [y0, y1], "tiles": nx * ny})
        save_manifest(manifest)   # ★ 每层落一次：断点时前端拿到的仍是真实覆盖范围
        print("z%d done: %d/%d" % (z, got, nx * ny), flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("ALL DONE: %d/%d tiles" % (done, total), flush=True)


if __name__ == "__main__":
    main()
