# -*- coding: utf-8 -*-
"""良渚时序（BP15 · 阶段 1 样例版）判据。

★ 纪律：每条判据都指到一个**实测数字或集合关系**；
  与端点有关的数字一律**另写一套算法现算对账**（直连库重算，再比），
  不拿端点自己的返回值当"真值"。

用法：
    python tests/test_atlas.py --api      # 需要服务在跑
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_results: list[tuple[bool, str, str]] = []

# ── 实测真值（写死；变了要改这里并说明）──
EXP_BANDS = [("PRE", 1), ("EARLY_MID", 3), ("MID", 5), ("MID_LATE", 7),
             ("LATE", 117), ("SPAN_ALL", 14), ("GENERAL", 15), ("CONTEMP", 38)]
EXP_PAPERS = 200
EXP_POINTS = 339
EXP_YEAR_MIN, EXP_YEAR_MAX = 1984, 2026
EXP_REGIONS = 12
EXP_REGIONS_ON_MAP = 6


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(("  OK  " if ok else "  FAIL") + f" {name}" + (f"  [{detail}]" if detail else ""))
    return bool(ok)


def api_get(path: str):
    url = "http://127.0.0.1:8771" + path
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def db_conn():
    from lzkg.db import connect
    return connect()


def test_frontend():
    print("\n── 前端文件与接线 ──")
    js = ROOT / "lzkg" / "web" / "static" / "js" / "atlas.js"
    check("判据1 atlas.js 存在", js.exists(), str(js))
    src = js.read_text(encoding="utf-8") if js.exists() else ""
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    check("判据2 atlas.js 通过 node --check（ES5 语法门）", r.returncode == 0,
          (r.stderr or "").strip()[:120])
    check("判据3 atlas.js 暴露 window.LZKG_ATLAS", "global.LZKG_ATLAS =" in src, "")
    check("判据4 atlas.js 不含外链（http/https 引用）",
          not re.search(r'(src|href)\s*=\s*["\']https?://', src), "")
    check("判据5 atlas.js 只用同源底图（/basemap/）", '"/basemap/"' in src or "/basemap/" in src, "")
    check("判据6 atlas.js 自建 Leaflet 实例（不动统计页单例）",
          "L.map(host" in src and "mapstInit" not in src, "")
    check("判据7 atlas.js 有双轴时间轴（上=出版年 / 下=分期带）",
          "atlas-band" in src and "yBase" in src, "")

    app = (ROOT / "lzkg" / "app.py").read_text(encoding="utf-8")
    check("判据8 /js 白名单登记 atlas.js", '"atlas.js"' in app, "")

    html = (ROOT / "lzkg" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    check("判据9 index.html 引用 /js/atlas.js", "/js/atlas.js" in html, "")
    check("判据10 index.html 首个页签是良渚时序",
          re.search(r'<button data-tab="atlas"[^>]*>良渚时序</button>\s*\n\s*<button data-tab="overview"', html)
          is not None, "")
    check("判据11 默认落地页 = atlas", 'return { tab: "atlas", id: null, sub: "" }' in html, "")
    check("判据12 index.html 有时序容器 #w-atlas", 'id="w-atlas"' in html, "")
    check("判据13 路由挂上 atlas 页（render + destroy）",
          "LZKG_ATLAS.render()" in html and "LZKG_ATLAS.destroy()" in html, "")
    check("判据14 跳文献页支持分期（mapstGoFilter 带 period）",
          "if (opts.period) p.period = opts.period;" in html, "")
    check("判据15 标签筛选改用名称（tags.code 归一码在原文里不存在，按码筛命中 0）",
          "var v = t.name;" in html and "t.code || t.name" not in html, "")

    i18n = (ROOT / "lzkg" / "web" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    check("判据16 i18n 词典含 BP15 词条块", "BP15 良渚时序" in i18n, "")
    if js.exists():
        used = set(re.findall(r'\bT[F]?\("([^"]+)"\)', src))
        dict_keys = set(re.findall(r'^\s{4}"(.+?)":\s*\[', i18n, re.M))
        missing = sorted(k for k in used if k not in dict_keys)
        check("判据17 时序界面文案全部在 i18n 词典中（无漏翻）", not missing, str(missing[:4]))
        check("判据18 BP15 短词进 AMBIG（只整段命中，别翻数据）",
              '"播放": 1' in i18n and '"分区": 1' in i18n, "")

    pkg = ROOT.parent / "_tools" / "build_package.py"
    if pkg.exists():
        check("判据19 打包清单登记 atlas.js",
              "lzkg/web/static/js/atlas.js" in pkg.read_text(encoding="utf-8"), "")

    # ── BP15 交互面（静态可查的部分）──
    check("判据46 时序页有可点的分区 chip（地图点位挤在 ±1.1 km 内点不中）",
          "atlas-chips" in src and "data-reg" in src, "")
    check("判据47 底图可切矢量／卫星（本地瓦片，公开包无瓦片时整条隐藏）",
          "satManifest" in src and "litmapSatLayer" in src and 'data-am="sat"' in src, "")
    check("判据48 切卫星时整批撤掉矢量层（否则多边形盖住影像）",
          "A.baseLayers" in src and "removeLayer" in src, "")
    check("判据49 切语言会重画时序页（data-lang 监听）",
          "data-lang" in src and "paintChrome" in src, "")
    check("判据50 分期带点击绑在 g 上（点在文字上也要生效）",
          "g.addEventListener(\"click\"" in src, "")


def test_api_truth():
    print("\n── /api/atlas：与库现算对账 ──")
    res = api_get("/api/atlas")
    check("判据20 /api/atlas 返回 ok", res.get("ok") is True, "")
    d = res.get("data") or {}
    meta = d.get("meta") or {}
    bands = d.get("bands") or []
    years = d.get("years") or []
    papers = d.get("papers") or []
    m = d.get("map") or {}

    got = [(b["key"], b["n"]) for b in bands]
    check("判据21 分期带 8 档（键与篇数实测）", got == EXP_BANDS, str(got))
    check("判据22 文献总数 = 200", meta.get("papers") == EXP_PAPERS, str(meta.get("papers")))
    check("判据23 展开点数 = 339", meta.get("points") == EXP_POINTS, str(meta.get("points")))
    check("判据24 年份跨度 = 1984–2026",
          meta.get("year_min") == EXP_YEAR_MIN and meta.get("year_max") == EXP_YEAR_MAX,
          f"{meta.get('year_min')}-{meta.get('year_max')}")

    # 年份轴：逐年连续、cum 单调
    ys = [y["year"] for y in years]
    check("判据25 年份轴逐年连续（1984–2026 共 43 格）",
          ys == list(range(EXP_YEAR_MIN, EXP_YEAR_MAX + 1)), f"{len(ys)} 格")
    cums = [y["cum"] for y in years]
    check("判据26 累计篇数单调不减",
          all(cums[i] <= cums[i + 1] for i in range(len(cums) - 1)), "")

    conn = db_conn()
    try:
        # 另算一套：直连库重新数
        db_rows = list(conn.execute(
            "SELECT lit_id, title, year, period_tags, region_tags, topic_tags "
            "FROM literatures"))
        db_ids = set(r["lit_id"] for r in db_rows)
        img = {r["lit_id"]: r["n"] for r in conn.execute(
            "SELECT lit_id, COUNT(*) n FROM images GROUP BY lit_id")}
        year_re = re.compile(r"((?:19|20)\d{2})")
        db_year = {}
        db_no_year = 0
        for r in db_rows:
            m2 = year_re.search(r["year"] or "")
            if m2:
                y = int(m2.group(1))
                db_year[y] = db_year.get(y, 0) + 1
            else:
                db_no_year += 1

        api_ids = set(p["lit_id"] for p in papers)
        check("判据27 端点覆盖库里全部 200 篇文献", api_ids == db_ids,
              f"缺 {len(db_ids - api_ids)} 篇 / 多 {len(api_ids - db_ids)} 篇")
        check("判据28 文献不重复", len(papers) == len(api_ids), str(len(papers)))

        # 每年篇数 = 库现算
        bad_year = []
        for y in years:
            if y["n"] != db_year.get(y["year"], 0):
                bad_year.append((y["year"], y["n"], db_year.get(y["year"], 0)))
        check("判据29 每年文献数 = 库现算（逐格对账）", not bad_year, str(bad_year[:5]))
        check("判据30 无出版年文献数 = 库现算", meta.get("no_year") == db_no_year,
              f"{meta.get('no_year')} vs {db_no_year}")
        check("判据31 年份轴篇数之和 = 有年份文献数",
              sum(y["n"] for y in years) == EXP_PAPERS - db_no_year,
              str(sum(y["n"] for y in years)))

        # 分期带 → 跳文献页的筛选值必须恰好命中整档（LIKE 包含匹配）
        bad_fv = []
        for b in bands:
            fv = b.get("filter_value") or ""
            c = conn.execute("SELECT COUNT(*) c FROM literatures WHERE period_tags LIKE ?",
                             ("%" + fv + "%",)).fetchone()["c"]
            if c != b["n"]:
                bad_fv.append((b["key"], fv, c, b["n"]))
        check("判据32 分期带的筛选值恰好命中整档篇数", not bad_fv, str(bad_fv[:5]))

        # 每篇文献的分期原文 / 分区原文 / 图版数 = 库实数
        bad_raw, bad_reg, bad_img = [], [], []
        for p in papers:
            row = conn.execute("SELECT period_tags, region_tags, topic_tags FROM literatures "
                               "WHERE lit_id=?", (p["lit_id"],)).fetchone()
            if row is None:
                continue
            if (row["period_tags"] or "").strip() != p.get("period_raw"):
                bad_raw.append(p["lit_id"])
            if (row["region_tags"] or "").strip() != p.get("region"):
                bad_reg.append(p["lit_id"])
            if p.get("img_count") != img.get(p["lit_id"], 0):
                bad_img.append(p["lit_id"])
        check("判据33 每篇的分期原文 = literatures.period_tags", not bad_raw, str(bad_raw[:5]))
        check("判据34 每篇的空间分区 = literatures.region_tags", not bad_reg, str(bad_reg[:5]))
        check("判据35 每篇的图版数 = images 表实数", not bad_img, str(bad_img[:5]))

        # 主题族：每篇的族集合 = 库里 topic_tags 的 S-08xx 码
        bad_fam = []
        for p in papers:
            row = conn.execute("SELECT topic_tags FROM literatures WHERE lit_id=?",
                               (p["lit_id"],)).fetchone()
            want = sorted(set(re.findall(r"S-08\d{2}", (row["topic_tags"] or "") if row else "")))
            got_f = sorted(set(p.get("families") or []))
            if want != got_f:
                bad_fam.append(p["lit_id"])
        check("判据36 每篇的主题族 = 库里 topic_tags 的 S-08xx 码", not bad_fam, str(bad_fam[:5]))
        check("判据37 展开点合计 = 339",
              sum(len(p.get("families") or []) for p in papers) == EXP_POINTS,
              str(sum(len(p.get("families") or []) for p in papers)))
    finally:
        conn.close()

    # 地图：分区数与上图数
    regions = m.get("regions") or []
    check("判据38 空间分区 12 个", len(regions) == EXP_REGIONS, str(len(regions)))
    on_map = [r for r in regions if r.get("on_map") and r.get("lat") is not None
              and r.get("lon") is not None]
    check("判据39 上图分区 6 个", len(on_map) == EXP_REGIONS_ON_MAP, str(len(on_map)))
    bad_coord = [r["name"] for r in regions
                 if r.get("lat") is not None and not (-90 <= float(r["lat"]) <= 90)]
    check("判据40 分区坐标都在合法范围内（不编造坐标）", not bad_coord, str(bad_coord[:5]))
    check("判据41 未上图的分区不带坐标",
          all(r.get("lat") is None for r in regions if not r.get("on_map")),
          str([r["name"] for r in regions if not r.get("on_map") and r.get("lat") is not None]))

    # 口径说明必须在
    notes = " ".join(d.get("notes") or [])
    check("判据42 口径写明上轴＝出版年", "出版年" in notes, "")
    check("判据43 口径写明分期带等宽（非年代跨度）", "等宽" in notes, "")
    check("判据44 口径写明分区代表点是示意坐标", "±1.1 km" in notes, "")

    js = api_get_js()
    check("判据45 GET /js/atlas.js 走白名单通道且返回模块",
          js is not None and "LZKG_ATLAS" in js, "")


def api_get_js():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8771/js/atlas.js", timeout=30) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None


def main() -> int:
    print("═══ 良渚时序（BP15）· 判据 ═══")
    test_frontend()
    if "--api" in sys.argv:
        test_api_truth()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
