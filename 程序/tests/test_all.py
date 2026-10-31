# -*- coding: utf-8 -*-
"""验收脚本：数据层与 API 的关键判据。**可独立运行**。

    python tests/test_all.py            # 全跑
    python tests/test_all.py --api      # 只跑 API（需要先启动服务）
    python tests/test_all.py --layout   # 只跑布局可复现性（慢：跨进程重算两次）

设计原则（照抄 cacx 的教训）：
    **断言「产物合法」≠ 断言「产物正确」**
    所以每条判据都写成「必须等于某个实测真值」，而不是「不为空」。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


# ── 数据层 ──────────────────────────────────────────────────────────────
def test_db():
    section("数据层")
    from lzkg.db import connect, count, get_meta
    from lzkg.config import get_config
    cfg = get_config()
    check("数据源目录存在", cfg.source_root.is_dir(), str(cfg.source_root))
    check("文献登记表存在", cfg.lit_xlsx.exists(), cfg.lit_xlsx.name)
    check("图像登记表存在", cfg.img_xlsx.exists(), cfg.img_xlsx.name)
    check("数据库存在", cfg.db_path.exists(), str(cfg.db_path))
    if not cfg.db_path.exists():
        return
    c = connect()
    try:
        meta = get_meta(c)
        n_lit = count(c, "literatures")
        n_img = count(c, "images")
        check("文献 200 条", n_lit == 200, f"实际 {n_lit}")
        check("图像 3162 条", n_img == 3162, f"实际 {n_img}")
        check("标签已建", count(c, "tags") > 50, f"实际 {count(c,'tags')}")
        check("文献标签关联非空", count(c, "lit_tags") > 0, f"实际 {count(c,'lit_tags')}")
        check("图像标签关联非空", count(c, "img_tags") > 0, f"实际 {count(c,'img_tags')}")
        check("原始 JSON 已入库", count(c, "raw_json") >= 200, f"实际 {count(c,'raw_json')}")
        check("全文索引非空", count(c, "docs_fts") >= 200, f"实际 {count(c,'docs_fts')}")
        check("图谱节点非空", count(c, "graph_nodes") > 0, f"实际 {count(c,'graph_nodes')}")
        check("图谱边非空", count(c, "graph_edges") > 0, f"实际 {count(c,'graph_edges')}")
        check("迁移时间已记录", bool(meta.get("migrated_at")), str(meta.get("migrated_at")))
        # 四维标签不得为空
        empty = c.execute(
            "SELECT COUNT(*) n FROM literatures WHERE period_tags='' OR region_tags='' "
            "OR topic_tags='' OR lineage_tags=''").fetchone()["n"]
        check("四维标签无空值", empty == 0, f"空值行 {empty}")
        # ★ 受控词表校准后：**一码多义的族系代号必须彻底消失**
        #   （旧机制是「保留码位 + 标不可靠」；现在改成「每个受控词一个唯一码」）
        multi = c.execute(
            "SELECT code, dim, COUNT(DISTINCT name) n FROM tags WHERE code<>'' "
            "GROUP BY code, dim HAVING n>1").fetchall()
        check("受控词表已消除一码多义码位", len(multi) == 0,
              str([f"{r['dim']}:{r['code']}×{r['n']}" for r in multi]))
        # 旧的多义族系代号不得再出现在库里
        old_codes = [("region", "R-0199"), ("topic", "S-0801"),
                     ("topic", "S-0802"), ("topic", "S-0803"), ("topic", "S-0804")]
        left = []
        for dim, code in old_codes:
            n = c.execute("SELECT COUNT(*) n FROM tags WHERE dim=? AND code=?",
                          (dim, code)).fetchone()["n"]
            if n:
                left.append(f"{dim}:{code}×{n}")
        check("旧族系代号已全部退役", not left, f"仍在用：{left}")
        # region / topic 两个维度，每个受控词都必须有唯一码
        nocode = c.execute(
            "SELECT COUNT(*) n FROM tags WHERE dim IN ('region','topic') "
            "AND (code IS NULL OR code='')").fetchone()["n"]
        check("region/topic 受控词全部有唯一码", nocode == 0, f"{nocode} 个缺码")
        unreliable = c.execute(
            "SELECT COUNT(*) n FROM tags WHERE code_reliable=0 AND code<>''").fetchone()["n"]
        check("受控词表内无不可靠码位", unreliable == 0, f"{unreliable} 个")
        # 同义归并后「墓葬」不再是独立标签，「墓葬考古」在
        merged = c.execute(
            "SELECT COUNT(*) n FROM tags WHERE dim='topic' AND name='墓葬'").fetchone()["n"]
        check("同义归并：墓葬并入墓葬考古", merged == 0,
              f"墓葬仍有 {merged} 条")
        kept = c.execute(
            "SELECT COUNT(*) n FROM tags WHERE dim='topic' AND name='墓葬考古'").fetchone()["n"]
        check("归并目标标签在库", kept == 1, f"墓葬考古 {kept} 条")
    finally:
        c.close()


def test_tag_normalize():
    section("标签规范化")
    from lzkg.migrate import normalize_tags
    t = normalize_tags("S-0801 数据库; S-0803 GIS", "topic")
    check("多值拆分", len(t) == 2, str([x["name"] for x in t]))
    # ★ 旧码位换成受控词表的**唯一码**（S-09xx 段），不再沿用族系代号
    check("代码拆分为受控唯一码",
          bool(t[0]["code"]) and t[0]["code"] != "S-0801"
          and t[0]["code_reliable"] == 1 and t[0]["name"] == "数据库", str(t[0]))
    t2 = normalize_tags("良渚文化（数字展示）", "lineage")
    check("括号限定不进 name", t2 and t2[0]["name"] == "良渚文化", str(t2[0] if t2 else None))
    t3 = normalize_tags("R-0199 环太湖", "region")
    check("旧多义码位换成唯一码",
          t3 and t3[0]["code"] != "R-0199" and t3[0]["code_reliable"] == 1
          and t3[0]["name"] == "环太湖", str(t3[0] if t3 else None))
    t4 = normalize_tags("通用", "period")
    check("通用不建节点", t4 == [], str(t4))
    # 同义归并
    t5 = normalize_tags("S-0801 墓葬", "topic")
    check("同义归并：墓葬→墓葬考古",
          len(t5) == 1 and t5[0]["name"] == "墓葬考古", str(t5))
    # 组合标签拆分
    t6 = normalize_tags("凌家滩文化/红山文化", "lineage")
    check("组合标签拆成两个文化",
          len(t6) == 2 and {x["name"] for x in t6} == {"凌家滩文化", "红山文化"}, str(t6))
    # 受控词表码位文件覆盖：每个词都能查到唯一码
    from lzkg import vocab
    ct = vocab.code_table()
    miss = [f"{d}:{n}" for d, m in ct.items() for n in ct[d]
            if not vocab.code_for(d, n)]
    check("受控词表码位文件自洽", not miss, f"查不到码的词：{miss[:5]}")


def test_search():
    section("检索")
    from lzkg.db import connect
    from lzkg.app import _match_lit_ids, _match_detail
    c = connect()
    try:
        # ★ 精确层（mode="exact"）：**原有行为一字不改**（子串匹配）。
        #   检索增强 E1 之后 `mode` 字段只表示四档模式之一，
        #   「精确层走 FTS5 还是 LIKE 兜底」改由 `exact_backend` 字段承担
        #   （v3.9：同一个字段混两套词表 → 判据与界面对不齐，已拆开）。
        d2 = _match_detail(c, "玉琮", "exact")
        check("2 字词走 LIKE 兜底", d2["exact_backend"] == "like",
              f"backend={d2['exact_backend']} mode={d2['mode']}")
        check("2 字词有命中", len(d2["hits"]) > 0, f"命中 {len(d2['hits'])}")
        d3 = _match_detail(c, "良渚古城", "exact")
        check("3 字词走 FTS5", d3["exact_backend"] == "fts5",
              f"backend={d3['exact_backend']} mode={d3['mode']}")
        check("FTS5 有命中", len(d3["hits"]) > 0, f"命中 {len(d3['hits'])}")
        # 合并模式（E1 默认）：两种语义并存
        hb, modeb = _match_lit_ids(c, "玉琮", "both")
        check("合并模式生效", modeb == "both", f"mode={modeb} 命中 {len(hb)}")
        check("合并命中 ⊇ 精确命中", set(hb) >= set(d2["hits"]),
              f"精确 {len(d2['hits'])} 篇 → 合并 {len(hb)} 篇")
        # ★ mode 只允许四档之一（口径不许混）
        check("mode 取值只在四档模式内",
              d2["mode"] in ("exact", "word", "syn", "both") and modeb == "both",
              f"exact→{d2['mode']} both→{modeb}")
    finally:
        c.close()


def test_graph():
    section("图谱")
    from lzkg.db import connect
    from lzkg.graph import build_networkx, analyze, crop_scope
    c = connect()
    try:
        nodes = {r["node_id"]: dict(r) for r in c.execute("SELECT * FROM graph_nodes")}
        edges = [dict(r) for r in c.execute("SELECT * FROM graph_edges")]
        check("节点非空", len(nodes) > 0, str(len(nodes)))
        check("边非空", len(edges) > 0, str(len(edges)))
        fn, fe = crop_scope(nodes, edges, keyword="GIS")
        check("关键词裁剪生效", len(fn) < len(nodes), f"{len(nodes)} → {len(fn)}")
        a = analyze(fn, fe)
        check("分析返回连通分量数", "components" in a, str(a.get("components")))
        a2 = analyze(fn, fe)
        check("社区划分可复现（seed=42）", a.get("communities") == a2.get("communities"),
              "两次调用结果一致")
    finally:
        c.close()


# ── 布局的可复现性 ─────────────────────────────────────────────
def _layout_probe(hashseed: str) -> str:
    """在指定 PYTHONHASHSEED 下，算出「最大社区」的布局签名。

    为什么单独起进程、还要换 hashseed：
    Python 对字符串的哈希**每进程随机化**，`G.subgraph(社区集合)` 会按 set 的
    迭代顺序建图；而 spring_layout 把初始随机位置按节点顺序分配 —— 于是同一份
    数据在不同进程里算出的坐标不同（实测最大差 191 个单位）。
    这种差异在同进程里测两次永远是绿的，只在「重算一次后和库里的坐标对不上」
    才暴露。所以判据必须**跨进程 + 换 hashseed**。
    """
    import subprocess
    # ★ 路径用环境变量传，不要塞进 -c 的代码里：
    #   Windows 上 `-c` 里的非 ASCII 路径会在 argv 编码环节被搞坏，
    #   子进程 import 时报 `stat: embedded null character in path`。
    code = (
        "import os, sys, hashlib;"
        "sys.path.insert(0, os.environ['LZKG_ROOT']);"
        "from lzkg.db import connect;"
        "from lzkg.graph import load_graph;"
        "from lzkg.layout import compute_layout;"
        "c=connect(); n,e=load_graph(c); c.close();"
        "p=compute_layout(n,e,seed=42);"
        "h=hashlib.sha256();"
        "parts=[];"
        "parts.extend(['%s:%.6f:%.6f|' % (k,p[k][0],p[k][1]) for k in sorted(p)]);"
        "h.update(''.join(parts).encode());"
        "print(h.hexdigest()[:16])"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["PYTHONIOENCODING"] = "utf-8"
    env["LZKG_ROOT"] = str(PROGRAM_ROOT)
    # ★ 必须显式给 encoding：子进程按 PYTHONIOENCODING=utf-8 输出，
    #   而父进程默认按本地 GBK 解码 → UnicodeDecodeError（Windows 中文版必踩）
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=env, timeout=900)
    out = (r.stdout or "").strip()
    if out:
        return out.splitlines()[-1]
    return "ERR:" + ((r.stderr or "<no stderr>")[-300:]).replace("\n", " ")


def test_layout():
    section("图谱布局（确定性）")
    from lzkg.db import connect
    c = connect()
    try:
        n_node = c.execute("SELECT COUNT(*) n FROM graph_nodes").fetchone()["n"]
        n_pos = c.execute("SELECT COUNT(*) n FROM graph_nodes WHERE x IS NOT NULL AND y IS NOT NULL").fetchone()["n"]
        check("每个节点都有布局坐标", n_pos == n_node, f"{n_pos}/{n_node}")
        r = c.execute("SELECT MIN(x) lo, MAX(x) hi FROM graph_nodes").fetchone()
        check("坐标在约定范围内", -1000.5 <= r["lo"] and r["hi"] <= 1000.5, f"x∈[{r['lo']:.0f},{r['hi']:.0f}]")
        ver = c.execute("SELECT version, node_count, edge_count FROM graph_layout "
                        "ORDER BY created_at DESC LIMIT 1").fetchone()
        check("布局版本已登记", ver is not None and ver["node_count"] == n_node,
              f"{ver['version'] if ver else '无'}")
        # 扫描顺序必须写死，否则优化器一换策略结果就变
        ids = [r[0] for r in c.execute("SELECT node_id FROM graph_nodes")]
        check("load_graph 扫描顺序已固定", ids == sorted(ids), f"前 3 个 {ids[:3]}")
    finally:
        c.close()

    a = _layout_probe("0")
    b = _layout_probe("12345")
    check("换 PYTHONHASHSEED 结果不变（跨进程可复现）", a == b and not a.startswith("ERR"),
          f"seed0={a} seed12345={b}")

    from lzkg.db import connect as _c2
    c = _c2()
    try:
        row = c.execute("SELECT COUNT(*) n FROM graph_nodes WHERE x IS NOT NULL").fetchone()["n"]
    finally:
        c.close()
    check("库内坐标已写入", row > 0, f"{row} 个节点")


# ── Cloudflare Access 身份（公网开放注册）──────────────────────
def test_access():
    """Access 身份必须**验签**后才认。

    ★ 这组判据守的是最要命的一条：**请求头是能伪造的**。
      不验签就等于「任何人自己加一个 Cf-Access-Authenticated-User-Email 头
      就能当任意邮箱登录」。所以伪造的邮箱头、伪造的 JWT、aud 不对、
      过期、kid 不对、签名被改过——**一律必须不认**。
    """
    section("Cloudflare Access 身份（验签）")
    import time
    from lzkg import access
    from lzkg.config import get_config

    # ── 1) 端点层：没有可验签的身份就一律拒绝 ──
    try:
        import requests
        from lzkg.config import get_config as _c
        B = f"http://{_c().host}:{_c().port}"
        fake_jwt = ("eyJhbGciOiJSUzI1NiJ9."
                    "eyJlbWFpbCI6ImF0dGFja2VyQGV2aWwuY29tIn0.abc")
        r = requests.get(f"{B}/api/auth/access-identity", timeout=30)
        check("无 Access 头 → 401", r.status_code == 401, str(r.status_code))
        r = requests.get(f"{B}/api/auth/access-identity", timeout=30,
                         headers={"Cf-Access-Authenticated-User-Email": "attacker@evil.com"})
        check("伪造邮箱头 → 401", r.status_code == 401, str(r.status_code))
        r = requests.get(f"{B}/api/auth/access-identity", timeout=30,
                         headers={"Cf-Access-Jwt-Assertion": fake_jwt})
        check("伪造断言头 → 401", r.status_code == 401, str(r.status_code))
        r = requests.post(f"{B}/api/auth/register", timeout=30,
                          json={"email": "attacker@evil.com"},
                          headers={"Cf-Access-Authenticated-User-Email": "attacker@evil.com",
                                   "Cf-Access-Jwt-Assertion": fake_jwt})
        check("伪造头去注册 → 400", r.status_code == 400, str(r.status_code))
    except Exception as e:
        check("Access 端点可测", False, str(e)[:80])

    # ── 2) 「仅本机」判断：经隧道/反代进来的必须拒 ──
    #  ★ 这条判据守的是「含本机绝对路径的端点」在公网上会不会泄露。
    #    隧道后面 request.client.host 恒为 127.0.0.1，只看它等于没锁；
    #    真正管用的是「转发头里那个真实客户端 IP」。
    try:
        from lzkg.app import _is_local_request

        class _Req:
            def __init__(self, host, headers):
                self.client = type("C", (), {"host": host})()
                self.headers = headers

        check("本机直连 → 判定为本机",
              _is_local_request(_Req("127.0.0.1", {})) is True)
        check("本机 + 本机转发头 → 仍是本机",
              _is_local_request(_Req("127.0.0.1", {"cf-connecting-ip": "127.0.0.1"})) is True)
        check("隧道进来的公网访客（CF-Connecting-IP）→ 不算本机",
              _is_local_request(_Req("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"})) is False)
        check("隧道进来的公网访客（X-Forwarded-For）→ 不算本机",
              _is_local_request(_Req("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 10.0.0.1"})) is False)
        check("外网直连 → 不算本机",
              _is_local_request(_Req("203.0.113.7", {})) is False)
    except Exception as e:
        check("仅本机判断可测", False, str(e)[:90])

    # ── 3) 验签逻辑：正反各来一遍 ──
    try:
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        import json as _json
        jwk = _json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk["kid"] = "test-kid"
        jwk["alg"] = "RS256"
        aud = (get_config().public_cfg or {}).get("access_aud")
        access._CERTS_CACHE.update({"at": time.time(), "keys": {
            "test-kid": jwt.algorithms.RSAAlgorithm.from_jwk(_json.dumps(jwk))}})

        def tok(email, a=None, delta=3600, kid="test-kid"):
            now = int(time.time())
            return jwt.encode({"email": email, "name": "测试", "sub": "u1",
                               "aud": a or aud, "iat": now, "exp": now + delta},
                              key, algorithm="RS256", headers={"kid": kid})

        good = tok("real.user@example.com")
        check("正常断言 → 认出邮箱",
              (access.identity_from_headers({"cf-access-jwt-assertion": good}) or {}).get("email")
              == "real.user@example.com")
        check("aud 不匹配 → 不认",
              access.identity_from_headers({"cf-access-jwt-assertion": tok("a@b.com", a="wrong-aud")}) is None)
        check("已过期 → 不认",
              access.identity_from_headers({"cf-access-jwt-assertion": tok("a@b.com", delta=-60)}) is None)
        check("kid 对不上 → 不认",
              access.identity_from_headers({"cf-access-jwt-assertion": tok("a@b.com", kid="nope")}) is None)
        check("签名被改过 → 不认",
              access.identity_from_headers({"cf-access-jwt-assertion": good[:-4] + "AAAA"}) is None)
        check("只有邮箱头没有断言 → 不认",
              access.identity_from_headers({"cf-access-authenticated-user-email": "attacker@evil.com"}) is None)
    except ImportError:
        check("验签测试依赖可用（jwt/cryptography）", False, "未安装")
    except Exception as e:
        check("验签逻辑可测", False, str(e)[:90])


# ── API ─────────────────────────────────────────────────────────────────
def test_api():
    section("API（需先启动服务）")
    try:
        import requests
    except ImportError:
        check("requests 可用", False, "未安装")
        return
    from lzkg.config import get_config
    B = f"http://{get_config().host}:{get_config().port}"
    try:
        r = requests.get(f"{B}/api/health", timeout=10)
    except Exception as e:
        check("服务可达", False, f"{B} → {e}")
        return
    check("服务可达", r.status_code == 200)
    d = requests.get(f"{B}/api/overview", timeout=30).json()
    check("overview 文献数 200", d["lit_count"] == 200, str(d["lit_count"]))
    check("overview 图像数 3162", d["img_count"] == 3162, str(d["img_count"]))
    check("overview 复核率有值", 0 < d["review"]["rate"] < 1, str(d["review"]["rate"]))
    d = requests.get(f"{B}/api/literatures", params={"q": "玉琮", "size": 5}, timeout=60).json()
    check("检索有命中", d["total"] > 0, str(d["total"]))
    d = requests.get(f"{B}/api/literatures", params={"doc_type": "DOC-THESIS｜学位论文"}, timeout=30).json()
    check("类别筛选生效", d["total"] == 9, str(d["total"]))
    d = requests.get(f"{B}/api/literatures/LIT-000175", timeout=30).json()
    check("详情含四维标签", len(d["tags"]["region"]) > 0, str(d["tags"]))
    check("详情含原始 JSON", len(d["raw_json"]) > 0, str(len(d["raw_json"])))
    check("详情含图版", len(d["images"]) > 0, str(len(d["images"])))
    d = requests.get(f"{B}/api/graph", params={"limit": 100}, timeout=90).json()
    check("图谱返回节点", len(d["nodes"]) > 0, str(len(d["nodes"])))
    check("图谱两个口径都在", "returned_nodes" in d["stats"] and "nodes" in d["stats"], str(d["stats"]))
    # 证据要用**库里真实存在**的图 ID（不是编一个）
    row = requests.get(f"{B}/api/images", params={"lit_id": "LIT-000175", "size": 1},
                       timeout=30).json()["items"][0]
    d = requests.get(f"{B}/api/graph/evidence",
                     params={"src": f"lit:{row['lit_id']}", "dst": f"img:{row['img_id']}",
                             "rel": "contains"}, timeout=30).json()
    check("边有证据", len(d["items"]) > 0, f"{row['img_id']} → {len(d['items'])} 条")
    check("证据能指回文献", bool(d["items"] and d["items"][0].get("lit_id")),
          str(d["items"][0].get("lit_id") if d["items"] else None))
    r = requests.get(f"{B}/api/export/literatures.csv", timeout=90)
    check("CSV 导出 200 行", r.content.decode("utf-8-sig").count("\n") > 200, str(len(r.content)))
    check("CSV 带 BOM", r.content[:3] == b"\xef\xbb\xbf")
    r = requests.get(f"{B}/api/export/graph.html", params={"limit": 50}, timeout=90)
    # 判据是「没有外链依赖」：src/href 不指向 http(s)。注释里的官网地址不算依赖。
    import re as _re
    outer = _re.findall(r'(?:src|href)\s*=\s*["\']https?://', r.text)
    check("离线图谱零外链依赖", len(outer) == 0, f"外链 {len(outer)} 处")
    r = requests.get(f"{B}/media/img/NOT_EXIST.jpg", timeout=30)
    check("缺图返回占位而非报错", r.status_code == 200 and b"svg" in r.content[:80])

    # ── AI 接口（浏览器保存配置 + 三种协议）────────────────────────
    d = requests.get(f"{B}/api/ai/providers", timeout=30).json()
    provs = {p["provider"] for p in d.get("data", {}).get("items", [])}
    check("AI 协议清单三种", provs == {"openai", "anthropic", "local"}, str(sorted(provs)))
    # 不带配置 → 必须降级且**明说**不是模型生成的
    d = requests.post(f"{B}/api/ai/ask", json={"question": "良渚玉琮的分期研究", "top_k": 3},
                      timeout=60).json()
    check("AI 未配置时降级", d.get("data", {}).get("degraded") is True, str(d.get("data", {}).get("provider")))
    check("AI 降级答案自报家门", "不是大模型生成的回答" in (d.get("data", {}).get("answer") or ""),
          (d.get("data", {}).get("answer") or "")[:60])
    check("AI 降级时仍有检索命中", len(d.get("data", {}).get("citations") or []) > 0,
          f"{len(d.get('data', {}).get('citations') or [])} 条")
    # 配了但连不上 → 也要降级，**不许 500**
    try:
        r = requests.post(f"{B}/api/ai/ask", timeout=90, json={
            "question": "良渚古城",
            "provider": {"provider": "openai", "api_base": "http://127.0.0.1:59999/v1",
                         "api_key": "sk-should-never-leak-123456", "model": "x"}})
        d2 = r.json()
        check("AI 接口不通时不报错（降级兜底）", r.status_code == 200 and d2.get("data", {}).get("degraded") is True,
              str(r.status_code))
        check("AI 失败信息里不含 key", "should-never-leak" not in r.text, "已打码")
    except Exception as e:
        check("AI 接口不通时不报错（降级兜底）", False, str(e)[:80])

    # ── 遗址点位 / 区域代表点 ───────────────────────────────────
    d = requests.get(f"{B}/api/sites", timeout=30).json()
    dd = d.get("data", {})
    check("遗址候选非空", dd.get("total", 0) > 0, str(dd.get("total")))
    check("遗址坐标未编造（实测坐标为 0）", dd.get("with_coordinates") == 0, str(dd.get("with_coordinates")))
    check("区域代表点已载入", dd.get("regions_total", 0) == 12, str(dd.get("regions_total")))
    check("上图的区域代表点为 6", dd.get("regions_with_points") == 6, str(dd.get("regions_with_points")))
    nogeo = [r for r in (dd.get("regions") or []) if not r.get("on_map")]
    check("非地理分区不配坐标", len(nogeo) == 6 and all(r["lat"] is None for r in nogeo),
          str([r["region"] for r in nogeo]))
    r = requests.get(f"{B}/api/stats/map.png", timeout=90)
    check("地图出图是合法 PNG", r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n",
          f"{len(r.content)}B mode={r.headers.get('X-Chart-Mode')}")
    mode = (r.headers.get("X-Chart-Mode") or "")
    # ★ 地图现在是**三联图**（世界 / 中国 / 良渚区域），底图来自本地 GeoJSON。
    #   判据用「包含」而不是「全等」：模式里带的计数会随人工补录变化，
    #   钉死某个数就会在补坐标后失效。
    check("地图是三联图（世界+中国+区域）",
          mode.startswith("triptych:") and "world+china+region" in mode, mode)
    check("地图带文献范围框", "range:1" in mode, mode)
    check("地图带文献坐标点", "points:4" in mode, mode)

    # ── 交互地图：点级数据 + 底图文件通道 ────────────────────────────
    r_md = requests.get(f"{B}/api/stats/mapdata", timeout=60)
    check("mapdata 端点可用", r_md.status_code == 200 and r_md.json().get("ok") is True,
          f"HTTP {r_md.status_code}")
    if r_md.status_code == 200:
        md = (r_md.json().get("data") or {})
        check("mapdata 点数与分区数正确",
              len(md.get("points", [])) == 4 and len(md.get("regions", [])) == 12
              and len(md.get("sites", [])) == 18,
              f"点 {len(md.get('points', []))} / 分区 {len(md.get('regions', []))}"
              f" / 遗址 {len(md.get('sites', []))}")
        pts = md.get("points", [])
        check("文献坐标点都有经纬与出处",
              all(p.get("lon") is not None and p.get("lat") is not None
                  and p.get("source") for p in pts),
              str([p.get("name") for p in pts]))
        # ★ 关联文献数不许靠宽泛词凑数：宁可是 0，也不能虚高
        over = [(p["name"], p["lit_count"]) for p in pts if p.get("lit_count", 0) > 80]
        check("坐标点关联文献数无虚高（≤80）", not over, str(over))
        # 分区：不上图的必须没有坐标（不编造位置）
        nogeo_r = [x for x in md.get("regions", []) if not x.get("on_map")]
        check("不上图的分区没有坐标",
              len(nogeo_r) == 6 and all(x.get("lat") is None and x.get("lon") is None
                                       for x in nogeo_r),
              str([x.get("name") for x in nogeo_r]))
        # 候选遗址：文献里没有单点坐标的必须留空
        blank = [x for x in md.get("sites", []) if x.get("lat") is None]
        check("无坐标的候选遗址留空", len(blank) == 18,
              f"{len(blank)}/{len(md.get('sites', []))} 个留空")
        check("mapdata 写明口径", len(md.get("notes", [])) >= 3,
              str(len(md.get("notes", []))))

    # 底图文件通道：必需图层能取到，且**目录穿越被挡**
    for name in ("ne_110m_admin_0_countries.geojson",
                 "ne_50m_admin_1_states_provinces.geojson",
                 "ne_10m_lakes.geojson",
                 "osm_qiantang_yuhang.geojson"):
        rr = requests.get(f"{B}/basemap/{name}", timeout=60)
        check(f"底图可取：{name}", rr.status_code == 200 and len(rr.content) > 1000,
              f"HTTP {rr.status_code} {len(rr.content)}B")
    rr = requests.get(f"{B}/basemap/..%2Flzkg.db", timeout=30)
    check("底图通道挡目录穿越", rr.status_code == 404, f"HTTP {rr.status_code}")
    rr = requests.get(f"{B}/basemap/manifest.json", timeout=30)
    check("底图通道只放行 geojson", rr.status_code == 404, f"HTTP {rr.status_code}")

    # ── Web Dev（WebDAV）：端点存在、要求登录、私网被拒 ──────────────
    def _wd_post(path, payload, token=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Auth-Token"] = token
        return requests.post(f"{B}{path}", json=payload, headers=h, timeout=60)

    rw = _wd_post("/api/webdav/test", {"url": "http://192.168.1.1/", "user": "a",
                                      "password": "b"})
    check("WebDAV 端点拒绝私网地址（防 SSRF）",
          rw.status_code == 400 and "私网" in (rw.json().get("error") or ""),
          f"HTTP {rw.status_code} {(rw.json().get('error') or '')[:60]}")
    rw = _wd_post("/api/webdav/test", {"url": "http://127.0.0.1:8771/", "user": "a",
                                      "password": "b"})
    check("WebDAV 端点拒绝回环地址", rw.status_code == 400,
          f"HTTP {rw.status_code}")
    rw = _wd_post("/api/webdav/test", {"url": "http://169.254.169.254/", "user": "a",
                                      "password": "b"})
    check("WebDAV 端点拒绝云元数据地址", rw.status_code == 400,
          f"HTTP {rw.status_code}")
    rw = _wd_post("/api/webdav/test", {"url": "ftp://x/", "user": "a", "password": "b"})
    check("WebDAV 端点只允许 http(s)", rw.status_code == 400, f"HTTP {rw.status_code}")
    rw = _wd_post("/api/webdav/put", {"url": "https://dav.example.com/", "user": "a",
                                     "password": "b", "name": "x.json",
                                     "content": "{}"})
    check("WebDAV 上传端点存在且受同一防护", rw.status_code in (400, 401),
          f"HTTP {rw.status_code}")
    rw = _wd_post("/api/webdav/get", {"url": "https://dav.example.com/", "user": "a",
                                     "password": "b", "name": "x.json"})
    check("WebDAV 下载端点存在且受同一防护", rw.status_code in (400, 401),
          f"HTTP {rw.status_code}")

    # ── Web Dev 总闸：状态端点 + 关掉后接口 403 ────────────────────
    r_ws = requests.get(f"{B}/api/webdev/status", timeout=30)
    check("Web Dev 总闸状态端点可用",
          r_ws.status_code == 200 and isinstance(r_ws.json().get("data", {}).get("enabled"), bool),
          f"HTTP {r_ws.status_code}")
    # 总闸：真的关一次 → webdav 端点必须 403 → 再打开回来（finally 保证恢复）
    try:
        requests.post(f"{B}/api/webdev/toggle", json={"enabled": False}, timeout=30)
        r_off = _wd_post("/api/webdav/test", {"url": "https://dav.example.com/",
                                               "user": "a", "password": "b"})
        check("总闸关闭时 WebDAV 接口 403", r_off.status_code == 403,
              f"HTTP {r_off.status_code}")
    finally:
        requests.post(f"{B}/api/webdev/toggle", json={"enabled": True}, timeout=30)
    r_on = _wd_post("/api/webdav/test", {"url": "https://dav.example.com/",
                                          "user": "a", "password": "b"})
    check("总闸重新打开后接口不再 403", r_on.status_code != 403, f"HTTP {r_on.status_code}")

    # ── Web Dev 前端：界面、存储键名、说明链接、浏览记录钩子 ──────────
    wdir = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static"
    whtml = (wdir / "index.html").read_text(encoding="utf-8")
    check("前端有 Web Dev 独立界面", "renderWebDevPage" in whtml
          and 'sub === "webdev"' in whtml, "")
    check("前端有 Web Dev 工作台卡片", "Web Dev（浏览数据与同步）" in whtml, "")
    check("浏览记录存浏览器（键名固定）",
          "lzkg.browse.v1" in whtml and "lzkg.webdav.v1" in whtml, "")
    check("浏览记录有写入钩子", "browseRecord(" in whtml, "")
    check("配置区挂坚果云说明文档",
          "https://help.jianguoyun.com/?p=2064" in whtml, "")
    check("配置区挂知乎说明文档",
          "https://zhuanlan.zhihu.com/p/397069064" in whtml, "")
    check("前端有存放目录字段（坚果云不能往根目录写）",
          "wd-folder" in whtml and "WEBDAV_DEFAULT_FOLDER" in whtml, "")
    check("前端有导入 JSON（零门槛同步途径）",
          "wd-import" in whtml and "wdImportPicked" in whtml, "")
    check("前端有管理员总闸开关",
          "wd-toggle" in whtml and "wd-toggle2" in whtml and "/api/webdev/toggle" in whtml, "")
    check("前端有文件名兜底（别把人粘的路径当报错）",
          "wdSafeName" in whtml, "")
    check("WebDAV 后端模块在位",
          (Path(__file__).resolve().parent.parent / "lzkg" / "webdav.py").exists(), "")

    # ── 浏览记录「存本站服务器」：端点 + 总闸 + 隔离 ──────────────────
    r_bs = requests.get(f"{B}/api/browse/server/status", timeout=30)
    bs_data = {}
    if r_bs.status_code == 200:
        bs_data = (r_bs.json() or {}).get("data") or {}
    check("存服务器状态端点可用且带开关位",
          r_bs.status_code == 200 and isinstance(bs_data.get("enabled"), bool),
          f"HTTP {r_bs.status_code}")
    check("状态里给出落盘目录（相对路径，不暴露绝对路径）",
          isinstance(bs_data.get("dir"), str) and bs_data.get("dir", "").startswith("data/"),
          str(bs_data.get("dir")))

    # 走一遍真实的写→读（写完立刻还原，别动用户已有记录）
    old_bs = requests.get(f"{B}/api/browse/server", timeout=30)
    old_items = []
    if old_bs.status_code == 200:
        old_items = ((old_bs.json() or {}).get("data") or {}).get("items") or []
    try:
        probe = [{"kind": "lit", "id": "ZZ-TEST-1", "title": "自检用", "year": "2026",
                  "ts": 1, "password": "leak-me"}]
        wr = requests.post(f"{B}/api/browse/server",
                           json={"items": probe, "updated_at": "selftest"}, timeout=30)
        check("存服务器写入成功", wr.status_code == 200
              and (wr.json().get("data") or {}).get("count") == 1, f"HTTP {wr.status_code}")
        rd = requests.get(f"{B}/api/browse/server", timeout=30)
        got = ((rd.json() or {}).get("data") or {}) if rd.status_code == 200 else {}
        ids = [i.get("id") for i in (got.get("items") or [])]
        check("读回来的就是刚写的那条", ids == ["ZZ-TEST-1"], str(ids))
        check("非白名单字段被挡在磁盘外",
              not any("password" in i for i in (got.get("items") or [])), str(got.get("items")))
    finally:
        requests.post(f"{B}/api/browse/server",
                      json={"items": old_items, "updated_at": ""}, timeout=30)

    # 总闸：关 → 403 → 开回来（finally 保证恢复）
    try:
        requests.post(f"{B}/api/browse/server/toggle", json={"enabled": False}, timeout=30)
        r_off = requests.post(f"{B}/api/browse/server",
                              json={"items": []}, timeout=30)
        check("存服务器总闸关闭时接口 403", r_off.status_code == 403,
              f"HTTP {r_off.status_code}")
        r_off2 = requests.get(f"{B}/api/browse/server", timeout=30)
        check("关闸时读接口也 403", r_off2.status_code == 403, f"HTTP {r_off2.status_code}")
    finally:
        requests.post(f"{B}/api/browse/server/toggle", json={"enabled": True}, timeout=30)
    r_on = requests.get(f"{B}/api/browse/server/status", timeout=30)
    check("总闸重新打开后恢复可用",
          r_on.status_code == 200
          and ((r_on.json() or {}).get("data") or {}).get("enabled") is True,
          f"HTTP {r_on.status_code}")

    # ── 右上角同步控件（前端）──────────────────────────────────────
    check("顶栏有同步控件（自动同步 + 立即同步）",
          'id="sync-auto"' in whtml and 'id="sync-now"' in whtml, "")
    check("未设置时点同步会跳去配置页",
          'location.hash = "#/work/webdev"' in whtml, "")
    check("同步目标按「谁配了用谁」判定",
          "function syncTargetOf()" in whtml
          and '"webdav"' in whtml and '"server"' in whtml and '"none"' in whtml, "")
    check("浏览记录一变就排一次自动同步",
          "if (okSaved) autoSyncKick();" in whtml, "")
    check("自动同步有节流（防连续浏览连发请求）",
          "AUTOSYNC_DELAY" in whtml and "clearTimeout(_syncTimer)" in whtml
          and "_syncPending" in whtml, "")
    check("手动同步走「下载→合并→上传」",
          "function syncPullAndPush" in whtml and "browseMerge(g.obj.items)" in whtml, "")
    check("同步状态与成败存浏览器（键名固定）",
          "lzkg.autosync.v1" in whtml, "")
    check("浏览记录后端模块在位",
          (Path(__file__).resolve().parent.parent / "lzkg" / "browserec.py").exists(), "")

    # ── 交互地图：前端必须真的挂上了交互层 ──────────────────────────
    webdir = Path(__file__).resolve().parent.parent / "lzkg" / "web" / "static"
    idx = webdir / "index.html"
    html = idx.read_text(encoding="utf-8") if idx.exists() else ""
    check("前端引用本地 Leaflet（不走 CDN）",
          "vendor/leaflet/leaflet.js" in html and "vendor/leaflet/leaflet.css" in html
          and "unpkg.com" not in html and "cdn.jsdelivr.net/npm/leaflet" not in html,
          "Leaflet 本地引用")
    check("前端调用 mapdata 点级接口", "/api/stats/mapdata" in html, "")
    check("前端有点选联动逻辑", "barhit" in html and "mapstPickRegion" in html, "")
    check("本地 Leaflet 文件在位",
          (webdir / "vendor" / "leaflet" / "leaflet.js").exists(), "")
    check("Leaflet 许可声明随包",
          (webdir / "vendor" / "leaflet" / "LICENSE").exists(), "")

    # ── 离线底图：数据在本地、图上真有内容 ────────────────────────────
    import json as _json
    from pathlib import Path as _Path
    prog = _Path(__file__).resolve().parent.parent
    bm = prog / "data" / "basemap"
    layers = sorted(p.name for p in bm.glob("*.geojson")) if bm.exists() else []
    # ★ 按**必需图层名**逐一断言，而不是「数量 ≥ N」——
    #   数量写法会随图层增删漂移（精简掉 3 个用不到的图层后就会假失败）。
    need_layers = [
        "ne_110m_admin_0_countries.geojson",   # 世界视图
        "ne_50m_admin_0_countries.geojson",    # 台湾 TWN（中国视图补用）
        "ne_50m_admin_1_states_provinces.geojson",  # 中国省级拼图
        "ne_10m_lakes.geojson",                 # 太湖
        "ne_10m_coastline.geojson",             # 海岸线
        "osm_qiantang_yuhang.geojson",          # 钱塘江 + 余杭区界
    ]
    miss_layers = [n for n in need_layers if n not in layers]
    check("离线底图必需图层齐全", not miss_layers,
          f"缺 {miss_layers}（现有 {len(layers)} 个：{layers}）")
    check("底图含太湖图层", any("lakes" in n for n in layers), str(layers))
    geo_json = prog / "data" / "遗址坐标.json"
    check("机读坐标文件存在", geo_json.exists(), str(geo_json.name))
    if geo_json.exists():
        gj = _json.loads(geo_json.read_text(encoding="utf-8"))
        sg = gj.get("site_group") or {}
        # 良渚遗址群范围（WGS84）：经度 119.9~120.1、纬度 30.3~30.5
        check("遗址群范围框在文献坐标内",
              119.90 <= sg.get("lon_min", 0) <= 119.99
              and 120.02 <= sg.get("lon_max", 0) <= 120.09
              and 30.37 <= sg.get("lat_min", 0) <= 30.39
              and 30.42 <= sg.get("lat_max", 0) <= 30.45,
              f"lon {sg.get('lon_min')}~{sg.get('lon_max')} "
              f"lat {sg.get('lat_min')}~{sg.get('lat_max')}")
        check("坐标点都带出处", all(p.get("source") for p in gj.get("points", [])),
              str([p.get("source") for p in gj.get("points", [])]))
        check("无坐标的遗址已列明", len(gj.get("sites_without_coord", [])) == 18,
              str(len(gj.get("sites_without_coord", []))))
    # 图里要有底图颜色（不是纯空白）：PNG 体积与像素多样性
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(r.content)).convert("RGB")
        colors = im.getcolors(maxcolors=100000)
        check("地图含离线底图（颜色数 > 200）", len(colors) > 200, f"{len(colors)} 色 / {im.size}")
        check("地图为三联横幅（宽 > 2000px）", im.size[0] > 2000, str(im.size))
    except Exception as e:
        check("地图含离线底图（颜色数 > 200）", False, str(e)[:80])


def test_browse_server():
    """浏览记录存服务器：身份隔离、字段白名单、脏数据、上限、留上一版。

    ★ 为什么把 RECORD_ROOT 指到临时目录
      这里要真的写文件、真的覆盖，测完还得造脏数据。
      落到真实的 `data/record/` 会污染用户自己的记录 → 一律用临时目录，
      测完整棵删掉，真实目录一个字节都不动。
    """
    section("浏览记录存服务器（模块层：隔离与白名单）")
    import shutil as _sh
    import tempfile

    from lzkg import browserec as BR
    from lzkg.auth import IMPLICIT_ADMIN, Principal

    tmp = Path(tempfile.mkdtemp(prefix="lzkgrec_"))
    old_root = BR.RECORD_ROOT
    BR.RECORD_ROOT = tmp / "record"
    try:
        p1 = Principal(user_id=901, username="alice", role="member", token="t1")
        p2 = Principal(user_id=902, username="bob", role="member", token="t2")
        check("本机免登录管理员落到 admin 目录",
              BR._slug(IMPLICIT_ADMIN) == "admin", BR._slug(IMPLICIT_ADMIN))
        check("不同用户的目录不串",
              BR._slug(p1) != BR._slug(p2), BR._slug(p1) + " / " + BR._slug(p2))
        check("用户名里的路径字符被清洗",
              ".." not in BR._slug(
                  Principal(user_id=903, username="../../etc", role="member", token="t3")),
              BR._slug(Principal(user_id=903, username="../../etc",
                                 role="member", token="t3")))

        r = BR.save(p1, {"items": [
            {"kind": "lit", "id": "LIT-1", "title": "甲", "year": "2020", "ts": 100},
            {"kind": "lit", "id": "LIT-2", "title": "乙", "year": "2021", "ts": 200},
        ], "updated_at": "x"})
        check("写入 2 条", r["count"] == 2, str(r.get("count")))
        g1 = BR.load(p1)
        check("读回按 ts 升序", [i["id"] for i in g1["items"]] == ["LIT-1", "LIT-2"],
              str([i["id"] for i in g1["items"]]))

        g2 = BR.load(p2)
        check("另一个用户读不到别人的记录",
              g2["count"] == 0 and g2["exists"] is False,
              f'count={g2["count"]} exists={g2["exists"]}')

        BR.save(p1, {"items": [
            {"kind": "lit", "id": "LIT-9", "title": "丙", "year": "2022", "ts": 300,
             "password": "leak-me", "token": "leak-token", "secret": "nope"},
        ]})
        raw = (BR.record_dir(p1) / BR.FILE_NAME).read_text(encoding="utf-8")
        check("非白名单字段一个都不落盘",
              "leak-me" not in raw and "leak-token" not in raw
              and "secret" not in raw and "password" not in raw, "")
        check("白名单字段保留（中文不坏）",
              "LIT-9" in raw and "丙" in raw, "")

        r3 = BR.save(p1, {"items": [None, {"no_id": 1}, {"id": "LIT-8", "ts": 1}]})
        check("脏数据被丢弃且不抛异常",
              r3["count"] == 1 and r3["dropped"] >= 2,
              f'count={r3["count"]} dropped={r3["dropped"]}')

        big = [{"id": f"X{i}", "ts": i} for i in range(600)]
        r4 = BR.save(p1, {"items": big})
        check("超过 500 条只留最新的 500", r4["count"] == 500, str(r4["count"]))
        check("覆盖时把上一版留成 prev", (BR.record_dir(p1) / BR.PREV_NAME).exists(), "")

        try:
            BR.save(p1, {"items": "not-a-list"})
            check("items 不是数组时必须报错", False, "居然没报错")
        except BR.BrowseRecError:
            check("items 不是数组时必须报错", True, "")

        # 状态里不能出现本机绝对路径（换台机器就露馅）
        st = BR.status(p1)
        check("状态里只给相对路径", not str(st.get("dir", "")).startswith(("C:", "D:", "E:")),
              str(st.get("dir")))
    finally:
        BR.RECORD_ROOT = old_root
        _sh.rmtree(tmp, ignore_errors=True)


def test_extra():
    """组员实测报的两次错，根因各修一处，这里各留一条判据。

    ① 「上传失败：HTTP 404」→ 坚果云根目录不可写、又不自动建父目录。
       修法是「存放目录 + 上传前 MKCOL」，用本地 mock 把成功路径真跑一遍。
    ② 「文件名不合法」→ 用户粘进来的是完整路径（`下载NDM\\xxx.json`），
       前端 `wdSafeName()` 只取最后一段；再从 html 里提取**真实源码**执行验证。

    ★ 为什么用子进程跑
      两者各自要起本地 mock 服务器 / 调 node，隔在自己进程里更干净，
      失败时也能直接拿到它们的完整输出。
    """
    section("补充实测（WebDAV 全链路 / 前端文件名兜底）")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(PROGRAM_ROOT)

    def _run(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=180, env=env)
        except (subprocess.TimeoutExpired, OSError):
            return None

    def _last_line(p):
        if p is None:
            return "超时或无法启动"
        out = (p.stdout or "") + (p.stderr or "")
        for ln in reversed(out.strip().splitlines()):
            if ln.strip():
                return ln.strip()[:120]
        return "（无输出）"

    p = _run([sys.executable, str(HERE / "test_webdav_live.py")])
    check("WebDAV 上传/下载全链路（本地 mock 服务器）",
          p is not None and p.returncode == 0, _last_line(p))

    node = shutil.which("node")
    if not node:
        print("  SKIP 前端逻辑测试：本机没有 node（不影响其余判据）")
        return
    for script, name in (
        ("test_wdsafename.js",
         "前端文件名兜底 wdSafeName（含整页内联脚本语法门禁）"),
        ("test_browsemerge.js",
         "浏览记录合并 browseMerge（恢复/导入不覆盖本地）"),
    ):
        q = _run([node, str(HERE / script)])
        check(name, q is not None and q.returncode == 0, _last_line(q))


def main():
    print("良渚文化文献目录站 · 验收")
    # ★ 没有库就别硬跑数据层判据 ——
    #   公开仓库（脱敏发布版）里**不带数据库**（含 CNKI 机构订阅全文，不能公开），
    #   克隆下来的人第一件事就是 `python -m lzkg migrate`。
    #   这时应该**明确告诉他去建库**，而不是抛一堆连接失败让人以为程序坏了。
    try:
        from lzkg.config import get_config
        has_db = get_config().db_path.exists()
    except Exception:
        has_db = False
    if has_db:
        test_db()
        test_tag_normalize()
        test_search()
        test_graph()
        test_access()
        test_browse_server()
    else:
        print("\n  ⚠ 没有找到数据库（程序/data/lzkg.db）——跳过**依赖数据**的判据。")
        print("    先建库：python -m lzkg set-source \"<你的 04_专题研究>\" && python -m lzkg migrate")
        print("    （公开仓库不带库：库里有 CNKI 机构订阅的文献全文，不能公开外传。）")
    test_extra()
    if "--layout" in sys.argv or "--all" in sys.argv:
        if has_db:
            test_layout()
    if "--api" in sys.argv or "--all" in sys.argv:
        if has_db:
            test_api()
        else:
            print("\n  SKIP API 判据：需要先建库并启动服务")
    print(f"\n=== 结果：通过 {PASS} / 失败 {FAIL} ===")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
