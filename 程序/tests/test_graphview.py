# -*- coding: utf-8 -*-
"""图谱降密度 + 详情页地图标记的验收脚本。**可独立运行**。

    python tests/test_graphview.py            # 全跑（需要先启动服务）
    python tests/test_graphview.py --api      # 只跑 API 层

设计原则（照抄 test_all.py 的规矩）：
    **断言「产物合法」≠ 断言「产物正确」**
    所以每条判据都写成「必须等于某个实测真值」，而不是「不为空」。

★ 这里的真值都是**实测出来的**，不是拍脑袋写的：
    基线（600 节点 / 7179 边 / 全图坐标）：513 个（85%）最近邻距离 < 20px，
    中位 8.8px，最大度数 **263** → 必然糊成一团。
    降密后（cap=6）：505 节点 / 1012 边 / 最大度数 **12** / 孤点 **0** /
    最近邻中位 **14.316px** / 43 个标签。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM_ROOT = HERE.parent
sys.path.insert(0, str(PROGRAM_ROOT))

PASS, FAIL = 0, 0
FAILURES: list[str] = []

# ── 实测真值（2026-10-29 降密改造后）──────────────────────────────
TRUE_NODES = 505          # dense=true, kinds=lit,img,tag,objtype, limit=600
TRUE_EDGES = 1012
TRUE_MAX_DEG = 12         # == edge_cap * 2
TRUE_ISOLATED = 0
TRUE_PRUNED = 95          # 剔除的孤点数
TRUE_LABELS = 43
TRUE_COMMUNITIES = 20
TRUE_GAP_MIN = 12.0       # 最近邻中位距离下限（基线 8.8）
TRUE_SPREAD12_MIN = 150.0  # 前 12 个标签两两最近距中位下限
TRUE_SPREAD_ALL_MIN = 40.0
TRUE_RAW_EDGES = 7179     # 裁剪前的边数
TRUE_RAW_MAX_DEG = 263    # 裁剪前的最大度数


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


def api(path: str):
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}{path}"
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


# ══════════════════════════════════════════════════════════════════
# 1 · 模块层：裁边 / 孤点 / 布局 / 标签
# ══════════════════════════════════════════════════════════════════
def test_trim_and_prune():
    section("模块层 · 边裁剪与孤点")
    from lzkg.db import connect
    from lzkg.graph import load_graph, degree_map
    from lzkg import graphview as gv

    c = connect()
    try:
        nodes = {r["node_id"]: dict(r) for r in c.execute("SELECT * FROM graph_nodes")}
        edges = [dict(r) for r in c.execute("SELECT * FROM graph_edges")]
    finally:
        c.close()

    deg = degree_map(nodes, edges)
    top = sorted(nodes, key=lambda n: (-deg.get(n, 0), n))[:600]
    sn = {n: nodes[n] for n in top}
    se = [e for e in edges if e["src"] in sn and e["dst"] in sn]

    check("裁剪前的子图规模 = 实测真值",
          len(sn) == 600 and len(se) == TRUE_RAW_EDGES,
          f"{len(sn)} 节点 / {len(se)} 边（应 600 / {TRUE_RAW_EDGES}）")

    raw_max = max(gv.degree_map_local(sn, se).values())
    check("裁剪前最大度数 = 实测真值", raw_max == TRUE_RAW_MAX_DEG,
          f"实际 {raw_max}（应 {TRUE_RAW_MAX_DEG}）")

    kept = gv.trim_edges(sn, se, 6)
    kn, ke, dropped = gv.prune_isolated(sn, kept)

    check("裁剪后边数 ≤ 实测真值", len(ke) == TRUE_EDGES,
          f"实际 {len(ke)}（应 {TRUE_EDGES}）")
    check("裁剪后节点数 = 实测真值", len(kn) == TRUE_NODES,
          f"实际 {len(kn)}（应 {TRUE_NODES}）")
    check("剔除的孤点数 = 实测真值", len(dropped) == TRUE_PRUNED,
          f"实际 {len(dropped)}（应 {TRUE_PRUNED}）")

    d2 = gv.degree_map_local(kn, ke)
    check("裁剪后无孤点", sum(1 for v in d2.values() if v == 0) == TRUE_ISOLATED,
          f"实际孤点 {sum(1 for v in d2.values() if v == 0)}（应 {TRUE_ISOLATED}）")
    check("裁剪后最大度数 = cap×2", max(d2.values()) == TRUE_MAX_DEG,
          f"实际 {max(d2.values())}（应 {TRUE_MAX_DEG}）")
    check("最大度数比裁剪前降一个数量级",
          max(d2.values()) * 10 <= raw_max,
          f"{raw_max} → {max(d2.values())}")
    check("裁剪后没有重复边",
          len({(min(str(e['src']), str(e['dst'])), max(str(e['src']), str(e['dst'])))
               for e in ke}) == len(ke),
          f"{len(ke)} 条边 / {len({(min(str(e['src']), str(e['dst'])), max(str(e['src']), str(e['dst']))) for e in ke})} 对")


# ══════════════════════════════════════════════════════════════════
# 2 · 布局可复现（跨进程 + 换 PYTHONHASHSEED）
# ══════════════════════════════════════════════════════════════════
def _layout_probe(hashseed: str) -> str:
    """在指定 PYTHONHASHSEED 下算出「降密子图布局」的签名。

    ★ 为什么必须跨进程 + 换 hashseed：
      Python 对字符串的哈希**每进程随机化**，`G.subgraph(集合)` 按 set 的
      迭代顺序建图；spring_layout 把初始随机位置按节点顺序分配 ——
      同一份数据在不同进程里算出的坐标会不同。
      同进程里测两次永远是绿的，只在「重算一次对不上」才暴露。
    """
    code = (
        "import os, sys, hashlib;"
        "sys.path.insert(0, os.environ['LZKG_ROOT']);"
        "from lzkg.db import connect;"
        "from lzkg.graph import load_graph, degree_map;"
        "from lzkg import graphview as gv;"
        "c=connect(); nodes,edges=load_graph(c); c.close();"
        "deg=degree_map(nodes,edges);"
        "top=sorted(nodes,key=lambda n:(-deg.get(n,0),n))[:600];"
        "sn={n:nodes[n] for n in top};"
        "se=[e for e in edges if e['src'] in sn and e['dst'] in sn];"
        "kept=gv.trim_edges(sn,se,6);"
        "kn,ke,_=gv.prune_isolated(sn,kept);"
        "pos=gv.layout_subgraph(kn,ke);"
        "h=hashlib.sha256();"
        "parts=['%s:%.6f:%.6f|'%(k,pos[k][0],pos[k][1]) for k in sorted(pos)];"
        "h.update(''.join(parts).encode());"
        "print(h.hexdigest()[:16])"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["PYTHONIOENCODING"] = "utf-8"
    env["LZKG_ROOT"] = str(PROGRAM_ROOT)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=900)
    out = (r.stdout or "").strip()
    if out:
        return out.splitlines()[-1]
    return "ERR:" + ((r.stderr or "<no stderr>")[-300:]).replace("\n", " ")


def test_layout_reproducible():
    section("布局可复现（跨进程）")
    from lzkg.db import connect
    from lzkg.graph import load_graph, degree_map
    from lzkg import graphview as gv

    c = connect()
    try:
        nodes, edges = load_graph(c)
    finally:
        c.close()
    deg = degree_map(nodes, edges)
    top = sorted(nodes, key=lambda n: (-deg.get(n, 0), n))[:600]
    sn = {n: nodes[n] for n in top}
    se = [e for e in edges if e["src"] in sn and e["dst"] in sn]
    kept = gv.trim_edges(sn, se, 6)
    kn, ke, _ = gv.prune_isolated(sn, kept)
    pos1 = gv.layout_subgraph(kn, ke)
    pos2 = gv.layout_subgraph(kn, ke)
    same = all(abs(pos1[k][0] - pos2[k][0]) < 1e-12 and
               abs(pos1[k][1] - pos2[k][1]) < 1e-12 for k in pos1)
    check("同进程两次布局逐位相同", same and len(pos1) == TRUE_NODES,
          f"{len(pos1)} 个坐标")

    xs = [p[0] for p in pos1.values()]
    ys = [p[1] for p in pos1.values()]
    check("坐标在约定范围内 ±1000",
          all(-1000.5 <= v <= 1000.5 for v in xs + ys),
          f"x∈[{min(xs):.0f},{max(xs):.0f}] y∈[{min(ys):.0f},{max(ys):.0f}]")
    check("没有两个节点完全重合",
          len({(round(p[0], 6), round(p[1], 6)) for p in pos1.values()}) == len(pos1),
          f"{len(pos1)} 个坐标")

    a = _layout_probe("0")
    b = _layout_probe("12345")
    check("换 PYTHONHASHSEED 结果不变（跨进程可复现）",
          a == b and not a.startswith("ERR"),
          f"seed0={a} seed12345={b}")


# ══════════════════════════════════════════════════════════════════
# 3 · 标签：轮转覆盖各社区 + 散布指标
# ══════════════════════════════════════════════════════════════════
def test_labels():
    section("标签候选与散布")
    from lzkg.db import connect
    from lzkg.graph import load_graph, degree_map
    from lzkg import graphview as gv
    import networkx as nx

    c = connect()
    try:
        nodes, edges = load_graph(c)
    finally:
        c.close()
    deg = degree_map(nodes, edges)
    top = sorted(nodes, key=lambda n: (-deg.get(n, 0), n))[:600]
    sn = {n: nodes[n] for n in top}
    se = [e for e in edges if e["src"] in sn and e["dst"] in sn]
    kept = gv.trim_edges(sn, se, 6)
    kn, ke, _ = gv.prune_isolated(sn, kept)
    pos = gv.layout_subgraph(kn, ke)
    labels = gv.label_candidates(kn, ke)

    check("标签数 = 实测真值", len(labels) == TRUE_LABELS,
          f"实际 {len(labels)}（应 {TRUE_LABELS}）")
    check("标签不重复", len(set(labels)) == len(labels), f"{len(set(labels))} 个不重复")
    check("标签节点都在子图里", all(x in kn for x in labels), "")

    G = gv.build_subgraph(kn, ke)
    comms = nx.community.louvain_communities(G, seed=42)
    cid: dict[str, int] = {}
    for i, cc in enumerate(comms):
        for n in cc:
            cid[n] = i
    check("社区数 = 实测真值", len(comms) == TRUE_COMMUNITIES,
          f"实际 {len(comms)}（应 {TRUE_COMMUNITIES}）")

    cov12 = len({cid.get(x) for x in labels[:12]})
    cov24 = len({cid.get(x) for x in labels[:24]})
    check("前 12 个标签覆盖 12 个社区（轮转生效）", cov12 == 12,
          f"实际覆盖 {cov12} 个社区（应 12）")
    check("前 24 个标签覆盖全部社区", cov24 == TRUE_COMMUNITIES,
          f"实际覆盖 {cov24}（应 {TRUE_COMMUNITIES}）")

    s12 = gv.spread(kn, pos, labels[:12])
    sall = gv.spread(kn, pos, labels)
    check("前 12 个标签散布 ≥ 实测下限", s12 >= TRUE_SPREAD12_MIN,
          f"实测 {s12:.2f}px（下限 {TRUE_SPREAD12_MIN}）")
    check("全部标签散布 ≥ 实测下限", sall >= TRUE_SPREAD_ALL_MIN,
          f"实测 {sall:.2f}px（下限 {TRUE_SPREAD_ALL_MIN}）")

    gap = gv.nearest_gap(kn, pos)
    check("最近邻中位距离 ≥ 实测下限（比基线 8.8px 更疏）", gap >= TRUE_GAP_MIN,
          f"实测 {gap:.3f}px（下限 {TRUE_GAP_MIN}，基线 8.8）")


# ══════════════════════════════════════════════════════════════════
# 4 · 前端：标签分级 / 渐进披露 / scale 兜底
# ══════════════════════════════════════════════════════════════════
def test_frontend():
    section("前端 · 图谱降密与标签分级")
    p = PROGRAM_ROOT / "lzkg" / "web" / "static" / "index.html"
    s = p.read_text(encoding="utf-8")

    check("标签渐进披露三档（12 / 24 / 全部）",
          '(sc < 0.35) ? 12 : (sc < 0.8 ? 24 : total)' in s,
          "applyLabelScale 的 quota 档位")
    check("标签原值另存 full_label（隐藏后放得回来）",
          "full_label" in s and "n.full_label || n.label" in s,
          "渐进披露不能把标签永久清空")
    check("scale 轮询兜底（程序化缩放不触发 zoom 事件）",
          "scaleTimer" in s and "getScale()" in s,
          "moveTo({scale}) 不触发 zoom → 必须有轮询")
    check("边淡色 + 无平滑",
          'color: "#c3d0d9"' in s and "smooth: { enabled: false }" in s, "")
    check("节点按类型分级大小",
          '(n.kind === "lit") ? 15 : (n.kind === "img" ? 8' in s, "")
    check("标签白描边（压住底下的线）",
          'strokeWidth: 3, strokeColor: "#ffffff"' in s, "")
    check("详情页内嵌小地图容器存在", 'id="litmap"' in s and 'id="litmap-txt"' in s, "")
    check("四档精度标签在界面上写明",
          'lz-tier lit_coord' in s and 'lz-tier public' in s and
          'lz-tier region' in s and 'lz-tier none' in s, "")
    check("离开详情页拆掉 Leaflet 实例", "litmapDestroy()" in s, "")


# ══════════════════════════════════════════════════════════════════
# 5 · API 层
# ══════════════════════════════════════════════════════════════════
def test_api():
    section("API · /api/graph")
    d = api("/api/graph?kinds=lit,img,tag,objtype&limit=600")
    st = d.get("stats") or {}
    nodes = d.get("nodes") or []
    edges = d.get("edges") or []

    check("API 返回节点数 = 实测真值", len(nodes) == TRUE_NODES,
          f"实际 {len(nodes)}（应 {TRUE_NODES}）")
    check("API 返回边数 = 实测真值", len(edges) == TRUE_EDGES,
          f"实际 {len(edges)}（应 {TRUE_EDGES}）")
    check("stats.edge_cap = 6", st.get("edge_cap") == 6, str(st.get("edge_cap")))
    check("stats.dense = true", st.get("dense") is True, str(st.get("dense")))
    check("stats.raw_edges = 裁剪前边数", st.get("raw_edges") == TRUE_RAW_EDGES,
          str(st.get("raw_edges")))
    check("stats.pruned_isolated = 剔除孤点数",
          st.get("pruned_isolated") == TRUE_PRUNED, str(st.get("pruned_isolated")))
    check("label_nodes 数 = 实测真值", len(d.get("label_nodes") or []) == TRUE_LABELS,
          str(len(d.get("label_nodes") or [])))
    check("label_nodes 前 12 个互不相同（覆盖不同社区）",
          len(set((d.get("label_nodes") or [])[:12])) == 12, "")

    shown = [n for n in nodes if n.get("show_label")]
    check("只有 label_nodes 里的节点 show_label=true",
          set(n["id"] for n in shown) == set(d.get("label_nodes") or []),
          f"{len(shown)} 个")
    check("所有节点都有 x/y（后端算好坐标，前端 physics:false）",
          all(isinstance(n.get("x"), (int, float)) and
              isinstance(n.get("y"), (int, float)) for n in nodes),
          f"{len(nodes)} 个节点")

    raw = api("/api/graph?kinds=lit,img,tag,objtype&limit=600&dense=false")
    check("dense=false 保留旧行为（全边）",
          len(raw.get("edges") or []) == TRUE_RAW_EDGES,
          str(len(raw.get("edges") or [])))


def test_api_litmap():
    section("API · /api/literatures/{id}/map（地图标记分档）")
    d = api("/api/literatures/LIT-000127/map")
    check("文献坐标档：本篇即出处",
          d.get("level") == "lit_coord" and
          d["markers"][0].get("from_this_paper") is True,
          f"level={d.get('level')}")
    tiers = [m["tier"] for m in d.get("markers") or []]
    check("三档标记齐全（文献坐标 / 公开权威 / 分区代表点）",
          tiers == ["lit_coord", "public", "region"], str(tiers))
    check("每条标记都带精度说明",
          all(m.get("precision") for m in d.get("markers") or []), "")
    check("每条标记都带命中依据",
          all(m.get("why") for m in d.get("markers") or []), "")

    d2 = api("/api/literatures/LIT-000006/map")
    check("无坐标的文献报「未定位」且不编造标记",
          d2.get("level") == "none" and not (d2.get("markers") or []),
          f"level={d2.get('level')}")

    d3 = api("/api/literatures/LIT-000122/map")
    check("公开权威坐标单独成档",
          d3.get("level") == "public" and
          d3["markers"][0]["tier"] == "public",
          f"level={d3.get('level')}")


def main():
    # ★ 没有库就只跑**纯代码**的判据（前端分级 / 渐进披露 / scale 兜底）——
    #   公开仓库不带数据库（CNKI 机构订阅全文不能公开外传），克隆者要先 migrate。
    try:
        from lzkg.config import get_config
        has_db = get_config().db_path.exists()
    except Exception:
        has_db = False
    if has_db:
        test_trim_and_prune()
        test_layout_reproducible()
        test_labels()
    else:
        print("\n  ⚠ 没有找到数据库（程序/data/lzkg.db）——跳过依赖数据的判据。")
        print("    先建库：python -m lzkg set-source \"<你的 04_专题研究>\" && python -m lzkg migrate")
    test_frontend()
    if "--api" in sys.argv or "--all" in sys.argv:
        if has_db:
            test_api()
            test_api_litmap()
        else:
            print("\n  SKIP API 判据：需要先建库并启动服务")
    print(f"\n{'=' * 46}")
    print(f"通过 {PASS}　失败 {FAIL}")
    if FAILURES:
        print("\n失败明细：")
        for f in FAILURES:
            print("  · " + f)
    print("=" * 46)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
