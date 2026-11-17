# -*- coding: utf-8 -*-
"""地图素材页（BP18）判据。

★ 纪律：每条判据都指到一个**实测数字或集合关系**；
  与文件有关的数字一律**现算对账**（重新算 sha256 / 重新量宽高），
  不拿 manifest 自己写的值当"真值"。

用法：
    python tests/test_maps.py --api      # 需要服务在跑
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

JOB = ROOT.parent
MAPS_DIR = ROOT / "data" / "media" / "maps"
MANIFEST = MAPS_DIR / "manifest.json"
SRC_CSV = JOB / "05-相关资料" / "地图" / "下载地图_清单.csv"
SRC_DIR = JOB / "05-相关资料" / "地图" / "下载地图"

_results: list[tuple[bool, str, str]] = []

# ── 实测真值（写死；变了要改这里并说明）──
EXP_TOTAL = 89          # 组员交付并下载成功的素材总数
EXP_INCLUDED = 27       # 按收录口径筛出入站的幅数
EXP_DIMS = {"城址格局": 8, "水利系统": 5, "文化分布": 5, "中心遗址": 5,
             "局部遗址": 2, "复原想象": 1, "文化谱系": 1}
# 目视复核判定为「实物展板翻拍」的两幅（见可用性检验报告）
EXP_REVOKED = {"LZ-80", "LZ-52"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(("  OK  " if ok else "  FAIL") + f" {name}" + (f"  [{detail}]" if detail else ""))
    return bool(ok)


def api_get(path: str):
    url = "http://127.0.0.1:8771" + path
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def api_get_raw(path: str):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8771" + path, timeout=60) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", b""
    except Exception as e:
        return None, str(e), b""


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def test_data():
    print("\n── 素材本体与清单（现算对账）──")
    check("判据1 manifest 存在", MANIFEST.exists(), str(MANIFEST))
    if not MANIFEST.exists():
        return None
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = m.get("items") or []

    check("判据2 总素材数 = 89（组员交付实测）", m.get("total_all") == EXP_TOTAL,
          str(m.get("total_all")))
    check("判据3 收录幅数 = 27", m.get("included") == EXP_INCLUDED, str(m.get("included")))
    check("判据4 排除幅数 = 89 − 27 = 62", m.get("excluded") == EXP_TOTAL - EXP_INCLUDED,
          str(m.get("excluded")))
    check("判据5 items 条数 = included", len(items) == m.get("included"), str(len(items)))
    dims = m.get("dims") or {}
    check("判据6 维度计数与实测一致", dims == EXP_DIMS, str(dims))
    check("判据7 维度计数之和 = 收录幅数", sum(dims.values()) == len(items),
          f"{sum(dims.values())} vs {len(items)}")
    check("判据8 收录口径写进 manifest（可复算）", len(m.get("rules") or []) >= 3, "")

    # 与组员交付的下载清单对账（真值来自原始 CSV，不是 manifest 自述）
    rows = []
    if SRC_CSV.exists():
        with SRC_CSV.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    check("判据9 组员交付清单 = 89 行", len(rows) == EXP_TOTAL, str(len(rows)))
    csv_ids = {r["id"] for r in rows}
    check("判据10 收录幅全部来自交付清单", {i["id"] for i in items} <= csv_ids,
          str(sorted({i["id"] for i in items} - csv_ids)))
    check("判据11 收录幅不重复", len({i["id"] for i in items}) == len(items), "")

    # 逐幅：文件在、sha256 现算一致、宽高现量一致
    missing, bad_sha, bad_size = [], [], []
    for it in items:
        p = MAPS_DIR / it["file"]
        if not p.exists():
            missing.append(it["id"])
            continue
        if sha16(p) != it.get("sha256_16"):
            bad_sha.append(it["id"])
        try:
            from PIL import Image
            with Image.open(p) as im:
                w, h = im.size
            if (w, h) != (int(it["w"]), int(it["h"])):
                bad_size.append((it["id"], (w, h), (it["w"], it["h"])))
        except Exception as e:
            bad_size.append((it["id"], str(e), ""))
    check("判据12 收录幅的图文件全部落盘", not missing, str(missing))
    check("判据13 逐幅 sha256 现算 = manifest 值", not bad_sha, str(bad_sha))
    check("判据14 逐幅实测宽高 = manifest 值（尺寸必须等于实测真值）",
          not bad_size, str(bad_size[:3]))

    # 与原始下载件对账：入库图不得被压缩/改写
    by_id = {r["id"]: r for r in rows}
    bad_src = []
    for it in items:
        r = by_id.get(it["id"])
        if not r:
            continue
        op = SRC_DIR / r["file"]
        if op.exists() and sha16(op) != it.get("sha256_16"):
            bad_src.append(it["id"])
    check("判据15 入库图 = 原始下载件（同 sha256，未重编码）", not bad_src, str(bad_src))

    # 授权口径：不得混入论坛/自媒体/聚合站
    lic = sorted({i.get("license") for i in items})
    check("判据16 收录幅授权分级只含官方/学术两类", lic == ["academic", "official"], str(lic))
    check("判据17 论坛/自媒体来源一幅未进站",
          not any(k in (i.get("source") or "") for i in items
                  for k in ("论坛", "贴吧", "知乎", "自媒体", "聚合", "豆瓣", "微博")),
          "")
    # 目视回改的两幅翻拍图必须被排除
    check("判据18 翻拍图 LZ-80 / LZ-52 未收录",
          not (EXP_REVOKED & {i["id"] for i in items}), "")
    # 复原类必须带免责标注
    bad_cav = [i["id"] for i in items
               if i.get("category") == "复原想象" and not i.get("caveat")]
    check("判据19 复原想象类每幅带『非考古实景』标注", not bad_cav, str(bad_cav))
    # 每幅必须可溯源
    bad_src2 = [i["id"] for i in items
                if not (i.get("source") or "").strip()
                or not (i.get("link") or "").startswith("http")]
    check("判据20 每幅都有来源与原文链接", not bad_src2, str(bad_src2))
    return m


def test_frontend(m):
    print("\n── 前端与接线 ──")
    js = ROOT / "lzkg" / "web" / "static" / "js" / "maps.js"
    check("判据21 maps.js 存在", js.exists(), str(js))
    src = js.read_text(encoding="utf-8") if js.exists() else ""
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    check("判据22 maps.js 通过 node --check（ES5 语法门）", r.returncode == 0,
          (r.stderr or "").strip()[:120])
    check("判据23 maps.js 不含外链（零外链约束）",
          not re.search(r'(src|href)\s*=\s*["\']https?://', src)
          and not re.search(r'https?://(?!127\.0\.0\.1)', src.replace("https://127.0.0.1", "")),
          "")
    check("判据24 maps.js 只用同源端点 /api/maps 与 /media/map/",
          "/api/maps" in src and "/media/map/" in src, "")
    check("判据25 大图查看复用站点既有灯箱（不新造轮播）",
          "LB_GROUPS" in src and "lbOpen" in src, "")
    check("判据26 暴露全局 renderMaps（供路由调用）", "global.renderMaps" in src, "")
    check("判据27 数量行拆成可翻译片段（无 {n} 占位符失配）",
          "{n}" not in src, "")

    app = (ROOT / "lzkg" / "app.py").read_text(encoding="utf-8")
    check("判据28 /js 白名单登记 maps.js", '"maps.js"' in app, "")
    check("判据29 /api/maps 端点存在", '@app.get("/api/maps")' in app, "")
    check("判据30 /media/map/{filename} 端点存在", '/media/map/{filename}' in app, "")
    check("判据31 图片端点做了目录穿越校验",
          '".." in filename' in app and "MAPS_DIR / filename" in app, "")
    check("判据32 图片端点限定 image/jpeg", 'media_type="image/jpeg"' in app, "")

    html = (ROOT / "lzkg" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    check("判据33 index.html 有「地图」页签", '<button data-tab="maps">地图</button>' in html, "")
    check("判据34 路由白名单含 maps", '"maps"' in html, "")
    check("判据35 路由挂上 renderMaps", "renderMaps()" in html, "")
    check("判据36 index.html 引用 /js/maps.js", "/js/maps.js" in html, "")

    i18n = (ROOT / "lzkg" / "web" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    for kw in ("地图", "地图素材", "全部维度", "原文链接", "收录口径：", "收录数量（幅）", "素材总数（幅）"):
        check(f"判据37 i18n 词典含「{kw}」", f'"{kw}"' in i18n, "")
    check("判据38 数据容器进 SKIP（图名/说明/来源不翻译）",
          ".maps-t" in i18n and ".maps-n" in i18n and ".maps-src" in i18n, "")

    pkg = JOB / "_tools" / "build_package.py"
    if pkg.exists():
        psrc = pkg.read_text(encoding="utf-8")
        check("判据39 组内包登记 data/media/maps", 'data/media/maps' in psrc, "")
        check("判据40 组内包必备清单含 maps/manifest.json",
              '"data/media/maps/manifest.json"' in psrc, "")
    gh = JOB / "_tools" / "make_github_release.py"
    if gh.exists():
        gsrc = gh.read_text(encoding="utf-8")
        check("判据41 公开版对地图素材有硬断言（只进组内包）",
              "media/" in gsrc and "拒绝生成发布版" in gsrc, "")
    return src


def test_api(m):
    print("\n── 端点实测（需服务在跑）──")
    if m is None:
        check("判据42 manifest 可读（API 对账前提）", False, "")
        return
    api = api_get("/api/maps")
    check("判据42 /api/maps 返回清单", isinstance(api, dict) and "items" in api, "")
    check("判据43 /api/maps 收录幅数 = manifest", api.get("included") == m.get("included"),
          str(api.get("included")))
    check("判据44 /api/maps 维度计数 = manifest", (api.get("dims") or {}) == (m.get("dims") or {}),
          "")
    check("判据45 /api/maps 逐幅带来源与链接",
          all(i.get("source") and i.get("link") for i in (api.get("items") or [])), "")

    first = (api.get("items") or [{}])[0].get("file")
    st, ct, body = api_get_raw("/media/map/" + first)
    check("判据46 /media/map 返回图片（200 + image/jpeg）",
          st == 200 and "image/jpeg" in ct and len(body) > 1000,
          f"{st} {ct} {len(body)}B")
    st2, _, _ = api_get_raw("/media/map/..%2f..%2fconfig.json")
    check("判据47 目录穿越被拒（400/404）", st2 in (400, 404), str(st2))
    js = api_get_raw("/js/maps.js")
    check("判据48 GET /js/maps.js 走白名单通道",
          js[0] == 200 and b"renderMaps" in js[2], str(js[0]))


def main() -> int:
    print("═══ 地图素材页（BP18）· 判据 ═══")
    m = test_data()
    test_frontend(m)
    if "--api" in sys.argv:
        test_api(m)
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
