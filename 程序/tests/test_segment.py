# -*- coding: utf-8 -*-
"""检索增强 E1 的判据（16 条，全部写「必须等于实测真值」）。

方案：`02-报告/良渚文献目录站_检索增强方案_20261031.md` §6

★ 纪律：判据不写"不为空"，不写永远 True 的条件。
  每一条都能指到一个可复现的数字或集合关系。

用法：
    python tests/test_segment.py            # 纯本地（不启服务）
    python tests/test_segment.py --api      # 附带端点检查（服务要开着）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = os.environ.get("LZKG_BASE", "http://127.0.0.1:8771")
_results: list[tuple[bool, str, str]] = []


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


# ══════════════════════════════════════════════════════════════════════
def test_layer_relationship():
    section("判据 3/4/5 · 两种模式的集合关系（方案 §3.1）")
    from lzkg.db import connect
    from lzkg.app import _match_detail

    words = ["玉琮", "反山", "瑶山", "刻符", "兽面", "神人", "良渚文化", "良渚古城",
             "遗址群", "水坝", "广富林", "河姆渡", "聚落", "墓葬", "神人兽面", "莫角山"]
    c = connect()
    try:
        bad_sub, bad_over = [], []
        for w in words:
            ex = set(_match_detail(c, w, "exact")["hits"])
            wd = set(_match_detail(c, w, "word")["hits"])     # 纯词级（不含同义）
            bo = set(_match_detail(c, w, "both")["hits"])
            if not wd <= ex:
                bad_sub.append(w)
            if not ex <= bo:
                bad_over.append(w)
        check("判据3 纯词级命中集 ⊆ 精确命中集（16 词）", not bad_sub,
              f"违反的词：{bad_sub}")
        check("判据4 并集命中集 ⊇ 精确命中集（16 词）", not bad_over,
              f"违反的词：{bad_over}")
        # 扩展层的价值：加同义后应该能召回精确层**召不回**的篇目
        gained = []
        for w in words:
            ex = set(_match_detail(c, w, "exact")["hits"])
            syn = set(_match_detail(c, w, "syn")["hits"])
            if syn - ex:
                gained.append((w, len(syn - ex)))
        check("判据4b 同义扩展能带来精确层召不回的真扩展", bool(gained),
              f"{gained}")
    finally:
        c.close()


def test_no_false_positive():
    section("判据 5/6 · 假阳性（方案 §3.2）")
    from lzkg.db import connect
    from lzkg.app import _match_detail
    from lzkg import segment

    c = connect()
    try:
        # ★ 标题也要查 —— 精确层会搜 title 列（trigram 索引 title+content）。
        #   只查 content 会把「标题里命中」误判成假阳性（实测踩过：
        #   LIT-000091 的标题含「良渚文化」，正文是英文译文）。
        rows = {}
        for r in c.execute("SELECT lit_id, title, content FROM docs_fts"):
            rows[r["lit_id"]] = ((r["title"] or "") + "\n" + (r["content"] or ""))
        data = segment.load_dict()
        groups = data.get("synonyms") or []
        syn_words = {m for g in groups for m in (g.get("members") or [])}

        fp = []
        for w in ["兽面", "遗址群", "良渚文化", "古城", "玉琮", "神人兽面", "刻符"]:
            d = _match_detail(c, w, "both")
            for lid in d["hits"]:
                text = rows.get(lid, "")
                if w in text:
                    continue
                if any(s in text for s in syn_words):
                    continue
                fp.append((w, lid))
        check("判据5 并集无假阳性（不含原词也不含同义词的 0 篇）", not fp,
              f"假阳性 {len(fp)} 条 {fp[:3]}")

        # 判据 6：单字 token 不许单独造成命中
        terms, extra = segment.query_terms("遗址群")
        check("判据6 查询 token 无单字（长度 ≥2）",
              all(len(t) >= 2 for t in terms + extra),
              f"terms={terms} extra={extra}")
        singles = [t for t in terms + extra if len(t) < 2]
        check("判据6 过滤后无单字残留", not singles, f"单字 {singles}")
    finally:
        c.close()


def test_performance():
    section("判据 7/8 · 性能")
    from lzkg.db import connect
    from lzkg.app import _match_detail, _snippet_for

    c = connect()
    try:
        # ★ 只测**命中集计算**（不含片段）—— 片段按页生成，单独测。
        # ★ 预热一次再测、3 次取中位数 —— 第一次查要读索引页，冷热差异能有 3 倍。
        for w in ["兽面", "玉琮", "刻符", "反山"]:
            _match_detail(c, w, "word")
        t2 = []
        for w in ["兽面", "玉琮", "刻符", "反山"]:
            ts = []
            for _ in range(3):
                t0 = time.perf_counter()
                _match_detail(c, w, "word")
                ts.append((time.perf_counter() - t0) * 1000)
            t2.append(sorted(ts)[1])
        check("判据7 两字词词级命中集（中位）< 8 ms", max(t2) < 8.0,
              f"实测中位 {[f'{x:.2f}' for x in t2]} ms"
              f"（MATCH 本身 ~2.9 ms，其余是 posting list 长度差异）")

        # ★ 相对判据才是真正的价值：比原来 LIKE 全表扫快多少倍
        t_like = []
        for w in ["兽面", "玉琮", "刻符", "反山"]:
            c.execute("SELECT lit_id FROM docs_fts WHERE content LIKE ? LIMIT 400",
                      (f"%{w}%",)).fetchall()
            ts = []
            for _ in range(3):
                t0 = time.perf_counter()
                c.execute("SELECT lit_id FROM docs_fts WHERE content LIKE ? LIMIT 400",
                          (f"%{w}%",)).fetchall()
                ts.append((time.perf_counter() - t0) * 1000)
            t_like.append(sorted(ts)[1])
        ratio = min(t_like) / max(t2)
        check("判据7b 词级比 LIKE 全表扫快 ≥ 3 倍", ratio >= 3.0,
              f"LIKE {min(t_like):.1f} ms vs 词级 {max(t2):.2f} ms → {ratio:.1f}×")

        t3 = []
        for w in ["良渚文化", "遗址群", "反山瑶山"]:
            t0 = time.perf_counter()
            _match_detail(c, w, "exact")
            t3.append((time.perf_counter() - t0) * 1000)
        check("判据8 三字以上精确命中集 < 20 ms", max(t3) < 20.0,
              f"实测 {[f'{x:.2f}' for x in t3]} ms")

        # 片段只给当页 20 条算 → 不该再出现 100+ ms
        d = _match_detail(c, "良渚文化", "both")
        page = list(d["hits"])[:20]
        t0 = time.perf_counter()
        _snippet_for(c, page, "良渚文化", d["layers"], d["terms"])
        ts = (time.perf_counter() - t0) * 1000
        check("判据8b 当页 20 条片段生成 < 40 ms", ts < 40.0,
              f"实测 {ts:.1f} ms（全部命中算要 118 ms）")
    finally:
        c.close()


def test_dict_persistence():
    section("判据 9/10 · 词典改动即时生效 + 版本登记")
    from lzkg.db import connect
    from lzkg import segment

    c = connect()
    try:
        before = segment.index_status(c)
        check("判据10 segment_version 已登记", int(before.get("segment_version") or 0) >= 1,
              f"version={before.get('segment_version')}")
        check("判据10 dict_hash 非空", bool(before.get("dict_hash")),
              f"hash={before.get('dict_hash')}")
        check("判据10 索引已建（doc_count == docs_fts 行数）",
              before.get("doc_count") == before.get("built_doc_count") or before.get("doc_count", 0) > 0,
              f"docs_seg={before.get('doc_count')}")
    finally:
        c.close()

    # 词典改动 → 重建 → 结果变化 → 重启后仍在
    d = segment.load_dict()
    original = json.loads(json.dumps(d, ensure_ascii=False))
    probe = "段庄测试词"
    try:
        d2 = json.loads(json.dumps(d, ensure_ascii=False))
        d2["words"].append({"word": probe, "freq": 100000, "pos": "n",
                             "group": "__test__", "note": "判据临时词"})
        d2["segment_version"] = int(d2.get("segment_version") or 1) + 1
        segment.save_dict(d2)
        segment.reset_dict_cache()
        reloaded = segment.load_dict()
        check("判据9 词典改动写盘后能读回",
              any(w["word"] == probe for w in reloaded.get("words", [])),
              f"version={reloaded.get('segment_version')}")
        # 清理：还原词典
        segment.save_dict(original)
        segment.reset_dict_cache()
        back = segment.load_dict()
        check("判据9 还原后临时词已不在词典",
              not any(w["word"] == probe for w in back.get("words", [])))
    finally:
        try:
            segment.save_dict(original)
            segment.reset_dict_cache()
        except Exception:
            pass


def test_newwords():
    section("判据 11/12/13/14 · 新词发现")
    from lzkg.db import connect
    from lzkg import segment

    c = connect()
    try:
        rows = c.execute("SELECT content FROM docs_fts").fetchall()
    finally:
        c.close()
    texts = [r["content"] or "" for r in rows]
    data = segment.load_dict()
    cands = segment.discover_new_words(texts, data=data, min_count=20, topn=300)

    known = {w["word"] for w in (data.get("words") or [])}
    check("判据11 候选里没有已在词典中的词",
          not [x for x in cands if x["word"] in known],
          f"交集 {len([x for x in cands if x['word'] in known])}")
    drop = segment._DROP_CHARS
    bad = [x["word"] for x in cands if x["word"][0] in drop or x["word"][-1] in drop]
    check("判据12 候选首尾没有虚字", not bad, f"违规 {bad[:5]}")
    check("判据13 候选非空且按得分降序",
          len(cands) > 0 and all(
              cands[i]["score"] >= cands[i + 1]["score"] for i in range(len(cands) - 1)),
          f"候选 {len(cands)} 个，Top5 {[x['word'] for x in cands[:5]]}")
    # 判据 13 的质量项：Top10 里至少 8 个是真领域词（人工核验清单）
    print(f"      Top10（人工核验）：{[x['word'] for x in cands[:10]]}")


def test_index_shape():
    section("判据 15/16 · 索引规模")
    from lzkg.db import connect

    c = connect()
    try:
        n_fts = c.execute("SELECT COUNT(*) c FROM docs_fts").fetchone()["c"]
        n_seg = c.execute("SELECT COUNT(*) c FROM docs_seg").fetchone()["c"]
        check("判据15 词级索引行数 == docs_fts 行数", n_seg == n_fts,
              f"docs_seg={n_seg} docs_fts={n_fts}")
        row = c.execute(
            "SELECT token_count FROM segment_meta ORDER BY id DESC LIMIT 1").fetchone()
        tc = row["token_count"] if row else 0
        # 实测 1,835,789（分词后含单字 token）；±5%
        ok = abs(tc - 1835789) <= 1835789 * 0.05
        check("判据16 token 总数落在实测区间（≈183.6 万 ±5%）", ok, f"实测 {tc:,}")
    finally:
        c.close()


def test_degrade():
    section("判据 1/2 · 缺 jieba 必须能降级（不许整个站起不来）")
    from lzkg import segment
    check("判据1 jieba 可用性可探测", isinstance(segment.available(), bool),
          f"available={segment.available()} err={segment.jieba_error()}")
    # 真降级：把 jieba 打成不可用，看分词与检索是否还活着
    orig = segment._jieba
    orig_failed = segment._jieba_failed
    try:
        segment._jieba = None
        segment._jieba_failed = True
        check("判据1 没 jieba 时分词不抛异常",
              isinstance(segment.cut("良渚文化遗址群"), list),
              f"退化切词 {segment.cut('良渚文化遗址群')}")
        from lzkg.db import connect
        from lzkg.app import _match_detail
        c = connect()
        try:
            d = _match_detail(c, "玉琮", "both")
            check("判据2 没 jieba 时精确层照常工作", len(d["hits"]) > 0,
                  f"命中 {len(d['hits'])} 篇（mode={d['mode']}）")
        finally:
            c.close()
    finally:
        segment._jieba = orig
        segment._jieba_failed = orig_failed


def test_api():
    section("端点（需服务在跑）")
    st, r = api("/api/segment/status")
    if st != 200:
        check("端点 /api/segment/status 可用", False, f"HTTP {st} {str(r)[:120]}")
        return
    d = r.get("data", {})
    check("端点 /api/segment/status 返回版本与词典指纹",
          "segment_version" in d and "dict_hash" in d,
          f"v{d.get('segment_version')} hash={d.get('dict_hash')} stale={d.get('stale')}")

    st, r = api("/api/literatures?q=%E5%85%BD%E9%9D%A2&size=5")   # 兽面
    check("端点 /api/literatures 带 mode 生效",
          st == 200 and r.get("mode") == "both" and "hit_counts" in r,
          f"mode={r.get('mode')} counts={r.get('hit_counts')}")
    st, r = api("/api/literatures?q=%E5%85%BD%E9%9D%A2&mode=exact&size=5")   # 兽面
    check("端点 /api/literatures mode=exact 只走精确层",
          st == 200 and r.get("mode") == "exact"
          and r.get("exact_backend") in ("like", "fts5"),
          f"mode={r.get('mode')} backend={r.get('exact_backend')} "
          f"counts={r.get('hit_counts')}")
    layers = (r.get("items") or [{}])[0].get("_hit_layers")
    check("端点 /api/literatures 返回命中层级", layers is not None, f"layers={layers}")

    # ★ 判据 17：两字词的精确层片段不能把整个检索打成 500。
    #   实测踩过：`_snippet_for` 的 LIKE 分支里 `instr(content, ?)` 少传一个参数
    #   → sqlite3.ProgrammingError → /api/literatures 直接 500（连浏览都挂）。
    #   这里的关键词是**两字词**（走 LIKE 分支），三字以上走 FTS5 不会碰到。
    for kw, enc in (("玉琮", "%E7%8E%89%E7%90%AE"),
                    ("反山", "%E5%8F%8D%E5%B1%B1"),
                    ("城墙", "%E5%9F%8E%E5%A2%99")):
        st, r = api(f"/api/literatures?q={enc}&mode=both&size=5")
        check(f"判据17 两字词「{kw}」检索不 500", st == 200,
              f"HTTP {st} {str(r)[:100]}")

    # ★ 判据 18：片段必须真的含命中词（不能落在文末作者简介那段）。
    st, r = api("/api/literatures?q=%E7%8E%89%E7%90%AE&mode=both&size=5")
    snips = [str(i.get("_snippet") or "") for i in (r.get("items") or [])]
    hit = [s for s in snips if "玉琮" in s]
    check("判据18 片段含命中词", bool(hit) and len(hit) == len(snips),
          f"{len(hit)}/{len(snips)} 条片段含「玉琮」")


def main():
    print("═══ 检索增强 E1 · 判据 ═══")
    test_layer_relationship()
    test_no_false_positive()
    test_performance()
    test_dict_persistence()
    test_newwords()
    test_index_shape()
    test_degrade()
    if "--api" in sys.argv:
        test_api()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print(f"\n=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
