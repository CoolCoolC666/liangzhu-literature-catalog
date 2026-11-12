# -*- coding: utf-8 -*-
"""良渚时间图谱（BP14 · 阶段 1 样例版）判据。

方案：`02-报告/良渚文献目录站_时间图谱可行性报告_20261112.md`

★ 纪律：每条判据都指到一个**实测数字或集合关系**；
  与端点有关的数字一律**另写一套算法现算对账**（直连库重算，再比），
  不拿端点自己的返回值当"真值"。

用法：
    python tests/test_timeline.py --api      # 需要服务在跑
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


# ── 期望值：都是实测真值（写死，变了要改这里并说明）──
EXP_X = [("PRE", 1), ("EARLY_MID", 3), ("MID", 5), ("MID_LATE", 7),
         ("LATE", 117), ("SPAN_ALL", 14), ("GENERAL", 15), ("CONTEMP", 38)]
EXP_Y = [("S-0801", 121), ("S-0802", 111), ("S-0803", 63), ("S-0804", 44)]
EXP_PAPERS = 200
EXP_POINTS = 339
EXP_MULTI = 139
EXP_YEAR_MIN, EXP_YEAR_MAX = 1984, 2026


def test_frontend():
    print("\n── 前端文件与接线 ──")
    js = ROOT / "lzkg" / "web" / "static" / "js" / "timeline.js"
    check("判据1 timeline.js 存在", js.exists(), str(js))
    if js.exists():
        src = js.read_text(encoding="utf-8")
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        check("判据2 timeline.js 通过 node --check（ES5 语法门）", r.returncode == 0,
              (r.stderr or "").strip()[:120])
        check("判据3 timeline.js 暴露 window.LZKG_TL", "global.LZKG_TL = TL" in src, "")
        check("判据4 timeline.js 不含外链（http/https 引用）",
              not re.search(r'(src|href)\s*=\s*["\']https?://', src), "")
    app = (ROOT / "lzkg" / "app.py").read_text(encoding="utf-8")
    check("判据5 /js 白名单登记 timeline.js", '"timeline.js"' in app, "")
    html = (ROOT / "lzkg" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    check("判据6 index.html 引用 /js/timeline.js", '/js/timeline.js' in html, "")
    check("判据7 index.html 有时间图谱容器 #w-tl", 'id="w-tl"' in html, "")
    check("判据8 index.html 拉取 /api/stats/timeline-matrix",
          "/api/stats/timeline-matrix" in html, "")
    check("判据9 地图点分区 → 矩阵高亮的反向钩子已接",
          "LZKG_TL.onRegionPick" in html, "")

    # ── BP14-i18n：界面文案走词典，词条不许漏；联动判据不许被短路 ──
    i18n = (ROOT / "lzkg" / "web" / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    check("判据29 i18n 词典含 BP14 时间图谱词条块", "BP14 良渚时间图谱" in i18n, "")
    if js.exists():
        src = js.read_text(encoding="utf-8")
        used = set(re.findall(r'\bT[F]?\("([^"]+)"\)', src))
        dict_keys = set(re.findall(r'^\s{4}"(.+?)":\s*\[', i18n, re.M))
        missing = sorted(k for k in used if k not in dict_keys)
        check("判据30 时间图谱界面文案全部在 i18n 词典中（无漏翻）",
              not missing, str(missing[:4]))
        check("判据31 isDim 不因「未选中」短路（分区高亮独立生效）",
              "if (!s) return false;" not in src, "")
        check("判据32 分区名比较统一去编码（regionKey）", "function regionKey(" in src, "")
        check("判据33 切语言会重画矩阵（data-lang 监听）", "data-lang" in src, "")


def test_matrix_truth():
    print("\n── 矩阵端点：与库现算对账 ──")
    res = api_get("/api/stats/timeline-matrix")
    check("判据10 /api/stats/timeline-matrix 返回 ok", res.get("ok") is True, "")
    d = res.get("data") or {}
    x = d.get("x") or []
    y = d.get("y") or []
    pts = d.get("points") or []
    cells = d.get("cells") or {}

    got_x = [(b["key"], b["n"]) for b in x]
    check("判据11 X 轴＝良渚分期带 8 档（键与篇数实测）", got_x == EXP_X, str(got_x))
    got_y = [(b["key"], b["n"]) for b in y]
    check("判据12 Y 轴＝主题族 4 带（码族与篇数实测）", got_y == EXP_Y, str(got_y))

    meta = d.get("meta") or {}
    check("判据13 文献总数 = 200", meta.get("papers") == EXP_PAPERS, str(meta.get("papers")))
    check("判据14 展开后的点数 = 339", meta.get("points") == EXP_POINTS, str(meta.get("points")))
    check("判据15 一篇多族（多带）的文献 = 139",
          meta.get("multi_family") == EXP_MULTI, str(meta.get("multi_family")))
    check("判据16 年份跨度 = 1984–2026",
          meta.get("year_min") == EXP_YEAR_MIN and meta.get("year_max") == EXP_YEAR_MAX,
          f"{meta.get('year_min')}-{meta.get('year_max')}")

    conn = db_conn()
    try:
        # 另算一套：分期原文 → 归一（与端点同一张映射表，但要自己重新数一遍）
        MAP = {"崧泽-良渚过渡": "PRE", "新石器时代早中期": "EARLY_MID",
               "新石器时代中期": "MID", "新石器时代中晚期": "MID_LATE",
               "新石器时代晚期": "LATE", "新石器时代晚期（申遗后）": "LATE",
               "新石器时代早中晚三期": "SPAN_ALL", "通用": "GENERAL", "当代": "CONTEMP"}
        cnt: dict = {}
        fam_cnt: dict = {}
        img: dict = {}
        for r in conn.execute("SELECT lit_id, period_tags, topic_tags FROM literatures"):
            k = MAP.get((r["period_tags"] or "").strip(), "OTHER")
            cnt[k] = cnt.get(k, 0) + 1
            for code in set(re.findall(r"S-08\d{2}", r["topic_tags"] or "")):
                fam_cnt[code] = fam_cnt.get(code, 0) + 1
        for r in conn.execute("SELECT lit_id, COUNT(*) n FROM images GROUP BY lit_id"):
            img[r["lit_id"]] = r["n"]

        check("判据17 X 各档篇数与库现算一致",
              all(cnt.get(k) == n for k, n in EXP_X),
              str({k: (cnt.get(k), n) for k, n in EXP_X}))
        check("判据18 Y 各族篇数与库现算一致",
              all(fam_cnt.get(k) == n for k, n in EXP_Y),
              str({k: (fam_cnt.get(k), n) for k, n in EXP_Y}))

        # 每点的图版数、分区必须与库一致
        bad_img = []
        bad_reg = []
        for p in pts:
            if p.get("img_count") != img.get(p["lit_id"], 0):
                bad_img.append(p["lit_id"])
            row = conn.execute("SELECT region_tags FROM literatures WHERE lit_id=?",
                               (p["lit_id"],)).fetchone()
            if row is not None and (row[0] or "").strip() != p.get("region"):
                bad_reg.append(p["lit_id"])
        check("判据19 每点图版数 = images 表实数", not bad_img, str(bad_img[:5]))
        check("判据20 每点空间分区 = literatures.region_tags 原文", not bad_reg, str(bad_reg[:5]))

        # 覆盖：端点的文献集合 = 库全部文献
        db_ids = set(r["lit_id"] for r in conn.execute("SELECT lit_id FROM literatures"))
        api_ids = set(p["lit_id"] for p in pts)
        check("判据21 端点覆盖库里全部 200 篇文献", api_ids == db_ids,
              f"缺 {len(db_ids - api_ids)} 篇 / 多 {len(api_ids - db_ids)} 篇")

        # 点唯一性：(分期, 族, 文献) 不得重复
        keys = [(p["period"], p["family"], p["lit_id"]) for p in pts]
        check("判据22 点不重复（分期×族×文献 唯一）", len(set(keys)) == len(keys),
              f"{len(keys) - len(set(keys))} 个重复")
    finally:
        conn.close()

    # 格计数 = 点数
    check("判据23 各格计数之和 = 点数", sum(cells.values()) == len(pts),
          f"{sum(cells.values())} vs {len(pts)}")

    # 年份归一：无越界
    bad_year = [p["lit_id"] for p in pts
                if p.get("y") is not None and not (EXP_YEAR_MIN <= p["y"] <= EXP_YEAR_MAX)]
    check("判据24 归一年份全部落在 1984–2026", not bad_year, str(bad_year[:5]))

    # 口径说明必须在（分期/族/码段不一致 三件事）
    notes = " ".join(d.get("notes") or [])
    check("判据25 口径说明写明 X 轴＝良渚分期", "X 轴＝良渚文化分期" in notes, "")
    check("判据26 口径说明写明 Y 轴＝主题族", "Y 轴＝主题族" in notes, "")
    check("判据27 口径说明写明 S-08xx 码不在受控词表", "不在受控词表" in notes, "")

    # 模块能从 /js/ 通道取到（白名单通道，不是整目录）
    js = api_get_js()
    check("判据28 GET /js/timeline.js 走白名单通道且返回模块", js is not None and "LZKG_TL" in js, "")


def api_get_js():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8771/js/timeline.js", timeout=30) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None


def main() -> int:
    print("═══ 良渚时间图谱（BP14）· 判据 ═══")
    test_frontend()
    if "--api" in sys.argv:
        test_matrix_truth()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
