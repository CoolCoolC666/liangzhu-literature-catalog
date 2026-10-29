# -*- coding: utf-8 -*-
"""图谱的**确定性布局**（F2）。

为什么不用前端的力导向
----------------------
vis-network 的力导向是**每次打开都在重算**：节点位置会漂、同一份数据两次
截图对不上、演示时说不清"这张图代表什么"。而且本项目图密度是 cacx 的两倍
（600 节点 / 7140 边），力导向在那个密度下会缩成一团毛线，点都点不中。

所以这里做法是：**后端把坐标一次算好存进 graph_nodes.x / y，前端 physics:false
直接用坐标画**。这样做的三个好处：
  1. 同一份数据永远画出同一张图（截图、报告、演示都能对得上）
  2. 前端不再每开一次页面重算 3000+ 节点，开页即画
  3. 节点位置可被后端其他功能复用（子图、离线路谱导出、截图脚本）

算法：社区感知的向日葵排布
------------------------
整图直接跑 spring_layout 得到的还是一团毛线——本图是「文献–图像–标签–对象类型」
四层结构，整图力导向会把四层揉在一起。所以分三步：

  1. **分簇**：Louvain 社区划分（seed 固定，与 analyze() 用同一套参数）
  2. **簇内排布**：每个社区在自己的诱导子图上跑 spring_layout（seed 固定）
  3. **簇间排布**：社区按规模降序，用向日葵（黄金角 + sqrt 半径）排布在平面上，
     大簇靠中心、小簇在外圈，簇之间不重叠

★ 全流程 seed=42 且不依赖字典/集合的迭代顺序（所有涉及排序的地方都带
  tiebreaker），所以同一份输入必然得到同一份坐标。
"""
from __future__ import annotations

import math
from typing import Iterable

# 向日葵排布的黄金角（度）
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
# 最终坐标范围：[-OUT_SCALE, OUT_SCALE]
OUT_SCALE = 1000.0
# 簇半径的压缩指数（1.0 = 完全按规模，0 = 所有簇等大）
# 0.5 = 面积正比于簇规模（小簇不至于被压成一个点）
CLUSTER_SIZE_EXP = 0.50
# 相邻簇之间的最小间隙倍数（1.0 = 刚好贴上；越大越松）
CLUSTER_GAP = 1.08


def _induced_sorted(G, members):
    """按 node_id 排序构造**诱导子图**。

    ★ 绝不能直接用 G.subgraph(members).copy()。
      subgraph 视图在遍历时，若集合比原图小一半，就直接**按 set 的迭代顺序**走；
      而 Python 对字符串的哈希是**每进程随机化**的（PYTHONHASHSEED）→ 同一个
      社区在不同进程里节点顺序不同 → spring_layout 把初始随机位置分配给不同的
      节点 → 同一份数据两次算出的坐标差出上百。
      这个坑不报错、同进程里测两次还是绿的，只在「重算一次后和库里的坐标对不上」
      时才暴露。所以这里改成**显式排序建图**，把顺序从「碰运气」变成「写死」。
    """
    import networkx as nx
    ms = set(members)
    view = G.subgraph(ms)
    sub = nx.Graph()
    for n in sorted(view.nodes()):
        sub.add_node(n, **G.nodes[n])
    for u, v in sorted(view.edges()):
        attr = G[u][v]
        sub.add_edge(u, v, **{"weight": attr.get("weight", 1),
                              "rel": attr.get("rel", ""),
                              "lit_id": attr.get("lit_id", "")})
    return sub


def _spring(sub, seed: int):
    """在一个子图上跑 spring_layout，退化情况（0/1 个点）单独兜住。"""
    import networkx as nx
    n = sub.number_of_nodes()
    if n == 0:
        return {}
    if n == 1:
        only = next(iter(sub.nodes()))
        return {only: (0.0, 0.0)}
    # k 随规模走：大图里默认 k 太小会把图压成一团
    k = 1.0 / math.sqrt(n - 1)
    pos = nx.spring_layout(sub, seed=seed, k=k, iterations=200, weight="weight")
    return {node: (float(p[0]), float(p[1])) for node, p in pos.items()}


def _normalize(pos: dict, target_radius: float = 1.0) -> dict:
    """把一组坐标平移到质心、并缩放到最大半径 = target_radius。"""
    if not pos:
        return {}
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    far = max(math.hypot(p[0] - cx, p[1] - cy) for p in pos.values())
    scale = (target_radius / far) if far > 1e-9 else 1.0
    return {n: ((p[0] - cx) * scale, (p[1] - cy) * scale) for n, p in pos.items()}


def compute_layout(nodes: dict, edges: list, seed: int = 42) -> dict[str, tuple[float, float]]:
    """算出整图的确定性坐标：{node_id: (x, y)}。

    nodes / edges 用 graph.load_graph() 的返回格式。
    """
    import networkx as nx
    from .graph import build_networkx

    if not nodes:
        return {}
    G = build_networkx(nodes, edges)

    # ── 1. 分簇 ──────────────────────────────────────────────
    try:
        comms = list(nx.community.louvain_communities(G, seed=seed))
    except Exception:
        comms = [set(G.nodes())]
    # 排序必须带 tiebreaker（min 节点 id），否则同规模社区的顺序会漂
    comms = sorted(comms, key=lambda c: (-len(c), min(c)))

    # ── 2. 簇内排布 ──────────────────────────────────────────
    clusters = []
    for c in comms:
        sub = _induced_sorted(G, c)
        pos = _normalize(_spring(sub, seed))
        if pos:
            clusters.append({"members": set(pos.keys()), "pos": pos, "size": len(pos)})
    if not clusters:
        return {}

    # ── 3. 簇间排布（向日葵螺线，自适应步长）────────────────────
    #  均匀圆周排布会让大小簇之间空出一大片（前面版本就是这样：9 个簇挤在
    #  画面里却占了不到一半面积）。改成**沿螺线往外推**：每放一个簇，
    #  按它的半径算出下一个簇至少要离多远，保证相邻簇不重叠。
    max_size = max(c["size"] for c in clusters)
    out: dict[str, tuple[float, float]] = {}
    prev_r = 0.0
    prev_rho = 0.0
    for i, cl in enumerate(clusters):
        radius = (cl["size"] / max_size) ** CLUSTER_SIZE_EXP
        if i == 0:
            rho = radius * 1.15
        else:
            # 需要 rho 与上一个簇心的距离 ≥ (r_i + r_{i-1}) * GAP
            need = (radius + prev_r) * CLUSTER_GAP
            # 解 |P_i - P_{i-1}| = need，其中夹角固定为黄金角
            c = math.cos(GOLDEN_ANGLE)
            # rho^2 - 2*rho*prev_rho*c + prev_rho^2 - need^2 = 0
            b = -2.0 * prev_rho * c
            disc = b * b - 4.0 * (prev_rho * prev_rho - need * need)
            if disc < 0:
                rho = prev_rho + need
            else:
                rho = (-b + math.sqrt(disc)) / 2.0
        ang = i * GOLDEN_ANGLE
        ox = rho * math.cos(ang)
        oy = rho * math.sin(ang)
        for node, (lx, ly) in cl["pos"].items():
            out[node] = (ox + lx * radius, oy + ly * radius)
        prev_r = radius
        prev_rho = rho

    # ── 4. 归一到 [-OUT_SCALE, OUT_SCALE] ───────────────────
    xs = [p[0] for p in out.values()]
    ys = [p[1] for p in out.values()]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    scale = (OUT_SCALE * 2.0 / span) if span > 1e-9 else 1.0
    out = {n: ((x - cx) * scale, (y - cy) * scale) for n, (x, y) in out.items()}
    return out


def layout_into_db(conn, seed: int = 42) -> dict:
    """把算好的坐标写进 graph_nodes.x / y，并在 graph_layout 留一行版本记录。"""
    from .graph import load_graph
    import json
    import datetime

    nodes, edges = load_graph(conn)
    pos = compute_layout(nodes, edges, seed=seed)
    conn.executemany("UPDATE graph_nodes SET x = ?, y = ? WHERE node_id = ?",
                     [(p[0], p[1], n) for n, p in pos.items()])

    version = f"community-sunflower-seed{seed}"
    params = json.dumps({
        "algo": "louvain-community + per-community spring + sunflower packing",
        "seed": seed,
        "cluster_size_exp": CLUSTER_SIZE_EXP,
        "cluster_gap": CLUSTER_GAP,
        "out_scale": OUT_SCALE,
    }, ensure_ascii=False)
    conn.execute(
        "INSERT OR REPLACE INTO graph_layout (version, algo, params, node_count, edge_count, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (version, "community-sunflower", params, len(nodes), len(edges),
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return {"nodes": len(nodes), "edges": len(edges), "positioned": len(pos), "version": version}
