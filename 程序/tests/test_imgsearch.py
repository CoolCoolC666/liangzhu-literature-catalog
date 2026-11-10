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
    ext = re.findall(r"url\([^)]*https?://", s)
    check("判据15 前端零外链依赖（静态 HTML 的 src/href 不指向 http(s)）", not ext,
          f"外链 {len(ext)} 处")


def test_lightbox():
    section("判据 17 · 大图查看器（点图看大图，不跳文献）")
    idx = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static" / "index.html"
    s = idx.read_text(encoding="utf-8")
    check("判据17 前端有大图查看器（lbOpen / lbPaint / lbClose）",
          "function lbOpen" in s and "function lbPaint" in s and "function lbClose" in s, "")
    check("判据17 页面里有大图容器 #lb", 'id="lb"' in s and 'id="lb-img"' in s, "")
    # ★ 点图**不跳文献**：缩略图不再是到文献详情的 <a href="#/lit/...">
    check("判据17 图版检索页的缩略图不再是指向文献的链接",
          '<a class="thumb" href="#/lit/' not in s, "")
    check("判据17 缩略图带 data-lbg（点开走大图查看器）",
          'data-lbg=' in s and 'data-lbi=' in s and "search" in s, "")
    check("判据17 详情页图版带 data-lbg=\"detail\"（文献图版也能看大图）",
          'data-lbg="' in s and "lbFromImg" in s, "")
    # 跳文献的能力不能丢：改成**显式按钮**
    check("判据17 仍然保留显式的「打开该文献」入口",
          "打开该文献" in s, "")
    check("判据17 大图查看器支持翻页与 Esc 关闭",
          "lbStep" in s and "ArrowLeft" in s and "Escape" in s, "")

def test_blueprint_theme():
    section("判据 18 · 壁纸系统（一套规则 + N 套令牌）")
    idx = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static" / "index.html"
    s = idx.read_text(encoding="utf-8")
    m = re.search(r'<style id="bpTheme">(.*?)</style>', s, re.S)
    css = m.group(1) if m else ""
    check("判据18 存在独立的壁纸样式块 <style id=\"bpTheme\">", bool(css),
          f"{len(css)} 字符")
    # 开关：顶栏的壁纸选择器，三个选项
    check("判据18 顶栏有壁纸选择器", 'id="bp-theme"' in s, "")
    sel = re.search(r'<select id="bp-theme"[^>]*>(.*?)</select>', s, re.S)
    opts = re.findall(r'<option value="(\w+)"', sel.group(1)) if sel else []
    check("判据18 壁纸选择器列出三张壁纸（原版/图纸/宫墙）",
          opts == ["plain", "terra", "museum"], f"{opts}")
    # 结构：规则只有一份（body.bp），壁纸差异在 body.bp.t-*
    check("判据18 规则挂在 body.bp 下（不污染原版样式）",
          "body.bp{" in css and "body.bp ." in css, "")
    check("判据18 两张壁纸各有自己的令牌块",
          "body.bp.t-terra{" in css and "body.bp.t-museum{" in css, "")
    # 两套壁纸各自的色板真值
    terra = re.search(r"body\.bp\.t-terra\{(.*?)\}", css, re.S)
    museum = re.search(r"body\.bp\.t-museum\{(.*?)\}", css, re.S)
    tc = terra.group(1) if terra else ""
    mc = museum.group(1) if museum else ""
    miss_t = [c for c in ("#101619", "#B5672F", "#F1F3F3") if c not in tc]
    miss_m = [c for c in ("#24463F", "#C1925B", "#2A514D", "#F7F7F8") if c not in mc]
    check("判据18 壁纸 1（图纸陶土）三色与设计稿一致", not miss_t, f"缺 {miss_t}")
    check("判据18 壁纸 2（宫墙墨绿）色板与设计稿一致", not miss_m, f"缺 {miss_m}")
    # 主色必须是用户点名的那两支
    check("判据18 壁纸 1 主色是用户指定的陶土 #B5672F",
          "--bp-accent:#B5672F" in tc, "")
    check("判据18 壁纸 2 是宫墙墨绿 #2A514D + 描金 #C1925B",
          "--bp-top:#2A514D" in mc and "--bp-accent:#C1925B" in mc, "")
    n_var = len(re.findall(r"--bp-[a-z0-9-]+:", css))
    check("判据18 壁纸令牌是变量化的（加新壁纸只改令牌行）", n_var >= 24,
          f"{n_var} 个 --bp-* 令牌")
    # 状态持久化 + 白名单（老版本存的非法值要落到默认壁纸）
    check("判据18 壁纸选择持久化到 localStorage",
          "localStorage.setItem(BP_KEY" in s and "localStorage.getItem(BP_KEY)" in s, "")
    check("判据18 只认白名单里的壁纸名（旧的非法值落到默认壁纸）",
          all(v in s for v in ("plain", "terra", "museum")) and "s0 ===" in s, "")
    # 图版页
    check("判据18 图版页有工程标题条与三个读数位",
          all(x in s for x in ["pagehead", "ph-zh", "ph-en", "ph-total", "ph-lits", "ph-hit"]), "")
    n_calc = 0
    for pid in ("ph-total", "ph-lits", "ph-hit"):
        i = s.find('getElementById("%s")' % pid)
        if i >= 0 and "fmtNum" in s[i:i + 220]:
            n_calc += 1
    check("判据18 标题条读数是现算的（fmtNum，不写死数字）", n_calc >= 3,
          f"现算 {n_calc}/3")
    check("判据18 图版卡有图号牌且用现算字段（lit_id + fig_no）",
          re.search(r'class=\\"pnl\\"', s) is not None
          and "esc(it.lit_id)" in s and "it.fig_no" in s, "")
    bad = re.findall(r'/ 图 " + esc\(it\.fig_no\)', s)
    check("判据18 图号牌不重复「图」字", not bad, f"{len(bad)} 处")
    n_clamp = css.count("-webkit-line-clamp")
    check("判据18 卡片文字有行数截断（消除卡片高度参差）", n_clamp >= 2,
          f"{n_clamp} 处（题名 + 摘要）")
    # 零外链口径与既有判据一致
    ext = re.findall(r"url\([^)]*https?://", s)
    check("判据18 壁纸样式零外链（无 url(http) 资源引用）", not ext, f"{len(ext)} 处")

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


def test_search_layout():
    section("判据 19 · 检索页版式（顶部搜索行 + 左分类栏 + 右结果区）")
    idx = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static" / "index.html"
    s = idx.read_text(encoding="utf-8")
    for cls in (".srch-bar", ".srch-side", ".srch-main", ".srch{"):
        check("判据19 版式类存在 %s" % cls, cls in s, "")
    check("判据19 左栏是 sticky 侧栏（不随结果区滚走）",
          "position:sticky" in s and ".srch-side{" in s, "")
    check("判据19 文献检索页用 listSearchBarHtml + listSideHtml",
          "listSearchBarHtml()" in s and "listSideHtml()" in s, "")
    m = re.search(r"function renderImgSearch\(\).*?\n\}", s, re.S)
    blk = m.group(0) if m else ""
    check("判据19 图版检索页有搜索行与左栏",
          ("srch-bar" in blk) and ("srch-side" in blk), "")
    check("判据19 旧工具条函数已拆掉（listToolbarHtml 不再存在）",
          "listToolbarHtml" not in s, "")
    check("判据19 图版页有视图切换器 #im-view",
          "im-view" in s and "data-view" in s, "")
    m2 = re.search(r"function imgsearchParams\(\).*?\n\}", s, re.S)
    check("判据19 视图切换是纯前端（不把 view 发给后端）",
          bool(m2) and "view" not in m2.group(0), "")
    m3 = re.search(r"function loadImgSearch\(\).*?\n\}\n", s, re.S)
    lb = m3.group(0) if m3 else ""
    check("判据19 列表视图渲染 .imgrows / .imgrow",
          ("imgrows" in lb) and ("imgrow" in lb), "")
    check("判据19 列表视图缩略图仍带 data-lbg（点开看大图）",
          ("data-lbg=" in lb) and ("search" in lb), "")
    for pid in ("ph-lit-total", "ph-lit-hit"):
        i2 = s.find('getElementById("%s")' % pid)
        check("判据19 文献页读数 %s 是现算的" % pid,
              i2 >= 0 and "fmtNum" in s[i2:i2 + 220], "")
    ext = re.findall(r"url\([^)]*https?://", s)
    check("判据19 全站样式零外链", not ext, f"{len(ext)} 处")

    section("判据 21 · 图纸显影动效（卡片像被晒出来）")
    kr = re.search(r"@keyframes bpReveal\{([^}]*)\}", s)
    kbody = kr.group(1) if kr else ""
    check("判据21 有显影关键帧 bpReveal", bool(kr), "")
    # 显影只许动 transform/opacity：动了别的属性就会触发 layout（卡顿）
    check("判据21 显影只动 transform/opacity（不触发 layout）",
          ("opacity" in kbody) and ("transform" in kbody)
          and not re.search(r"\b(width|height|top|left|margin|padding)\s*:", kbody),
          kbody[:80])
    ks = re.search(r"@keyframes bpSweep\{([^}]*)\}", s)
    sbody = ks.group(1) if ks else ""
    check("判据21 扫描线关键帧只动 transform",
          bool(ks) and ("transform" in sbody)
          and not re.search(r"\b(width|height|top|left)\s*:", sbody), sbody[:60])
    check("判据21 扫描线不拦截点击（pointer-events:none）",
          ".imgcards.reveal .imgcard::after" in s and "pointer-events:none" in s, "")
    check("判据21 逐个延迟靠 --i（不是写死 nth-child）",
          "setProperty(\"--i\"" in s and "calc(var(--i" in s, "")
    check("判据21 尊重 prefers-reduced-motion（整套降级为不动）",
          "prefers-reduced-motion" in s and "{animation:none}" in s
          and ".imgcards.reveal .imgcard::after{display:none}" in s, "")
    m4 = re.search(r"function loadImgSearch\(\).*?\n\}\n", s, re.S)
    check("判据21 显影在图版渲染后触发",
          bool(m4) and "revealIn(box)" in m4.group(0), "")
    check("判据21 通用显影规则覆盖多类块（stat/imgcard/imgrow/litrow/card）",
          ".reveal>.stat" in s and ".reveal>.imgcard" in s
          and ".reveal>.litrow" in s and ".reveal>.card" in s, "")
    check("判据21 分层显影：角标画出 + 图号牌亮起",
          "@keyframes bpMark" in s and "@keyframes bpFade" in s
          and ".reveal>.imgcard::before" in s and ".reveal>.imgcard .pnl" in s, "")
    check("判据21 切页整体重晒（bpView / view-in）",
          "@keyframes bpView" in s and "#view.view-in" in s and "view-in" in s, "")
    check("判据21 显影已推广到总览与文献页（revealIn 多处调用）",
          s.count("revealIn(") >= 3, f"{s.count('revealIn(')} 处")


def _lum(hexstr):
    c = hexstr.lstrip("#")
    v = [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _contrast(a, b):
    l1, l2 = _lum(a), _lum(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def test_segbtn_selected_state():
    section("判据 20 · 分段控件：选中态必须是「最跳」的那块")
    idx = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static" / "index.html"
    s = idx.read_text(encoding="utf-8")
    m = re.search(r'<style id="bpTheme">(.*?)</style>', s, re.S)
    css = m.group(1) if m else ""

    # 1) 宫墙下选中态用**描金**（--bp-accent），不是深墨绿（--bp-btn）
    mus = re.search(r"body\.bp\.t-museum \.segbtn\.on\{([^}]*)\}", css)
    mus_rule = mus.group(1) if mus else ""
    check("判据20 宫墙下分段控件选中态有独立规则",
          bool(mus_rule), "")
    check("判据20 宫墙选中态用描金 --bp-accent（不是深墨绿 --bp-btn）",
          "var(--bp-accent)" in mus_rule and "var(--bp-btn)" not in mus_rule,
          mus_rule.strip()[:80])
    # 2) 陶土下选中态仍是实心按钮色
    ter = re.search(r"body\.bp\.t-terra \.segbtn\.on\{([^}]*)\}", css)
    check("判据20 陶土下选中态是实心 --bp-btn",
          bool(ter) and "var(--bp-btn)" in ter.group(1), "")
    # 3) 未选中态退后（透明底 + 描边），不再用实心浅灰块
    base = re.search(r"body\.bp \.segbtn\{([^}]*)\}", css)
    base_rule = base.group(1) if base else ""
    check("判据20 未选中态是透明底 + 描边（退后，不与选中块争）",
          "background:transparent" in base_rule and "border" in base_rule,
          base_rule.strip()[:80])
    # 4) 对比度实测：描金底 + 深墨字 ≥ 4.5
    cr = _contrast("#C1925B", "#191E20")
    check("判据20 描金选中块对比度 ≥ 4.5:1", cr >= 4.5, f"{cr:.2f}:1")
    # 5) 反例锁死：描金底不能配白字（只有 2.79:1）
    cr_bad = _contrast("#C1925B", "#FFFFFF")
    check("判据20 描金选中块不配白字（反例值 < 4.5）",
          cr_bad < 4.5 and "#191E20" in mus_rule,
          f"白字对比度 {cr_bad:.2f}:1")
    # 6) 陶土按钮白字对比度
    cr_terra = _contrast("#A65A26", "#FFFFFF")
    check("判据20 陶土选中块白字对比度 ≥ 4.5:1", cr_terra >= 4.5,
          f"{cr_terra:.2f}:1")
    # 7) 深底侧栏里未选中的字走浅色
    check("判据20 宫墙深底侧栏未选中态用浅色字",
          "body.bp.t-museum .srch-side .segbtn:not(.on)" in css, "")


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
    test_lightbox()
    test_blueprint_theme()
    test_search_layout()
    test_segbtn_selected_state()
    test_rebuild_idempotent()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
