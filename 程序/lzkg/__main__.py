# -*- coding: utf-8 -*-
"""命令行入口。

    python -m lzkg migrate      # 迁移数据（幂等，可重复跑）
    python -m lzkg serve        # 启动网站
    python -m lzkg stats        # 看库里的统计
    python -m lzkg doctor       # 自检：源目录/依赖/数据库
    python -m lzkg export nbkg  # 导出主库字段映射 CSV
    python -m lzkg rebuild-graph # 重建图谱
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import get_config


def _cfg():
    return get_config()


def cmd_migrate(args):
    from .migrate import migrate
    rep = migrate(json_full=not args.json_head, json_head=args.json_head)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args):
    import uvicorn
    cfg = _cfg()
    host = args.host or cfg.host
    port = args.port or cfg.port
    print(f"[lzkg] 打开 http://{host}:{port}/")
    print(f"[lzkg] 数据源：{cfg.source_root}")
    # ★ MCP 的 HTTP 形态挂到**同一个**应用上（路径 /mcp，给公网智能体）。
    #   挂不上也不影响网站照常跑（只是没有 /mcp 这个入口）。
    try:
        from .app import app as _app
        from . import mcp_server
        if mcp_server.attach_to_host_app(_app):
            print(f"[lzkg] MCP HTTP 已挂载：http://{host}:{port}/mcp")
    except Exception as e:
        print(f"[lzkg] MCP 没挂上（不影响网站）：{type(e).__name__}: {e}")
    uvicorn.run("lzkg.app:app", host=host, port=port, log_level="info")
    return 0


def cmd_mcp(args):
    """MCP 服务（契约 v3 §4）。

        python -m lzkg mcp            # stdio（给本地智能体）
        python -m lzkg mcp --http     # HTTP（挂到本机端口，供本地测试）
    """
    from . import mcp_server
    if args.http:
        import uvicorn
        cfg = _cfg()
        host = args.host or cfg.host
        port = args.port or (cfg.port + 1)
        app = mcp_server.build_standalone_http_app()
        print(f"[lzkg] MCP HTTP: http://{host}:{port}/mcp")
        uvicorn.run(app, host=host, port=port, log_level="info")
        return 0
    return mcp_server.run_stdio()


def cmd_stats(args):
    from .db import connect, count, get_meta
    c = connect()
    try:
        meta = get_meta(c)
        print(f"数据库：{_cfg().db_path}")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        for t in ("literatures", "images", "tags", "lit_tags", "img_tags",
                  "raw_json", "docs_fts", "graph_nodes", "graph_edges"):
            print(f"  表 {t}: {count(c, t)}")
    finally:
        c.close()
    return 0


def cmd_doctor(args):
    import importlib
    cfg = _cfg()
    ok = True
    print("=== 依赖 ===")
    for m in ("fastapi", "uvicorn", "networkx", "pyvis", "openpyxl"):
        try:
            mod = importlib.import_module(m)
            print(f"  {m}: {getattr(mod, '__version__', 'ok')}")
        except ImportError:
            print(f"  {m}: ★缺失")
            ok = False
    print("=== 数据源 ===")
    s = cfg.summary()
    for k, v in s.items():
        print(f"  {k}: {v}")
    for k in ("lit_xlsx_exists", "img_xlsx_exists", "md_dir_exists", "raw_json_dir_exists"):
        if not s[k]:
            ok = False
    print("=== 数据库 ===")
    from .db import connect, count, init_db
    try:
        c = connect()
        caps = init_db(c)
        print(f"  trigram: {caps.get('fts_trigram')}")
        print(f"  literatures: {count(c, 'literatures')}")
        if count(c, "literatures") == 0:
            print("  ★库是空的 —— 先跑 python -m lzkg migrate")
            ok = False
        c.close()
    except Exception as e:
        print(f"  ★打不开库：{e}")
        ok = False
    print("\n结果：" + ("一切就绪" if ok else "有问题，见上面 ★"))
    return 0 if ok else 1


def cmd_export(args):
    from .db import connect
    from .nbkg import nbkg_export_rows
    import csv
    import io
    cfg = _cfg()
    out = Path(args.out) if args.out else (cfg.db_path.parent / f"良渚_主库字段映射导出_{time.strftime('%Y%m%d')}.csv")
    c = connect()
    try:
        rows = nbkg_export_rows(c)
    finally:
        c.close()
    if not rows:
        print("库里没有数据，先跑 migrate")
        return 1
    with io.open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(rows[0].keys()))
        for r in rows:
            w.writerow(["" if r.get(k) is None else r.get(k) for k in rows[0].keys()])
        w.writerow([""])
        w.writerow([f"口径说明：导出时间 {time.strftime('%Y-%m-%d %H:%M:%S')}；条数 {len(rows)}"])
    print(f"已导出 {len(rows)} 行 → {out}")
    return 0


def cmd_rebuild_graph(args):
    """重建图谱**并重算布局坐标**。

    ★ 图谱和布局是两件事，但必须一起做：
      图谱（谁和谁有关系）来自登记表；布局（画在哪）是算法产物。
      只重建图谱的话，**新节点没有 x/y** → 前端对它们退回力导向 →
      整张图一半固定一半会漂，比全都不固定还糟。
    """
    from .db import connect
    from .graph import build_graph_into_db
    from .layout import layout_into_db
    c = connect()
    try:
        rep = build_graph_into_db(c)
        rep["layout"] = layout_into_db(c, seed=args.seed)
    finally:
        c.close()
    print(rep)
    return 0


def cmd_rebuild_layout(args):
    """重算图谱的确定性坐标（graph_nodes.x / y）。

    ★ 布局和图谱是**两件事**：图谱（谁和谁有关系）来自登记表，布局（画在哪）
      是算法产物。图谱改了要 rebuild-graph，布局要 rebuild-layout。
    """
    from .db import connect
    from .layout import layout_into_db
    c = connect()
    try:
        rep = layout_into_db(c, seed=args.seed)
    finally:
        c.close()
    print(rep)
    return 0


def cmd_reindex(args):
    """重建词级索引（检索增强 E1）。

        python -m lzkg reindex

    ★ 词典是源数据、索引是派生物 —— 改完词典跑这条命令，索引就跟上。
    ★ 没装 jieba 时**不报错退出**：明确告诉用户"检索已退回整句精确模式"。
    """
    from .db import connect, init_db
    from . import segment
    conn = connect()
    try:
        init_db(conn)
        rep = segment.rebuild_index(conn, progress=lambda s: print(f"  {s}"))
    finally:
        conn.close()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep.get("ok") else 1


def cmd_segment_status(args):
    """看词级索引的状态（版本、词典指纹、是否过期）。"""
    from .db import connect
    from . import segment
    conn = connect()
    try:
        st = segment.index_status(conn)
    finally:
        conn.close()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


def cmd_set_source(args):
    """把数据源路径写进 config.json（对接包接手时第一条命令）。"""
    cfg = _cfg()
    try:
        p = cfg.set_source(args.path)
    except FileNotFoundError as e:
        print(f"[X] {e}")
        return 1
    print(f"数据源已设为：{p}")
    print("接下来：python -m lzkg migrate")
    return 0


def main(argv=None):
    # 输出重定向到文件时，Windows 默认 GBK 会把中文写成乱码 → 统一 UTF-8 + replace
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="lzkg", description="良渚文化文献目录站")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("migrate", help="迁移数据到 SQLite")
    p.add_argument("--json-head", type=int, default=0,
                   help="原始 JSON 只保留前 N 字符（0=保留全文）")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("serve", help="启动网站")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("stats", help="看库统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("doctor", help="自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("export", help="导出")
    p.add_argument("what", choices=["nbkg", "literatures", "images"])
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("rebuild-graph", help="重建图谱（同时重算布局坐标）")
    p.add_argument("--seed", type=int, default=42, help="布局随机种子（固定才可复现）")
    p.set_defaults(func=cmd_rebuild_graph)

    p = sub.add_parser("rebuild-layout", help="重算图谱的确定性布局坐标")
    p.add_argument("--seed", type=int, default=42, help="随机种子（固定才可复现）")
    p.set_defaults(func=cmd_rebuild_layout)

    p = sub.add_parser("reindex", help="重建词级索引（检索增强 E1）")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("segment-status", help="看词级索引状态（版本/词典指纹/是否过期）")
    p.set_defaults(func=cmd_segment_status)

    p = sub.add_parser("set-source", help="设定数据源目录（写进 config.json）")
    p.add_argument("path", help="04_专题研究 目录，或它的上一级目录")
    p.set_defaults(func=cmd_set_source)

    p = sub.add_parser("mcp", help="起 MCP 服务（stdio；--http 起 HTTP 形态）")
    p.add_argument("--http", action="store_true",
                   help="起 HTTP 形态（挂到本机端口，供本地测试）")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)
    p.set_defaults(func=cmd_mcp)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
