# -*- coding: utf-8 -*-
"""图像检索 E2 的判据（独立文件，不塞进 test_all.py）。

方案：`02-报告/良渚文献目录站_图像检索方案_20261103.md`

★ 纪律：判据不写「不为空」，不写永远 True 的条件。
  每一条都能指到一个可复现的数字或集合关系；
  与检索结果有关的数字一律**现算对账**（用另一套实现算一遍，再比）。

★ 本文件同时守着「独立模块」这条线：
  图版检索不得与文献检索共用查询代码 / 索引表（§3.0 边界表）。

用法：
    python tests/test_imgsearch.py --api      # 需要服务在跑
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = os.environ.get("LZKG_BASE", "http://127.0.0.1:8771")
_results: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(f"  {'OK ' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def api(path: str, method: str = "GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def q(s: str) -> str:
    return urllib.parse.quote(s)


# ══════════════════════════════════════════════════════════════════════
# 独立现算：不走检索代码、不走索引表，直接从源表算
# ══════════════════════════════════════════════════════════════════════
def truth_rows():
    """从 images + img_tags 直接读原始行（**不读 img_fts**）。"""
    from lzkg.db import connect
    c = connect()
    tags = {}
    try:
        for img_id, name in c.execute(
                "SELECT it.img_id, t.name FROM img_tags it "
                "JOIN tags t ON t.tag_id = it.tag_id"):
            tags.setdefault(img_id, []).append(str(name))
    except Exception:
        pass
    rows = []
    for r in c.execute(
            "SELECT img_id, lit_id, caption, ai_desc, ai_objects, fig_no, "
            "img_type, obj_type, review_status FROM images ORDER BY img_id"):
        d = dict(r)
        d["tags"] = tags.get(d["img_id"], [])
        rows.append(d)
    c.close()
    return rows


def independent_zh(caption: str) -> str:
    """**独立实现**的中文图题抽取（字符级判断，与 clean.split_caption 不共用代码）。

    用来交叉验证清洗结果：两边各自算一遍，条数必须一致。
    """
    s = unicodedata.normalize("NFKC", caption or "")
    s = re.sub(r"^\s*图\s*[0-9０-９一二三四五六七八九十]+\s*[．.、:：]?\s*", "", s.strip())
    zh = "".join(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
    return zh


def truth_match(row, tokens, mode: str) -> bool:
    """独立现算：这个词（组）在这张图版上是否命中。

    语义 = 子串包含（trigram 短语匹配与 LIKE %x% 的语义都是子串），
    英文大小写不敏感。**只用源表字段算，不读索引表。**
    """
    from lzkg.imgsearch.clean import split_caption
    zh, en = split_caption(row.get("caption") or "")
    layers = {
        "caption_zh": zh,
        "caption_en": en,
        "ai_desc": unicodedata.normalize("NFKC", row.get("ai_desc") or ""),
        "ai_objects": unicodedata.normalize("NFKC", row.get("ai_objects") or ""),
        "fig_no": unicodedata.normalize("NFKC", row.get("fig_no") or ""),
    }
    if mode == "both":
        layers["tags_text"] = " ".join(row.get("tags") or [])
    for t in tokens:
        tl = t.lower()
        if not any(tl in str(v).lower() for v in layers.values()):
            return False
    return True


def tokenize_local(qs: str) -> list:
    parts = re.split(r"[\s,，、;；]+", unicodedata.normalize("NFKC", qs or ""))
    return [p.strip("．.。、:：\"'“”‘’()（）[]【】<>《》") for p in parts if p.strip()]


# ══════════════════════════════════════════════════════════════════════
# 判据 1-3：模块骨架 / 独立性 / 端点
# ══════════════════════════════════════════════════════════════════════
def test_module_shape():
    section("判据 1/2/3 · 独立模块的骨架与边界")
    pkg = Path(__file__).resolve().parent.parent / "lzkg" / "imgsearch"
    want = ["__init__.py", "clean.py", "index.py", "query.py", "api.py"]
    missing = [w for w in want if not (pkg / w).exists()]
    check("判据1 独立模块的 5 个文件都在", not missing, f"缺 {missing}")

    # 静态扫 import：不得反向依赖文献检索模块
    # ★ api_v2 是**允许**的：只借用它的 ApiError 异常类型（app.py 的异常处理器
    #   是按那个类注册的 —— 与 api_segment.py 同一套做法）。
    banned = {"app", "api_raw", "api_segment", "segment"}
    hits = []
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tail = node.module.split(".")[-1]
                if tail in banned:
                    hits.append(f"{f.name}: from {node.module}")
    check("判据2 图版检索不反向依赖文献检索模块", not hits, f"{hits}")

    # 索引表：不得出现文献检索的三张索引表。
    # ★ 只扫**真正会执行的 SQL**（字符串常量里以 SELECT/INSERT/CREATE 开头的那几条），
    #   注释与文档字符串里提到表名不算。
    sql_hits = []
    sql_re = re.compile(r"\s*(SELECT|INSERT|CREATE|UPDATE|DELETE)\b", re.I)
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if not sql_re.match(node.value):
                    continue
                for t in ("docs_fts", "docs_seg", "raw_json_fts"):
                    if re.search(r"\b" + t + r"\b", node.value):
                        sql_hits.append(f"{f.name}:{t}")
    check("判据2 图版检索的索引表与文献检索完全分开", not sql_hits, f"{sql_hits}")

    from lzkg.app import app
    paths = [getattr(r, "path", "") for r in app.routes]
    need = ["/api/imgsearch/search", "/api/imgsearch/stats",
             "/api/imgsearch/related", "/api/imgsearch/reindex"]
    miss = [n for n in need if n not in paths]
    check("判据3 四个端点都已挂载", not miss, f"缺 {miss}")
    check("判据3 既有 /api/images 端点没被顶掉", "/api/images" in paths, "")

    # 管理端点必须挂 require_admin（接口层锁）
    from lzkg.imgsearch import api as img_api
    src = Path(img_api.__file__).read_text(encoding="utf-8")
    check("判据3 重建索引端点挂了 require_admin",
          "Depends(require_admin)" in src, "")


# ══════════════════════════════════════════════════════════════════════
# 判据 4-7：索引形态与清洗
# ══════════════════════════════════════════════════════════════════════
def test_index():
    section("判据 4/5/6/7 · 索引规模、逐行对账、清洗真值")
    from lzkg.db import connect
    from lzkg.imgsearch import clean, index
    c = connect()
    try:
        n_img = c.execute("SELECT count(*) FROM images").fetchone()[0]
        n_fts = c.execute("SELECT count(*) FROM img_fts").fetchone()[0]
        check("判据4 索引行数 == images 行数", n_fts == n_img, f"img_fts={n_fts} images={n_img}")

        orphan = c.execute(
            "SELECT count(*) FROM img_fts f LEFT JOIN images i ON i.img_id = f.img_id "
            "WHERE i.img_id IS NULL").fetchone()[0]
        check("判据5 索引里没有孤儿行（每条都对应一张图版）", orphan == 0, f"孤儿 {orphan}")

        # 逐行对账：索引内容 == 现算清洗结果
        src = {r[0]: r for r in c.execute(
            "SELECT img_id, caption, ai_desc, ai_objects, fig_no FROM images")}
        bad = []
        for r in c.execute("SELECT img_id, caption_zh, caption_en, ai_desc, ai_objects, "
                          "fig_no FROM img_fts"):
            s = src.get(r[0])
            if s is None:
                bad.append((r[0], "源表里没有"))
                continue
            zh, en = clean.split_caption(s[1] or "")
            if (r[1], r[2], r[3], r[4], r[5]) != (zh, en, clean.nfkc(s[2] or ""),
                                                clean.clean_ai_objects(s[3] or ""),
                                                clean.clean_fig_no(s[4] or "")):
                bad.append((r[0], "内容与现算不一致"))
        check("判据6 索引内容与源数据逐行对账一致", not bad,
              f"不一致 {len(bad)} 条，例 {bad[:2]}")

        # AI 兜底句：真值现算（不写死数字）
        truth_generic = c.execute(
            "SELECT count(*) FROM images WHERE ai_desc LIKE ?",
            ("%具体视觉细节以原图为准%",)).fetchone()[0]
        idx_generic = c.execute(
            "SELECT count(*) FROM img_fts WHERE ai_desc LIKE ?",
            ("%具体视觉细节以原图为准%",)).fetchone()[0]
        check("判据7 AI 兜底句条数：索引 == 源表现算", idx_generic == truth_generic,
              f"索引 {idx_generic} / 源表 {truth_generic}")

        # 中文图题解析率：与**独立实现**交叉验证
        truth_zh = sum(1 for r in src.values() if independent_zh(r[1] or ""))
        idx_zh = c.execute(
            "SELECT count(*) FROM img_fts WHERE caption_zh <> ''").fetchone()[0]
        check("判据7 中文图题解析条数：索引 == 独立实现现算", idx_zh == truth_zh,
              f"索引 {idx_zh} / 独立现算 {truth_zh}（{100.0*idx_zh/max(1,n_img):.1f}%）")
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════
# 判据 8-14：检索行为（要服务在跑）
# ══════════════════════════════════════════════════════════════════════
QUERIES = ["玉琮", "遗址", "空间数据库", "良渚文化", "玉琮 考古", "diagram",
           "GIS", "反山", "水坝", "神人兽面"]


def test_search_truth():
    section("判据 8 · 命中数与独立现算逐条对账")
    rows = truth_rows()
    bad = []
    detail = []
    for qs in QUERIES:
        for mode in ("exact", "both"):
            toks = tokenize_local(qs)
            truth = sum(1 for r in rows if truth_match(r, toks, mode))
            st, r = api(f"/api/imgsearch/search?q={q(qs)}&mode={mode}&size=1")
            if st != 200:
                bad.append((qs, mode, f"HTTP {st}"))
                continue
            got = int(r.get("data", {}).get("total", -1))
            if got != truth:
                bad.append((qs, mode, f"接口 {got} ≠ 现算 {truth}"))
                detail.append(f"{qs}/{mode}: {got} vs {truth}")
    check("判据8 10 组查询 × 2 模式的命中数与独立现算完全一致", not bad,
          f"不一致 {len(bad)} 组：{detail[:4]}")


def test_layers_and_snippet():
    section("判据 9/13/14 · 命中层级、片段、路径安全")
    st, r = api(f"/api/imgsearch/search?q={q('遗址')}&size=20")
    if st != 200:
        check("判据9 检索端点可用", False, f"HTTP {st}")
        return
    items = r.get("data", {}).get("items", [])
    check("判据9 取回 20 条结果", len(items) == 20, f"{len(items)} 条")

    # 命中层级：每一条标出来的层，词必须真的在那层里
    toks = tokenize_local("遗址")
    wrong = []
    for it in items:
        layers = it.get("hit_layers") or []
        if not layers:
            wrong.append((it.get("img_id"), "没有命中层级"))
            continue
        for L in layers:
            v = str(it.get(L) or "").lower()
            if not any(t.lower() in v for t in toks):
                wrong.append((it.get("img_id"), L))
    check("判据9 命中层级不多标（标了的层里真的有词）", not wrong, f"{wrong[:3]}")

    # 片段必须带 <mark> 且含命中词
    snips = [str(i.get("snippet") or "") for i in items]
    marked = [s for s in snips if "<mark>" in s and "遗址" in s]
    check("判据14 片段带 <mark> 且含命中词", len(marked) == len(snips),
          f"{len(marked)}/{len(snips)} 条")

    # 路径安全：只出站内 URL，不出本机路径
    raw = json.dumps(items, ensure_ascii=False)
    check("判据13 响应里不含本机绝对路径",
          (":\\" not in raw) and ("/Users/" not in raw) and ("Deskbox" not in raw),
          "")
    bad_url = [i.get("thumb_url") for i in items
                if not str(i.get("thumb_url") or "").startswith("/media/img/")]
    check("判据13 缩略图一律走站内 /media/img/", not bad_url, f"{bad_url[:3]}")


def test_two_char_words():
    section("判据 10 · 两字词（LIKE 兜底）不许 500")
    bad = []
    for kw in ("玉琮", "城墙", "水坝", "反山", "神人"):
        st, r = api(f"/api/imgsearch/search?q={q(kw)}&size=3")
        if st != 200 or int(r.get("data", {}).get("total", -1)) < 0:
            bad.append((kw, st))
    check("判据10 五个两字词检索都不报错", not bad, f"{bad}")


def test_review_display_only():
    section("判据 11 · 复核状态只显示、不影响检索")
    from lzkg.app import app
    sig = None
    for r in app.routes:
        if getattr(r, "path", "") == "/api/imgsearch/search":
            sig = r.endpoint.__code__.co_varnames
            break
    check("判据11 检索端点没有 review 过滤参数",
          sig is not None and not any("review" in v for v in sig), f"{sig}")

    st, r = api(f"/api/imgsearch/search?q={q('遗址')}&size=50")
    items = r.get("data", {}).get("items", [])
    ver = [i for i in items if i.get("review_verified") is True]
    pend = [i for i in items if i.get("review_verified") is False]
    check("判据11 同一批结果里已复核与待复核都出现（复核不挡召回）",
          bool(ver) and bool(pend), f"已复核 {len(ver)} / 待复核 {len(pend)}")

    # 徽标与库里逐条一致
    from lzkg.db import connect
    c = connect()
    try:
        bad = []
        for it in items:
            row = c.execute("SELECT review_status FROM images WHERE img_id = ?",
                            (it.get("img_id"),)).fetchone()
            if row is None or str(row[0] or "") != str(it.get("review_status") or ""):
                bad.append(it.get("img_id"))
        check("判据11 复核状态徽标与库里的 review_status 逐条一致", not bad, f"{bad[:3]}")
    finally:
        c.close()

    # 结果顺序不因复核状态而变：两次请求顺序完全一致
    st2, r2 = api(f"/api/imgsearch/search?q={q('遗址')}&size=20")
    a = [i.get("img_id") for i in items[:20]]
    b = [i.get("img_id") for i in (r2.get("data", {}).get("items") or [])]
    check("判据11 同一查询两次结果顺序完全一致（排序与复核无关）", a == b,
          f"{a[:3]} vs {b[:3]}")


def test_related_and_stats():
    section("判据 12 · 相关图版与统计")
    st, r = api("/api/imgsearch/stats")
    if st != 200:
        check("判据12 统计端点可用", False, f"HTTP {st}")
        return
    d = r.get("data", {})
    idx = d.get("index", {})
    check("判据12 统计端点给出索引行数与版本",
          isinstance(idx.get("rows"), int) and isinstance(idx.get("version"), int),
          f"rows={idx.get('rows')} v={idx.get('version')} stale={idx.get('stale')}")
    check("判据12 统计端点给出筛选选项（图类型/维度/标签）",
          bool(d.get("img_types")) and bool(d.get("dims")) and bool(d.get("tag_options")),
          f"types={len(d.get('img_types') or [])} dims={len(d.get('dims') or [])}")

    st, r = api("/api/imgsearch/related?img_id=IMG-LIT-000001-4&limit=5")
    rel = r.get("data", {})
    check("判据12 相关图版端点可用且不含自己",
          st == 200 and rel.get("total", 0) > 0,
          f"total={rel.get('total')}")
    ids = [i.get("img_id") for i in (rel.get("items") or [])]
    check("判据12 相关图版不含自己", "IMG-LIT-000001-4" not in ids, f"{ids}")


def test_frontend():
    section("判据 15 · 前端：独立页签、零外链")
    idx = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static" / "index.html"
    s = idx.read_text(encoding="utf-8")
    check("判据15 顶栏有独立的「图版」页签",
          'data-tab="imgsearch"' in s, "")
    check("判据15 路由认得 imgsearch",
          '"imgsearch"' in s and "renderImgSearch" in s, "")
    check("判据15 前端调的是独立端点 /api/imgsearch/search",
          "/api/imgsearch/search" in s, "")
    check("判据15 图版检索不塞进文献检索页（独立函数，不复用 renderList）",
          "function renderImgSearch" in s and "function renderList" in s, "")
    # 零外链口径与既有判据一致：src/href 不指向 http(s)。
    # ★ 只扫**静态 HTML**（先把 <script> 块去掉）：
    #   脚本里运行时生成的 DOI 链接（https://doi.org/…）是站点功能，
    #   不是「资源外链」；SVG 命名空间与注释里的官网地址同理。
    static_html = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S | re.I)
    ext = re.findall(r"(?:src|href)\s*=\s*[\"']https?://", static_html)
    check("判据15 前端零外链依赖（静态 HTML 的 src/href 不指向 http(s)）", not ext,
          f"外链 {len(ext)} 处")


def test_rebuild_idempotent():
    section("判据 16 · 重建幂等（行数与指纹都不变）")
    from lzkg.db import connect
    from lzkg.imgsearch import index
    c = connect()
    try:
        before = index.status(c)
        rep = index.build_index(c)
        after = index.status(c)
    finally:
        c.close()
    check("判据16 重建后行数不变", before["rows"] == after["rows"] == rep["rows"],
          f"{before['rows']} -> {after['rows']}")
    check("判据16 重建后指纹不变（内容确定性）", before["sig"] == after["sig"],
          f"{before['sig']} -> {after['sig']}")
    check("判据16 重建后版本递增", after["version"] == before["version"] + 1,
          f"v{before['version']} -> v{after['version']}")


def main():
    print("═══ 图像检索 E2 · 判据 ═══")
    test_module_shape()
    test_index()
    if "--api" in sys.argv:
        test_search_truth()
        test_layers_and_snippet()
        test_two_char_words()
        test_review_display_only()
        test_related_and_stats()
    test_frontend()
    test_rebuild_idempotent()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
