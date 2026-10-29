# -*- coding: utf-8 -*-
"""图谱：L1 表派生（零抽取）+ 图分析。

图谱构成
--------
节点 4 类：
    lit      文献        lit:LIT-000001
    img      图像        img:IMG-LIT-000001-004
    tag      标签        tag:region:R-0101 良渚古城
    objtype  图像对象类型 objtype:IMGOBJ-SITE｜遗址
关系 5 类：
    contains  文献→图像      （登记表「关联文献标识符」）
    depicts   图像→对象类型   （登记表「图像对象类型代码」）
    tagged    文献→标签      （登记表四维标签）
    imgtag    图像→标签      （登记表图像级四维标签）
    cocite    文献↔文献      （共享同一标签）

**全部来自登记表 JOIN，零抽取、零误判。**

三条从 cacx 踩过的坑里继承的规矩
--------------------------------
1. **筛选后的指标必须在筛选后的范围重算**：分析与画图共用同一个 `crop_scope()`，
   否则「排名表跑全图、图画子图」，两个数差好几倍。
2. **Louvain 必须锁 seed=42**：不锁每次社区划分都不同，截图对不上、报告数字不可复现。
3. **连通块数不等于质量**：同时给出「关系覆盖 X/Y 篇 = Z%」，避免把图的样子当现实的样子。
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Iterable

REL_LABELS = {
    "contains": "文献包含图像",
    "depicts": "图像描绘对象",
    "tagged": "文献标注标签",
    "imgtag": "图像标注标签",
    "cocite": "共享标签",
}

# cocite 只用这些维度（period/topic/lineage 太泛时会让图变成一团）
COCITE_DIMS = ("region", "topic", "lineage", "period")
# 单个标签参与共现的最大文献数（超过说明这个标签太泛，共现边没有信息量）
COCITE_MAX_LIT = 40
# 共现边总数上限
COCITE_MAX_EDGES = 4000


def build_graph_into_db(conn: sqlite3.Connection) -> dict:
    """把 L1 图谱写进 graph_nodes / graph_edges。"""
    conn.execute("DELETE FROM graph_nodes")
    conn.execute("DELETE FROM graph_edges")

    nodes: dict[str, tuple] = {}
    edges: list[tuple] = []

    lits = conn.execute(
        "SELECT lit_id, title, doc_type, period_tags, region_tags, topic_tags, lineage_tags "
        "FROM literatures").fetchall()
    imgs = conn.execute(
        "SELECT img_id, lit_id, filename, fig_no, caption, img_type, obj_type "
        "FROM images").fetchall()
    tags = conn.execute("SELECT tag_id, dim, name, code, lit_count, img_count FROM tags").fetchall()

    # ── 节点：文献 ──────────────────────────────────────────────
    img_by_lit: dict[str, int] = defaultdict(int)
    for im in imgs:
        if im["lit_id"]:
            img_by_lit[im["lit_id"]] += 1
    for l in lits:
        nid = f"lit:{l['lit_id']}"
        label = (l["title"] or l["lit_id"]).strip()
        if len(label) > 48:
            label = label[:47] + "…"
        nodes[nid] = (nid, "lit", label, l["lit_id"], 1 + img_by_lit.get(l["lit_id"], 0))

    # ── 节点：图像 ──────────────────────────────────────────────
    for im in imgs:
        nid = f"img:{im['img_id']}"
        cap = (im["caption"] or "").strip()
        label = (im["fig_no"] or "").strip()
        if not label and cap:
            label = cap[:40] + ("…" if len(cap) > 40 else "")
        if not label:
            label = im["img_id"]
        nodes[nid] = (nid, "img", label, im["lit_id"], 1)

    # ── 节点：标签 / 对象类型 ───────────────────────────────────
    for t in tags:
        nid = f"tag:{t['tag_id']}"
        nodes[nid] = (nid, "tag", t["name"] or t["tag_id"], "", (t["lit_count"] or 0) + (t["img_count"] or 0))
    objtypes = conn.execute(
        "SELECT DISTINCT obj_type FROM images WHERE obj_type IS NOT NULL AND obj_type<>''").fetchall()
    for r in objtypes:
        raw = r["obj_type"]
        code, _, name = raw.partition("｜")
        name = name or code
        nid = f"objtype:{code}"
        nodes[nid] = (nid, "objtype", name, "", 1)

    # ── 边 ────────────────────────────────────────────────────
    for im in imgs:
        if not im["lit_id"]:
            continue
        src, dst = f"lit:{im['lit_id']}", f"img:{im['img_id']}"
        if src in nodes and dst in nodes:
            edges.append((src, dst, "contains", im["lit_id"], 1))
        if im["obj_type"]:
            code = im["obj_type"].partition("｜")[0]
            dst = f"objtype:{code}"
            if dst in nodes:
                edges.append((f"img:{im['img_id']}", dst, "depicts", im["lit_id"], 1))

    for row in conn.execute("SELECT lit_id, tag_id, dim FROM lit_tags").fetchall():
        src, dst = f"lit:{row['lit_id']}", f"tag:{row['tag_id']}"
        if src in nodes and dst in nodes:
            edges.append((src, dst, "tagged", row["lit_id"], 1))
    for row in conn.execute(
            "SELECT tt.img_id, tt.tag_id, i.lit_id FROM img_tags tt "
            "JOIN images i ON i.img_id = tt.img_id").fetchall():
        src, dst = f"img:{row['img_id']}", f"tag:{row['tag_id']}"
        if src in nodes and dst in nodes:
            edges.append((src, dst, "imgtag", row["lit_id"] or "", 1))

    # ── 边：共现（共享标签）─────────────────────────────────────
    lit_by_tag: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute("SELECT lit_id, tag_id, dim FROM lit_tags").fetchall():
        if row["dim"] in COCITE_DIMS:
            lit_by_tag[row["tag_id"]].append(row["lit_id"])
    seen_pairs: set[tuple] = set()
    cocite_edges = 0
    for tag_id, lst in lit_by_tag.items():
        uniq = sorted(set(lst))
        if not (2 <= len(uniq) <= COCITE_MAX_LIT):
            continue
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                pair = (a, b)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if cocite_edges >= COCITE_MAX_EDGES:
                    break
                edges.append((f"lit:{a}", f"lit:{b}", "cocite", a, 1))
                cocite_edges += 1

    conn.executemany("INSERT OR REPLACE INTO graph_nodes (node_id, kind, label, lit_id, weight) VALUES (?,?,?,?,?)",
                     list(nodes.values()))
    conn.executemany("INSERT OR REPLACE INTO graph_edges (src, dst, rel, lit_id, weight) VALUES (?,?,?,?,?)", edges)
    conn.commit()
    return {"nodes": len(nodes), "edges": len(edges), "cocite_edges": cocite_edges}


# ── 查询与裁剪 ──────────────────────────────────────────────────────────
def load_graph(conn: sqlite3.Connection, rels: Iterable[str] | None = None):
    """读全图，返回 (nodes:dict, edges:list)。

    ★ **必须显式 ORDER BY**。
      没有 ORDER BY 时，SQLite 会在「走 node_id 索引」和「走表扫描(rowid)」之间
      按统计信息自行选择 —— 而 Louvain 社区划分和 spring_layout **都对节点插入
      顺序敏感**。实测：同一份库、同一个 seed，只是 UPDATE 了几行让优化器换了
      扫描策略，社区就从 9 个变成 8 个，布局坐标最大差 191。
      这类差异不报错、测试在同进程里也是绿的，只在「重算一次」后对不上截图。
      所以这里两条查询都写死 ORDER BY，让结果只取决于数据内容。
    """
    rels = list(rels) if rels else None
    nodes = {}
    for r in conn.execute(
            "SELECT node_id, kind, label, lit_id, weight, x, y FROM graph_nodes ORDER BY node_id"):
        nodes[r["node_id"]] = dict(r)
    edges = []
    q = "SELECT src, dst, rel, lit_id, weight FROM graph_edges"
    params: list = []
    if rels:
        q += f" WHERE rel IN ({','.join('?' * len(rels))})"
        params = rels
    q += " ORDER BY src, dst, rel"
    for r in conn.execute(q, params):
        edges.append(dict(r))
    return nodes, edges


def degree_map(nodes: dict, edges: list) -> dict[str, int]:
    deg = {n: 0 for n in nodes}
    for e in edges:
        if e["src"] in deg:
            deg[e["src"]] += 1
        if e["dst"] in deg:
            deg[e["dst"]] += 1
    return deg


def crop_scope(nodes: dict, edges: list, keyword: str = "", focus: str = "",
               hop: int = 0) -> tuple[dict, list]:
    """裁剪子图：先按关键词，再按聚焦+跳数。

    ★ 画图与分析**必须共用这个函数的输出**，否则会出现「排名表跑全图、
    画的是子图」的两个口径打架。
    """
    keep_nodes = set(nodes)
    keep_edges = list(edges)

    if keyword:
        kw = keyword.strip()
        matched = {n for n, v in nodes.items() if kw in (v["label"] or "")}
        # 保留命中节点及其直接邻居（否则只剩孤立点）
        if matched:
            nb = set()
            for e in keep_edges:
                if e["src"] in matched:
                    nb.add(e["dst"])
                if e["dst"] in matched:
                    nb.add(e["src"])
            keep_nodes = matched | nb

    if focus and hop > 0:
        adj: dict[str, set] = defaultdict(set)
        for e in keep_edges:
            adj[e["src"]].add(e["dst"])
            adj[e["dst"]].add(e["src"])
        seen = {focus} if focus in nodes else set()
        frontier = set(seen)
        for _ in range(int(hop)):
            nxt = set()
            for n in frontier:
                nxt |= adj.get(n, set())
            nxt -= seen
            seen |= nxt
            frontier = nxt
        keep_nodes = keep_nodes & seen

    fn = {n: v for n, v in nodes.items() if n in keep_nodes}
    fe = [e for e in keep_edges if e["src"] in fn and e["dst"] in fn]
    return fn, fe


def build_networkx(nodes: dict, edges: list):
    """建 networkx 图。

    ★ 节点与边都**按 key 排序**后插入：Louvain 与 spring_layout 都对插入顺序
      敏感（见 load_graph 的说明）。排序后，同样的数据内容必然得到同样的图，
      无论上游传进来的 dict 顺序如何。
    """
    import networkx as nx
    G = nx.Graph()
    for nid in sorted(nodes):
        G.add_node(nid, **nodes[nid])
    for e in sorted(edges, key=lambda e: (e["src"], e["dst"], e["rel"])):
        if G.has_edge(e["src"], e["dst"]):
            G[e["src"]][e["dst"]]["weight"] = G[e["src"]][e["dst"]].get("weight", 0) + e["weight"]
        else:
            G.add_edge(e["src"], e["dst"], **{"weight": e["weight"], "rel": e["rel"], "lit_id": e["lit_id"]})
    return G


def analyze(nodes: dict, edges: list) -> dict:
    """图指标。**入参必须是已裁剪的子图**（口径见 crop_scope 的说明）。"""
    import networkx as nx
    G = build_networkx(nodes, edges)
    out: dict = {"degree_top": [], "betweenness_top": [], "communities": [],
                 "components": 0, "max_component": 0}
    if G.number_of_nodes() == 0:
        return out
    deg = dict(G.degree())
    for nid, d in sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
        out["degree_top"].append({"id": nid, "label": nodes.get(nid, {}).get("label", nid), "degree": d})
    try:
        btw = nx.betweenness_centrality(G, k=min(200, max(G.number_of_nodes() - 1, 1)), seed=42)
        for nid, b in sorted(btw.items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
            out["betweenness_top"].append(
                {"id": nid, "label": nodes.get(nid, {}).get("label", nid), "betweenness": round(b, 6)})
    except Exception:
        pass
    try:
        comms = nx.community.louvain_communities(G, seed=42)   # ★ seed 固定
        comms = sorted(comms, key=len, reverse=True)
        for i, c in enumerate(comms[:20]):
            labels = [nodes.get(n, {}).get("label", n) for n in list(c)[:8]]
            out["communities"].append({"cid": i, "size": len(c), "members": len(c),
                                       "sample_labels": labels})
    except Exception:
        pass
    comps = list(nx.connected_components(G))
    out["components"] = len(comps)
    out["max_component"] = max((len(c) for c in comps), default=0)
    return out


def evidence_for(conn: sqlite3.Connection, src: str, dst: str, rel: str) -> list[dict]:
    """边的证据：每条都要能点回原始文献。"""
    out: list[dict] = []
    if rel == "contains":
        img_id = dst.split(":", 1)[1]
        r = conn.execute(
            "SELECT i.*, l.title AS lit_title FROM images i "
            "LEFT JOIN literatures l ON l.lit_id = i.lit_id WHERE i.img_id = ?", (img_id,)).fetchone()
        if r:
            out.append({"lit_id": r["lit_id"], "title": r["lit_title"], "path": r["path"],
                        "evidence": (r["caption"] or r["img_title"] or ""),
                        "fig_no": r["fig_no"], "pdf_page": r["pdf_page"],
                        "caption": r["caption"], "img_type": r["img_type"], "obj_type": r["obj_type"]})
    elif rel == "depicts":
        img_id = src.split(":", 1)[1]
        r = conn.execute(
            "SELECT i.*, l.title AS lit_title FROM images i "
            "LEFT JOIN literatures l ON l.lit_id = i.lit_id WHERE i.img_id = ?", (img_id,)).fetchone()
        if r:
            out.append({"lit_id": r["lit_id"], "title": r["lit_title"], "path": r["path"],
                        "evidence": f"对象类型：{r['obj_type']}",
                        "fig_no": r["fig_no"], "pdf_page": r["pdf_page"], "caption": r["caption"]})
    elif rel in ("tagged", "imgtag"):
        tag_id = dst.split(":", 1)[1]
        t = conn.execute("SELECT * FROM tags WHERE tag_id = ?", (tag_id,)).fetchone()
        if t:
            if rel == "tagged":
                rows = conn.execute(
                    "SELECT l.lit_id, l.title, l.period_tags, l.region_tags, l.topic_tags, l.lineage_tags "
                    "FROM lit_tags tt JOIN literatures l ON l.lit_id = tt.lit_id WHERE tt.tag_id = ? LIMIT 50",
                    (tag_id,)).fetchall()
                dim = conn.execute("SELECT dim FROM lit_tags WHERE tag_id=? LIMIT 1", (tag_id,)).fetchone()
                col = {"period": "period_tags", "region": "region_tags",
                       "topic": "topic_tags", "lineage": "lineage_tags"}.get(
                           dim["dim"] if dim else "", "topic_tags")
                for r in rows:
                    out.append({"lit_id": r["lit_id"], "title": r["title"], "path": "",
                                "evidence": f"{t['name']} ← 登记表原文：{r[col]}",
                                "fig_no": "", "pdf_page": "", "caption": t["name"]})
            else:
                rows = conn.execute(
                    "SELECT i.*, l.title AS lit_title FROM img_tags tt "
                    "JOIN images i ON i.img_id = tt.img_id "
                    "LEFT JOIN literatures l ON l.lit_id = i.lit_id WHERE tt.tag_id = ? LIMIT 50",
                    (tag_id,)).fetchall()
                for r in rows:
                    out.append({"lit_id": r["lit_id"], "title": r["lit_title"], "path": r["path"],
                                "evidence": f"{t['name']}（图像级标注）",
                                "fig_no": r["fig_no"], "pdf_page": r["pdf_page"], "caption": r["caption"]})
    elif rel == "cocite":
        a = src.split(":", 1)[1]
        b = dst.split(":", 1)[1]
        rows = conn.execute(
            "SELECT a.lit_id, a.title, b.lit_id AS other_id, b.title AS other_title "
            "FROM literatures a JOIN literatures b ON b.lit_id = ? WHERE a.lit_id = ?", (b, a)).fetchall()
        shared = conn.execute(
            "SELECT t.name FROM tags t WHERE t.tag_id IN ("
            " SELECT tag_id FROM lit_tags WHERE lit_id=? INTERSECT SELECT tag_id FROM lit_tags WHERE lit_id=?)",
            (a, b)).fetchall()
        names = "、".join(s["name"] for s in shared[:8])
        for r in rows:
            out.append({"lit_id": r["lit_id"], "title": r["title"], "path": "",
                        "evidence": f"与《{r['other_title']}》共享标签：{names}",
                        "fig_no": "", "pdf_page": "", "caption": names})
    return out
