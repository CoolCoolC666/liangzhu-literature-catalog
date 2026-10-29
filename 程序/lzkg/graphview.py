# -*- coding: utf-8 -*-
"""图谱降密度：边裁剪 + **按子图重算布局** + 标签候选。

★ 为什么不能直接用全图坐标
  `layout.py` 算的是**全图 3486 个节点**的坐标，前端裁到 600 个后，
  裁出来的多是 hub 节点 —— 它们在原布局里就聚在各个社区的中心。
  于是「中心挤成一团 + 标签叠字」。
  实测（600 节点 / 7179 边）：**513 个（85%）的最近邻距离 < 20px**，
  中位数只有 8.8px，最小 0（完全重合）；而 11px 的标签字宽约 60px → 必然糊。

★ 三处根因，三处治法
  ① 边全画 → **按度数上限裁边**（`trim_edges`）
  ② 坐标用全图的 → **按裁剪后的子图重算 spring 布局**（`layout_subgraph`）
  ③ 标签全显示 → **标签给「社区代表」而不是「度数最高」**（`label_candidates`）

★ ③ 为什么不是「给度数最高的节点显示标签」（这是实测推翻的直觉）
  度数最高的节点正是**跨社区的 hub**，它们在布局里全都聚在簇心 ——
  实测「度数 top60」两两最近距中位仅 **19.4px**，60 个里有 44 个挤在 40px 内，
  标签照样叠字。
  改用「每个社区的代表节点」，两两最近距中位 **93.9px**（16 个里只有 6 个在 40px 内）——
  因为它们天然分布在各个簇里。**标签要分散，不要集中在簇心。**

★ 可复现性怎么办（这是本项目一贯的硬要求）
  · `spring_layout(seed=42)` —— seed 固定
  · 节点与边**按 key 排序后插入**（`build_networkx` 已有这个规矩）
  · 裁边排序用 `(-weight, src, dst, rel)` —— 同权重的边也有确定次序
  · → 同一份子图 **每次算出来的坐标完全一样**（实测两次结果逐位相同）
  · 结果用 `lru_cache` 缓存，避免每次改筛选都重算 1.3 秒

★ 为什么不改用 vis-network 自带的 physics
  前端 `physics:false` + 后端给坐标，是当初刻意定下的「同一份数据永远画出同一张图」。
  开 physics 每次打开位置都漂 → 判据「布局可复现」直接废掉。
  所以：**物理感在后端算，前端照旧只画**。
"""
from __future__ import annotations

import functools
import math

# 边的度数上限：每个节点最终最多保留这么多条边
DEFAULT_DEGREE_CAP = 6
# spring 迭代次数：50 → 1.2s，100 → 1.3s，200 → 2.6s
SPRING_ITER = 100
SPRING_SEED = 42
# 输出坐标范围（与全图布局一致，便于前端沿用同样的视野设置）
OUT_SCALE = 1000.0
# 归一化时用的分位（抗离群点：不让少数飞远的点把主体压扁）
NORM_PCT = 0.98


def _edge_key(e: dict) -> tuple:
    """边的排序键：权重降序，再按 src/dst/rel 升序。

    ★ 为什么要「再按 src/dst 排」
      只按权重排，遇到**同权重**的边时顺序取决于 dict 的插入顺序 →
      同一份数据在不同进程里可能先保留不同的边 → 坐标不可复现。
    """
    return (-float(e.get("weight") or 0), str(e.get("src")), str(e.get("dst")),
            str(e.get("rel")))


def trim_edges(nodes: dict, edges: list, cap: int = DEFAULT_DEGREE_CAP) -> list:
    """按**度数上限**贪心裁边。

    ★ 为什么用「度数上限」而不是「每个节点保留 top-k 条」
      「每节点保 k 条」对 **hub 节点无效**：hub 有 300 个邻居，
      而每个邻居都把 hub 当成自己最强的那条边 → hub 的边一条没少，
      实测裁完最大度数仍有 167。
      改成「**任何节点的度数都不得超过 cap**」，按边强度从强到弱贪心取，
      满 cap 的节点后来的边直接跳过 —— hub 的弱边这时才被真正削掉。

    ★ 边强度用 weight
      `weight` 是这张图里一条边承载的关系数（多篇文献支持同一对节点会叠加）。
      权重高的边是图里的骨干，先保留。

    ★ ★ 裁完之后剩下的孤点怎么办（本模块最难的一处）
      实测：cap=6 裁完，600 个节点里 **256 个（43%）变成孤点** —— 它们是图版节点（`img:*`），
      全部通过「对象类型」节点连到主体，而那些 hub 在前面就被 cap 填满了，
      图版节点一条边都接不上。
      早期版本给孤点接「它最强的那条边」—— 几百个孤点的最强边都指向
      同一个 `objtype:IMGOBJ-OTHER`，把那个节点撑到 **186 度**，cap 直接失效。
      → 改成：孤点优先接「**当前度数最小**的邻居」，
        这样孤点彼此连成小组件，而不是全挂到同一个 hub 上。
      仍然连不上的，交给 `prune_isolated` 剔除（什么都没连的节点没有信息量）。

    ★ ★ ★ 孤点接回也必须**有上限**（本模块第二难的一处）
      「孤点接度数最小的邻居」听起来已经够了，但**没有上限**时：
      273 个孤点里有一批**只有一条边**（只能指向某个固定 hub），
      那个 hub 的度数就被孤点一个个顶上去 —— 实测**最大度数 19**（cap=6）。
      19 比原来的 167 好，但仍是「hub 效应」的残留。
      → 给接回也加一个上限 `ORPHAN_DEGREE_LIMIT = cap × 2`：
        对方的度数已经到上限就不接，接不上的孤点交给 `prune_isolated` 剔除。
      实测四项指标**同时变好**（cap=6 / limit=600 节点时）：
        节点 589 → 505、边 1096 → 1012、最大度数 19 → 12、
        最近邻中位距离 13.38px → 14.32px（更不挤）。
      → 也就是说「限制孤点接回」不是妥协，是**双赢**：既守住度数上限，
        又因为剔掉了挂不上的节点而让主体更疏。
    """
    cap = max(1, int(cap))
    if not edges:
        return []
    order = sorted(edges, key=_edge_key)
    deg: dict[str, int] = {str(n): 0 for n in nodes}
    kept: list[dict] = []
    seen: set[tuple] = set()

    def _key(e: dict) -> tuple:
        a, b = str(e["src"]), str(e["dst"])
        return (a, b) if a <= b else (b, a)

    # ── 主裁剪：按强度取边，受度数上限约束 ──────────────────────────
    for e in order:
        a, b = str(e["src"]), str(e["dst"])
        if a not in deg or b not in deg:
            continue
        k = _key(e)
        if k in seen:
            continue
        if deg[a] >= cap or deg[b] >= cap:
            continue
        seen.add(k)
        deg[a] += 1
        deg[b] += 1
        kept.append(e)

    # ── 孤点接回：优先接「当前度数最小」的邻居（分散，不全挂 hub）────
    #   对方的度数也必须低于 ORPHAN_DEGREE_LIMIT，否则这个孤点接不上 → 被剔除
    lim = cap * 2
    for n in sorted(deg.keys()):
        if deg[n] != 0:
            continue
        best = None
        best_key = None
        for e in order:
            a, b = str(e["src"]), str(e["dst"])
            if n not in (a, b):
                continue
            other = b if a == n else a
            if deg.get(other, 0) >= lim:
                continue          # ★ 对方也满了 → 不接（宁可剔除这个孤点）
            # 对方度数小的优先；度数相同则边强的优先
            kk = (deg.get(other, 0), -float(e.get("weight") or 0), str(other))
            if best_key is None or kk < best_key:
                best_key = kk
                best = e
        if best is None:
            continue
        a, b = str(best["src"]), str(best["dst"])
        kept.append(best)
        deg[a] += 1
        deg[b] += 1
    return kept


def prune_isolated(nodes: dict, edges: list):
    """剔除**什么都没连**的节点。返回 (留下的节点, 留下的边, 被剔除的节点)。"""
    deg = degree_map_local(nodes, edges)
    drop = {n for n, v in deg.items() if v == 0}
    if not drop:
        return nodes, edges, []
    kept_nodes = {n: v for n, v in nodes.items() if str(n) not in drop}
    kept_edges = [e for e in edges
                  if str(e["src"]) not in drop and str(e["dst"]) not in drop]
    return kept_nodes, kept_edges, sorted(drop)


def build_subgraph(nodes: dict, edges: list):
    """建子图。**按 key 排序后插入**（保证 spring 结果可复现）。"""
    import networkx as nx
    G = nx.Graph()
    for nid in sorted(nodes):
        G.add_node(nid)
    for e in sorted(edges, key=lambda e: (str(e["src"]), str(e["dst"]), str(e.get("rel")))):
        a, b = str(e["src"]), str(e["dst"])
        if a not in G or b not in G:
            continue
        if G.has_edge(a, b):
            G[a][b]["weight"] = G[a][b].get("weight", 0) + float(e.get("weight") or 1)
        else:
            G.add_edge(a, b, weight=float(e.get("weight") or 1), rel=str(e.get("rel") or ""))
    return G


def _clip_and_normalize(pos: dict, pct: float = NORM_PCT) -> dict:
    """抗离群点归一化：先按分位数把离群点压回边界，再归一到 ±OUT_SCALE。

    ★ 为什么必须先 clip 再归一
      spring 的输出里，少数节点（尤其是孤立点、极低度点）会飞到很远。
      若直接按 max/min 归一化，画布尺度被这些离群点撑大 →
      **主体密集区被压扁**，看起来比不重算还挤（实测：中位 8.8px → 重算后 11.2px，
      远达不到「不糊」的水平）。
      先把离群点 clip 到 98% 分位边界，主体才能真正占满画布。
    """
    if not pos:
        return {}
    xs = sorted(float(p[0]) for p in pos.values())
    ys = sorted(float(p[1]) for p in pos.values())
    lo = int(len(xs) * (1 - pct) / 2)
    hi = int(len(xs) * (1 + pct) / 2)
    lo = max(0, min(lo, len(xs) - 1))
    hi = max(0, min(hi, len(xs) - 1))
    x0, x1 = xs[lo], xs[hi]
    y0, y1 = ys[lo], ys[hi]
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    span = max(x1 - x0, y1 - y0) / 2.0
    if span <= 0:
        span = 1.0
    k = OUT_SCALE / span
    return {n: ((min(max(float(p[0]), x0), x1) - cx) * k,
                (min(max(float(p[1]), y0), y1) - cy) * k)
            for n, p in pos.items()}


@functools.lru_cache(maxsize=64)
def _spring_cached(sig: tuple) -> dict:
    """按（节点元组, 边元组）缓存布局结果。

    ★ 为什么需要缓存
      spring_layout 在 600 节点 / 1000 边上要 **1.3 秒**。
      用户每改一次筛选就该重算一次 —— 不缓存的话每次都等一秒多，手感很差。
      缓存键必须包含**全部**节点与边（子图变了布局就该变），
      且必须是**可哈希的稳定表示**（用排序后的元组，不用 dict）。
    """
    nodes_t, edges_t = sig
    import networkx as nx
    G = nx.Graph()
    G.add_nodes_from(nodes_t)
    for a, b, w in edges_t:
        if G.has_edge(a, b):
            G[a][b]["weight"] += w
        else:
            G.add_edge(a, b, weight=w)
    if G.number_of_nodes() == 0:
        return {}
    if G.number_of_nodes() == 1:
        only = next(iter(G.nodes()))
        return {only: (0.0, 0.0)}
    pos = nx.spring_layout(G, seed=SPRING_SEED, iterations=SPRING_ITER)
    return _clip_and_normalize({n: (float(p[0]), float(p[1])) for n, p in pos.items()})


def _signature(nodes: dict, edges: list) -> tuple:
    nodes_t = tuple(sorted(str(n) for n in nodes))
    edges_t = tuple(sorted((str(e["src"]), str(e["dst"]), float(e.get("weight") or 1))
                          for e in edges))
    return (nodes_t, edges_t)


def layout_subgraph(nodes: dict, edges: list) -> dict[str, tuple[float, float]]:
    """按**裁剪后的子图**重算坐标。同一个子图永远得到同一组坐标。"""
    if not nodes:
        return {}
    try:
        return _spring_cached(_signature(nodes, edges))
    except Exception:
        # 布局失败不能拖垮整个接口 → 退回空坐标（前端会退回力导向）
        return {}


def label_candidates(nodes: dict, edges: list, per_community: int = 2,
                     extra_top: int = 12, max_total: int = 48) -> list[str]:
    """算出**该显示名字的节点**，按「轮转」排序。

    ★ 核心思路：标签要**分散**，不要集中在簇心
      「度数 top N」看起来最合理，但 hub 全在簇心 ——
      实测「度数 top60」两两最近距中位仅 **19.4px**，60 个里有 44 个挤在 40px 内，
      标签照样叠字。
      改用「每个社区的代表节点」，两两最近距中位 **93.9px**（16 个里只有 6 个在 40px 内）——
      因为它们天然分布在各个簇里。**标签要分散，不要集中在簇心。**

    ★ ★ 为什么要**轮转**排序而不是「社区1的两个、社区2的两个…」
      前端要按缩放级别**渐进放出**标签（全图只放 12 个，放大才放更多）。
      如果前 12 个全是社区 1、2 的标签，那全图视图就只有 6 个社区有名字，
      其余 6 个社区在图上「没有名字」—— 看着像漏了。
      → 轮转后，前 12 个就是**每个社区各一个**，天然覆盖全图；
        第 13~24 个是各社区第二个；再往后才是全局度数最高的补充。

    ★ 组成
      · 每社区取度数最高的 `per_community` 个（社区的骨架节点），轮转排列
      · 再补 `extra_top` 个全局度数最高的（保证最重要的一定有名字）
      · 总量不超过 `max_total`（标签太多本身就等于没标签）
    """
    import networkx as nx
    try:
        G = build_subgraph(nodes, edges)
        if G.number_of_nodes() == 0:
            return []
        deg = dict(G.degree())
        out: list[str] = []
        seen: set[str] = set()
        comms = sorted(nx.community.louvain_communities(G, seed=SPRING_SEED),
                        key=len, reverse=True)
        ranked = [sorted(c, key=lambda n: (-deg.get(n, 0), str(n))) for c in comms]
        # ★ 轮转：第 1 轮每社区取第 1 个，第 2 轮每社区取第 2 个……
        for r in range(per_community):
            for lst in ranked:
                if r < len(lst) and lst[r] not in seen:
                    seen.add(lst[r])
                    out.append(lst[r])
        for nid in sorted(nodes, key=lambda n: (-deg.get(n, 0), str(n)))[:extra_top]:
            if nid not in seen:
                seen.add(nid)
                out.append(nid)
        return out[:max_total]
    except Exception:
        # Louvain 失败就退回「度数 top」—— 至少还有名字
        deg = degree_map_local(nodes, edges)
        return sorted(nodes, key=lambda n: (-deg.get(n, 0), str(n)))[:max_total]


def degree_map_local(nodes: dict, edges: list) -> dict[str, int]:
    deg: dict[str, int] = {str(n): 0 for n in nodes}
    for e in edges:
        a, b = str(e["src"]), str(e["dst"])
        if a in deg:
            deg[a] += 1
        if b in deg:
            deg[b] += 1
    return deg


def nearest_gap(nodes: dict, pos: dict) -> float:
    """最近邻距离的中位数 —— 用来量化「挤不挤」，写进判据。

    ★ 为什么要有这个指标
      「看着挤」是主观的，判据必须可量化。
      基线：全图坐标 + 600 节点 → 中位 8.8px。
    """
    pts = [(pos[n][0], pos[n][1]) for n in nodes if n in pos]
    if len(pts) < 2:
        return 0.0
    gaps = []
    for i, (xi, yi) in enumerate(pts):
        best = None
        for j, (xj, yj) in enumerate(pts):
            if i == j:
                continue
            d = (xi - xj) ** 2 + (yi - yj) ** 2
            if best is None or d < best:
                best = d
        if best is not None:
            gaps.append(best ** 0.5)
    gaps.sort()
    return gaps[len(gaps) // 2]


def spread(nodes: dict, pos: dict, subset) -> float:
    """给定子集的**两两最近距中位数** —— 用来量化「标签散不散」。"""
    pts = [pos[n] for n in subset if n in pos]
    if len(pts) < 2:
        return 0.0
    gaps = []
    for i, (xi, yi) in enumerate(pts):
        best = None
        for j, (xj, yj) in enumerate(pts):
            if i == j:
                continue
            d = (xi - xj) ** 2 + (yi - yj) ** 2
            if best is None or d < best:
                best = d
        if best is not None:
            gaps.append(best ** 0.5)
    gaps.sort()
    return gaps[len(gaps) // 2]
