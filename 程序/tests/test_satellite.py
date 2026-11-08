# -*- coding: utf-8 -*-
"""卫星影像底图的判据（独立文件，不塞进 test_all.py）。

方案：`02-报告/良渚文献目录站_卫星地图可行性勘察_20261108.md`

★ 纪律：判据不写「不为空」，不写永远 True 的条件。
  与覆盖范围有关的数字一律**现算对账**（用另一套实现算一遍，再比）。
  与瓦片有关的数字一律**等于磁盘实测值**（字节数、文件数、层级范围）。

★ 两种运行形态都要能过：
  ① 组内离线包（有瓦片）→ 卫星按钮可见、瓦片端点 200；
  ② 公开包（无瓦片）    → 端点 404、前端**隐藏**卫星按钮（优雅降级）。

用法：
    python tests/test_satellite.py --api      # 需要服务在跑
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = os.environ.get("LZKG_BASE", "http://127.0.0.1:8771")
PROGRAM = Path(__file__).resolve().parent.parent
TILES = PROGRAM / "data" / "basemap_tiles"
HTML = PROGRAM / "lzkg" / "web" / "static" / "index.html"
_results: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(f"  {'OK ' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def http(path: str):
    """返回 (status, body_bytes, content_type)；不抛异常。"""
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""
    except Exception as e:
        return 0, str(e).encode("utf-8"), ""


def ll2t(lon: float, lat: float, z: int) -> tuple[int, int]:
    """独立实现一遍 Web Mercator 瓦片号换算（不 import 下载脚本 → 真·对账）。"""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return x, y


def main() -> int:
    print("卫星影像底图判据")

    # ── 前端源码事实 ──
    src = HTML.read_text(encoding="utf-8")

    section("前端：底图切换")
    check("详情页有「矢量/卫星」切换",
          'id="litmap-mode"' in src and 'data-lm="sat"' in src and 'data-lm="vec"' in src)
    check("卫星按钮默认隐藏（无瓦片时不露出来）",
          'id="litmap-sat-btn" hidden' in src)
    _qpat = "tileLayer\\(\\s*[" + chr(34) + chr(39) + "]https?://"
    check("卫星层用同源相对路径（零外链）",
          '"/tiles/{z}/{x}/{y}.jpg"' in src and
          not re.search(_qpat, src),
          "tileLayer 的 url 必须是 /tiles/... 同源路径")
    check("卫星层有覆盖范围限制（bounds）",
          "bounds: [[Number(b.lat_min)" in src,
          "超出核心区的请求被 Leaflet 挡住，不去要不存在的瓦片")
    check("卫星层带署名位",
          "attribution:" in src and "Esri World Imagery" in src,
          "影像来源必须署名")
    check("无瓦片时前端走降级分支（提示而不是碎图）",
          "本包未随附卫星影像瓦片" in src)
    # ── 第二版：默认卫星 / 覆盖锁定 / 点图看大图 / 官方署名 ──
    check("有瓦片时默认就用卫星底图（本地版默认）",
          'if (inside) litmapSetMode("sat")' in src,
          "公开包无瓦片 → 仍走矢量")
    check("卫星模式把视图锁在覆盖范围内（不拖出空白）",
          "setMaxBounds" in src and "fitBounds" in src)
    check("覆盖范围外用中性深底表示（不是一片浅灰）",
          ".leaflet-container.lm-sat{background:#1b2126}" in src)
    check("点卫星图看大图：canvas 拼 z18 瓦片 → 灯箱",
          "litmapSatBig" in src and "toDataURL" in src and 'lbOpen("sat", 0)' in src)
    check("大图与图版大图查看器共用同一套灯箱",
          "LB_GROUPS.sat" in src and 'fig: "卫星影像"' in src)
    check("点坐标标记不触发大图（标记是看说明的）",
          "leaflet-marker-icon" in src)
    check("署名用 Esri 服务自带的版权行原文",
          "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community" in src,
          "World Imagery MapServer 的 copyrightText")

    # ── 清单与端点 ──
    section("后端：瓦片端点")
    st, body, ctype = http("/tiles/manifest.json")
    has_tiles = (st == 200)
    if has_tiles:
        mf = json.loads(body.decode("utf-8"))
        disk = TILES / "manifest.json"
        check("清单端点内容 == 磁盘 manifest（实测真值）",
              disk.exists() and json.loads(disk.read_text(encoding="utf-8")) == mf,
              "%d 字节" % len(body))
        bb = mf.get("bbox") or {}
        check("清单带 bbox 四角与署名",
              all(k in bb for k in ("lon_min", "lon_max", "lat_min", "lat_max"))
              and bool(mf.get("attribution")),
              "attribution=%s" % mf.get("attribution"))
        check("署名 == Esri World Imagery 服务返回的版权行（实测真值）",
              mf.get("attribution") ==
              "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community",
              mf.get("attribution") or "")

        # 每层网格数现算对账
        bad = []
        for lv in mf.get("levels", []):
            z = lv["z"]; x0, x1 = lv["x"]; y0, y1 = lv["y"]
            want = (x1 - x0 + 1) * (y1 - y0 + 1)
            if want != lv.get("tiles"):
                bad.append("z%s 记录 %s 实算 %s" % (z, lv.get("tiles"), want))
            # 独立换算：bbox 在该层的瓦片号必须落在记录的范围内
            bx0, by1 = ll2t(bb["lon_min"], bb["lat_min"], z)
            bx1, by0 = ll2t(bb["lon_max"], bb["lat_max"], z)
            if not (x0 <= bx0 and x1 >= bx1 and y0 <= by0 and y1 >= by1):
                bad.append("z%s 瓦片号范围 %s/%s 装不下 bbox 现算 %s/%s" %
                           (z, (x0, x1), (y0, y1), (bx0, bx1), (by0, by1)))
        check("每层瓦片数与范围 == 现算结果",
              not bad,
              "; ".join(bad) if bad else "层级 %s" % ",".join(str(l["z"]) for l in mf.get("levels", [])))

        # 取一张磁盘上确实存在的瓦片，比对字节数
        sample = None
        for lv in mf.get("levels", []):
            z = lv["z"]
            for x in range(lv["x"][0], lv["x"][1] + 1):
                for y in range(lv["y"][0], lv["y"][1] + 1):
                    p = TILES / str(z) / str(x) / ("%d.jpg" % y)
                    if p.exists():
                        sample = (z, x, y, p.stat().st_size)
                        break
                if sample:
                    break
            if sample:
                break
        if sample:
            z, x, y, size = sample
            st2, body2, ct2 = http("/tiles/%d/%d/%d.jpg" % (z, x, y))
            check("瓦片端点返回 == 磁盘字节数（实测真值）",
                  st2 == 200 and len(body2) == size,
                  "z%d/%d/%d 期望 %d 实际 %d" % (z, x, y, size, len(body2)))
            check("瓦片 Content-Type 是 image/jpeg",
                  "image/jpeg" in ct2, ct2)
        else:
            check("至少有一张瓦片可取", False, "磁盘上没有瓦片")

        # 越界一律 404
        cases = [
            ("层级过小 z13", "/tiles/13/%d/%d.jpg" % (ll2t(120.0, 30.4, 13)[0], ll2t(120.0, 30.4, 13)[1])),
            ("层级过大 z20", "/tiles/20/1/1.jpg"),
            ("网格越界 x", "/tiles/18/999999/1.jpg"),
            ("网格越界 y", "/tiles/18/218443/999999.jpg"),
            ("路径穿越", "/tiles/18/..%2F..%2Fapp.py/1.jpg"),
        ]
        for nm, path in cases:
            s3, _, _ = http(path)
            check("越界请求 404：" + nm, s3 == 404, "HTTP %s" % s3)
    else:
        check("无瓦片时清单端点 404（公开包形态）",
              st == 404, "HTTP %s" % st)
        check("无瓦片时前端仍能优雅降级（源码已带降级分支）",
              "本包未随附卫星影像瓦片" in src)

    # ── 汇总 ──
    ok = sum(1 for r in _results if r[0])
    bad = [r for r in _results if not r[0]]
    print("\n结果：%d 通过 / %d 失败（共 %d）" % (ok, len(bad), len(_results)))
    for _, nm, det in bad:
        print("  FAIL  %s  %s" % (nm, det))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())