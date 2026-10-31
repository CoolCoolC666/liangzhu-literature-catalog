# -*- coding: utf-8 -*-
"""验收脚本：收藏页的**两层检索**（题名/备注 + 全文）。

    python tests/test_favsearch.py          # 需要先启动服务
    python tests/test_favsearch.py --api    # 同上（显式）

设计原则（沿用本项目的一贯做法）：
    **断言「产物合法」≠ 断言「产物正确」**
    所以每条判据都写成「必须等于某个实测真值」，不是「不为空」。

★ 本脚本**只读**收藏集合；若本机一条收藏都没有，会临时收藏一篇并在结束时取消
  （可逆，且只动自己的收藏，不碰主库文献数据）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM_ROOT = HERE.parent
sys.path.insert(0, str(PROGRAM_ROOT))

PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK   {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}")
        print(f"  FAIL {name}  {detail}")


def section(t):
    print(f"\n=== {t} ===")


def api(path: str, method: str = "GET", body=None):
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}


def favs(t: str = "", q: str = ""):
    ps = []
    if t:
        ps.append("t=" + urllib.parse.quote(t))
    if q:
        ps.append("q=" + urllib.parse.quote(q))
    path = "/api/favorites" + ("?" + "&".join(ps) if ps else "")
    r = api(path)
    return (r.get("data") or {})


def cn_run(s: str, n: int) -> str:
    """从字符串里取一段连续的 n 个汉字（用来当检索词）。"""
    runs = re.findall(r"[\u4e00-\u9fff]{%d,}" % n, s or "")
    for r in runs:
        if len(r) >= n:
            return r[:n]
    return ""


# ══════════════════════════════════════════════════════════════════
# 1 · 后端：两层检索的行为
# ══════════════════════════════════════════════════════════════════
def test_layers():
    section("后端 · 收藏两层检索")
    d = favs()
    items = d.get("items") or []
    total = d.get("total")
    check("默认返回带 total 与 matched",
          total is not None and d.get("matched") is not None,
          f"total={total} matched={d.get('matched')}")
    check("无参数时 matched == len(items) == total",
          d.get("matched") == len(items) == total,
          f"items={len(items)} matched={d.get('matched')} total={total}")
    check("无参数时 mode 为空（没走检索）", d.get("mode") in ("", None), f"mode={d.get('mode')!r}")

    need = ["lit_id", "title", "note", "created_at", "year", "authors", "venue",
            "keywords", "abstract", "open_type", "doc_type", "snippet", "matched"]
    missing = {}
    for it in items:
        for k in need:
            if k not in it:
                missing.setdefault(k, it.get("lit_id"))
    check("每条字段齐全（title/year/authors/venue/keywords/abstract/snippet/matched…）",
          not missing, f"缺字段={missing}")
    check("matched 取值合法（''/title_note/full/both）",
          all(it.get("matched") in ("", "title_note", "full", "both") for it in items),
          f"取值={sorted({it.get('matched') for it in items})}")

    if not items:
        print("  ⚠ 本机没有收藏，跳过依赖收藏内容的两条判据（t 筛选 / 全文命中）")
        return items, ""

    # ── 第一层：题名/备注 ───────────────────────────────────────
    target = items[0]
    t_word = cn_run(target.get("title", "") or target.get("note", ""), 2)
    if t_word:
        r = favs(t=t_word)
        ids = [i["lit_id"] for i in (r.get("items") or [])]
        check("题名/备注层：命中词能筛出该条", target["lit_id"] in ids,
              f"词={t_word!r} ids={ids}")
        check("题名/备注层：结果条数不超过收藏总数",
              (r.get("matched") or 0) <= (total or 0),
              f"matched={r.get('matched')} total={total}")
        check("题名/备注层：matched 标记为 title_note 或 both",
              any(i.get("matched") in ("title_note", "both") for i in (r.get("items") or [])),
              f"取值={sorted({i.get('matched') for i in (r.get('items') or [])})}")
    else:
        print("  ⚠ 收藏的题名里没有足够的汉字，跳过题名/备注层判据")

    r = favs(t="zzz查无此词zzz")
    check("题名/备注层：不存在的词 → 0 条（不是报错）",
          r.get("matched") == 0 and not (r.get("items") or []),
          f"matched={r.get('matched')}")

    # ── 第二层：全文（>=3 字走 trigram FTS5）────────────────────
    q_word = cn_run(target.get("title", ""), 4)
    if q_word:
        r = favs(q=q_word)
        ids = [i["lit_id"] for i in (r.get("items") or [])]
        check("全文层：标题里的 4 字词能命中该条", target["lit_id"] in ids,
              f"词={q_word!r} ids={ids}")
        check("全文层：mode 标出检索方式（fts5/like）",
              r.get("mode") in ("fts5", "like"), f"mode={r.get('mode')!r}")
        sn = ""
        for i in (r.get("items") or []):
            if i["lit_id"] == target["lit_id"]:
                sn = i.get("snippet") or ""
        check("全文层：命中片段带 <mark> 高亮", "<mark>" in sn, f"snippet={sn[:60]!r}")

    # ── 第二层：全文（2 字走 LIKE 降级）→ 片段必须含命中词 ────
    #   ★ 这条钉死一个既有 bug：substr 起点写成 max(instr(...),1)-40 会算出负数，
    #     片段落到**文末**（作者简介），命中处根本不在片段里。
    q2 = cn_run(target.get("title", ""), 2)
    if q2:
        r = favs(q=q2)
        items2 = r.get("items") or []
        ids = [i["lit_id"] for i in items2]
        check("全文层：2 字词走 LIKE 降级仍能命中该条", target["lit_id"] in ids,
              f"词={q2!r} mode={r.get('mode')!r} ids={ids}")
        sn2 = ""
        for i in items2:
            if i["lit_id"] == target["lit_id"]:
                sn2 = i.get("snippet") or ""
        check("全文层：LIKE 分支的片段**含命中词**（不是文末作者简介）",
              q2 in sn2, f"词={q2!r} snippet={sn2[:80]!r}")

    # ── 关键正确性：不得翻出没收藏的文献 ───────────────────────
    fav_ids = {i["lit_id"] for i in items}
    outside = None
    for lid in ["LIT-000001", "LIT-000002", "LIT-000003", "LIT-000100", "LIT-000199"]:
        if lid not in fav_ids:
            outside = lid
            break
    if outside:
        r = api(f"/api/literatures/{outside}")
        lit = (r.get("data") or {})
        w = cn_run(lit.get("title", ""), 4)
        if w:
            r = favs(q=w)
            ids = [i["lit_id"] for i in (r.get("items") or [])]
            check("★ 全文检索不得返回未收藏的文献",
                  outside not in ids and all(i in fav_ids for i in ids),
                  f"未收藏={outside} 词={w!r} 结果={ids}")
    else:
        print("  ⚠ 收藏里已包含候选文献，跳过「不泄漏未收藏文献」判据")

    # ── 两层是 AND 关系 ────────────────────────────────────────
    if len(items) >= 2:
        a = cn_run(items[0].get("title", ""), 2)
        b = cn_run(items[1].get("title", ""), 4)
        if a and b:
            r = favs(t=a, q=b)
            ids = [i["lit_id"] for i in (r.get("items") or [])]
            check("两层是 AND：分别命中两条的词组合起来 → 0 条",
                  len(ids) == 0, f"t={a!r} q={b!r} 结果={ids}")
            # 反向：两个词命中同一条 → 仍能命中
            a1 = cn_run(items[0].get("title", ""), 2)
            b1 = cn_run(items[0].get("title", ""), 4)
            if a1 and b1:
                r = favs(t=a1, q=b1)
                ids = [i["lit_id"] for i in (r.get("items") or [])]
                check("两层是 AND：两个词都命中同一条 → 该条仍在",
                      items[0]["lit_id"] in ids, f"t={a1!r} q={b1!r} 结果={ids}")
                m = ""
                for i in (r.get("items") or []):
                    if i["lit_id"] == items[0]["lit_id"]:
                        m = i.get("matched")
                check("两层都命中时 matched = both", m == "both", f"matched={m!r}")
    else:
        print("  ⚠ 收藏不足 2 条，跳过「两层 AND」判据")

    # ── 检索不写库 ─────────────────────────────────────────────
    after = favs()
    check("检索不改变收藏总数（只读）",
          after.get("total") == total, f"before={total} after={after.get('total')}")

    return items, q2 or ""


# ══════════════════════════════════════════════════════════════════
# 2 · 前端源码级：检索控件与高亮处理
# ══════════════════════════════════════════════════════════════════
def test_frontend():
    section("前端 · 收藏检索控件")
    html = (PROGRAM_ROOT / "lzkg" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    check("有题名/备注输入框（fav-t）", 'id="fav-t"' in html)
    check("有全文关键词输入框（fav-q）", 'id="fav-q"' in html)
    check("有检索按钮（fav-go）与清空按钮（fav-clear）",
          'id="fav-go"' in html and 'id="fav-clear"' in html)
    check("检索走带参请求（api(\"/api/favorites\", params)）",
          'api("/api/favorites", params)' in html)
    check("片段高亮先转义再放回 <mark>（escMark）",
          "function escMark(" in html and "&lt;mark&gt;" in html)
    check("命中层级有中文标签",
          "FAV_MATCH_LABEL" in html and "题名/备注命中" in html)
    check("列表显示年份/刊名/作者等定位信息",
          "it.year" in html and "it.venue" in html and "it.authors" in html)
    # 前端不许自己解析 Markdown 之外的东西：确认收藏页不引外链脚本
    check("收藏页未引入任何 CDN 外链", "cdn.jsdelivr" not in html and "unpkg.com" not in html)


def main():
    try:
        from lzkg.config import get_config
        cfg = get_config()
    except Exception as e:
        print(f"读取配置失败：{e}")
        sys.exit(2)

    r = api("/api/favorites")
    if not r.get("ok"):
        print(f"⚠ 取不到收藏列表（{r.get('error')}）——请先启动服务（python -m lzkg serve）")
        sys.exit(2)

    # 收藏不足 2 条时**临时**补一条（只动自己的收藏、可逆），
    # 否则「两层是 AND」那条判据永远跑不到。
    tmp_added = None
    have = [i["lit_id"] for i in ((r.get("data") or {}).get("items") or [])]
    if len(have) < 2:
        for lid in ["LIT-000002", "LIT-000001", "LIT-000003", "LIT-000004"]:
            if lid not in have:
                api(f"/api/favorites/{lid}", "POST", {"note": ""})
                tmp_added = lid
                print(f"  （临时收藏 {lid} 以便测试「两层 AND」，测完自动撤销）")
                break

    try:
        test_layers()
        test_frontend()
    finally:
        if tmp_added:
            api(f"/api/favorites/{tmp_added}", "DELETE")
            print(f"  （已撤销临时收藏 {tmp_added}）")

    print(f"\n{'=' * 46}")
    print(f"通过 {PASS}　失败 {FAIL}")
    if FAILURES:
        print("\n失败明细：")
        for f in FAILURES:
            print("  -", f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
