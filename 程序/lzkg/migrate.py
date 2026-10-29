# -*- coding: utf-8 -*-
"""数据迁移：登记表 xlsx + 全文 Markdown + 原始 JSON → SQLite。

来源（都在 04_专题研究/ 下）：
    05_登记表/00-最终版/文献基本信息登记表_修复.xlsx   → literatures / tags / lit_tags
    05_登记表/00-最终版/文献图像信息登记表_修复.xlsx   → images / img_tags
    03_文献全文/MinerU_markdown_LIT-*.md              → docs_fts（全文检索）
    02_解析结果/MinerU_json_LIT-*.json                → raw_json + raw_json_fts

设计取舍
--------
· **原始 JSON 全文入库**：JSON 单文件可达 MB 级，全量入库约 100 MB；
  这批文献的 JSON 是 4.0.12 的 docvortex 格式，文本量大且重复。
  做法是**入库但可裁剪**：正文保留全文（可检索），另存 size_bytes。
  若库体积过大，用 `--json-full` / `--json-head N` 控制（默认保留全文，
  因为用户明确要求「原始 JSON 全文可检索」）。
· **标签规范化**：见 `normalize_tags()`，规则写在函数 docstring 里。
· **不删源文件**：本模块只读源、只写库。
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .config import Config, get_config
from .db import connect, init_db, set_meta, get_meta, count

# ── 登记表列号（1-based，与 Excel 一致）────────────────────────────────
LIT_COLS = {
    "seq": 1, "lit_id": 2, "theme": 3, "doc_type": 4, "title": 5, "authors": 6,
    "lang": 7, "year": 8, "venue": 9, "publisher": 10, "pages_range": 11,
    "abstract": 12, "keywords": 13, "source_url": 14, "pdf_name": 15,
    "pdf_pages": 16, "pdf_path": 17, "body_id": 18, "md_path": 19,
    "parse_status": 20, "parse_note": 21, "open_type": 22, "period_tags": 23,
    "region_tags": 24, "topic_tags": 25, "lineage_tags": 26, "clc": 27,
    "subject_terms": 28, "recorder": 29, "record_date": 30, "review_status": 31,
    "remark": 32, "source_type": 33, "source_evidence": 34,
}

IMG_COLS = {
    "img_id": 2, "lit_id": 3, "theme": 4, "filename": 5, "path": 6,
    "img_title": 7, "img_type": 8, "obj_type": 9, "orig_page": 10,
    "pdf_page": 11, "fig_no": 12, "caption": 13, "ai_desc": 14,
    "ai_objects": 15, "ai_tool": 16, "ai_prompt": 17, "review_status": 18,
    "reviewer": 19, "review_date": 20, "period_tags": 21, "region_tags": 22,
    "topic_tags": 23, "lineage_tags": 24, "storage_id": 25, "remark": 26,
}

from . import vocab

TAG_DIMS = ["period", "region", "topic", "lineage"]

# 已知「一码多义」的码位：按名称拆，代码标记为不可靠
UNRELIABLE_CODES = {"R-0199"}

_CODE_RE = re.compile(r"^([A-Za-z]-\d{4})\s+(.+)$")
_BRACKET_RE = re.compile(r"[（(]([^）)]*)[）)]\s*$")
_SPLIT_RE = re.compile(r"[；;、]|\s{2,}")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    return str(v).strip()


def _read_sheet(path: Path):
    """读登记表 → (表头列表, 数据行列表)。

    ★ P0-4：表头定位已拆到 `sheetio.find_header_row`（按关键列名定位），
    这里只是迁移专用的薄封装——**迁移与导入共用同一套列名判据**，
    避免「迁移按第 4 行、导入按列名」两套逻辑各走各的。
    """
    import openpyxl
    from .sheetio import find_header_row
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    table = "literatures" if "文献" in path.name else "images"
    try:
        hidx = find_header_row(rows, table)
    except KeyError:
        # 兜底：老启发式（表结构是我们自己维护的，位置也已知）
        hidx = next((i for i, r in enumerate(rows[:12])
                     if sum(1 for v in r if v not in (None, "")) > 5), 0)
    header = [("" if v is None else str(v).strip()) for v in rows[hidx]]
    data = [[_cell(v) for v in r] for r in rows[hidx + 1:]
            if any(v not in (None, "") for v in r)]
    return header, data


# ── 标签规范化 ──────────────────────────────────────────────────────────
def normalize_tags(raw: str, dim: str) -> list[dict]:
    """把一个标签单元格拆成规范化标签列表。

    规则（按顺序）：
      1. 按 `；`/`;`/`、` 拆多值
      2. 每条 trim；去掉首尾的方括号/书名号
      3. `代码 名称` 形态 → 拆成 code / name（`R-0101 良渚古城`）
      4. 名称尾部的括号限定（`良渚文化（数字展示）`）→ 限定词**不进 name**，
         只保留在 raw_forms 里（限定词不是新的文化类型，否则会长出伪实体）
      5. **受控词表归并**（`lzkg/vocab.py`）：
         · 同义词归并（`墓葬` → `墓葬考古`）—— 只合并人工审定的等价词
         · 组合标签拆分（`凌家滩文化/红山文化` → 两个独立文化）
      6. **唯一码位**：按受控词表取该词的唯一码（`data/controlled_vocab_codes.json`）。
         原先那些「一码多义」的族系代号（S-0802/R-0199…）不再使用 ——
         它们底下藏了 54 个不同名称，按码归一会把「环太湖」和「文化创意」并成一个。
         受控词表里查不到的词 → 码留空并标不可靠（界面据此提示不可作聚合依据）。
    返回 [{tag_id, dim, code, name, raw_form, code_reliable}]
    """
    raw = (raw or "").strip()
    if not raw or raw in {"通用", "待定"}:
        # 「通用」不是一个具体标签，不建节点（避免图谱里出现无意义的大节点）
        return []
    out = []
    for piece in _SPLIT_RE.split(raw):
        piece = piece.strip().strip("【】[]")
        if not piece:
            continue
        code, name = "", piece
        m = _CODE_RE.match(piece)
        if m:
            code, name = m.group(1), m.group(2).strip()
        # 名称尾部的限定后缀
        qual = ""
        bm = _BRACKET_RE.search(name)
        if bm:
            qual = bm.group(1).strip()
            name = name[: bm.start()].strip()
        if not name:
            name = piece
        # 受控词表归并：拆分 → 同义归并 → 规范名列表
        for cname in vocab.canonical(dim, name):
            uniq_code = vocab.code_for(dim, cname)
            if uniq_code:
                c, reliable = uniq_code, 1
            else:
                # 受控词表里没有这个词 → 沿用原码但标不可靠
                c = code
                reliable = 0 if (code and code in UNRELIABLE_CODES) else (
                    0 if code else 1)
            # tag_id：dim + 名称（名称是身份），代码仅作辅助
            tag_id = f"{dim}:{cname}"
            out.append({
                "tag_id": tag_id, "dim": dim, "code": c, "name": cname,
                "raw_form": piece + (f"（{qual}）" if qual else ""),
                "code_reliable": reliable,
            })
    return out


def merge_tag_lists(all_lists: Iterable[list[dict]]) -> dict:
    """多来源合并：同 tag_id 保留一份，raw_forms 累加不同写法。"""
    merged: dict[str, dict] = {}
    for lst in all_lists:
        for t in lst:
            cur = merged.get(t["tag_id"])
            if cur is None:
                cur = dict(t)
                cur["raw_forms"] = [t["raw_form"]]
                merged[t["tag_id"]] = cur
            else:
                if t["raw_form"] not in cur["raw_forms"]:
                    cur["raw_forms"].append(t["raw_form"])
                # 代码：优先留可靠的
                if cur.get("code_reliable") == 0 and t.get("code_reliable") == 1:
                    cur["code"] = t["code"]
                    cur["code_reliable"] = 1
                elif not cur.get("code") and t.get("code"):
                    cur["code"] = t["code"]
    return merged


# ── 主流程 ──────────────────────────────────────────────────────────────
def migrate(cfg: Config | None = None, json_full: bool = True,
            json_head: int = 0, verbose: bool = True) -> dict:
    cfg = cfg or get_config()
    src = cfg.source_root
    if not src.is_dir():
        raise RuntimeError(f"找不到数据源目录：{src}")
    if not cfg.lit_xlsx.exists():
        raise RuntimeError(f"找不到文献登记表：{cfg.lit_xlsx}")

    t0 = time.time()
    conn = connect(cfg.db_path)
    caps = init_db(conn)

    # 清空重建（迁移是可重复执行的；源文件不动）
    for t in ("lit_tags", "img_tags", "tags", "images", "literatures",
              "graph_nodes", "graph_edges"):
        conn.execute(f"DELETE FROM {t}")
    # raw_json 用外部内容 FTS：直接 DROP 重建，保证表结构与索引一致
    conn.execute("DROP TABLE IF EXISTS raw_json_fts")
    conn.execute("DROP TABLE IF EXISTS raw_json")
    conn.execute("""CREATE TABLE raw_json (
        rowid INTEGER PRIMARY KEY, lit_id TEXT, filename TEXT,
        size_bytes INTEGER, text TEXT, UNIQUE(lit_id, filename))""")
    conn.execute("""CREATE VIRTUAL TABLE raw_json_fts USING fts5(
        text, content='raw_json', content_rowid='rowid', tokenize='trigram')""")
    conn.execute("DELETE FROM docs_fts")
    conn.commit()

    report: dict = {"caps": caps}

    # 1) 文献
    _, lit_rows = _read_sheet(cfg.lit_xlsx)
    lit_data = []
    for r in lit_rows:
        row = {}
        for field, colno in LIT_COLS.items():
            row[field] = r[colno - 1] if len(r) >= colno else ""
        if not row["lit_id"]:
            continue
        lit_data.append(row)
    conn.executemany(
        f"INSERT OR REPLACE INTO literatures ({','.join(LIT_COLS.keys())}) "
        f"VALUES ({','.join('?' * len(LIT_COLS))})",
        [tuple(row[f] for f in LIT_COLS) for row in lit_data])
    report["literatures"] = len(lit_data)

    # 2) 图像
    img_data = []
    if cfg.img_xlsx.exists():
        _, img_rows = _read_sheet(cfg.img_xlsx)
        for r in img_rows:
            row = {}
            for field, colno in IMG_COLS.items():
                row[field] = r[colno - 1] if len(r) >= colno else ""
            if not row["img_id"]:
                continue
            img_data.append(row)
    conn.executemany(
        f"INSERT OR REPLACE INTO images ({','.join(IMG_COLS.keys())}) "
        f"VALUES ({','.join('?' * len(IMG_COLS))})",
        [tuple(row[f] for f in IMG_COLS) for row in img_data])
    report["images"] = len(img_data)

    # 3) 标签规范化 + 关联
    tag_map: dict[str, dict] = {}
    lit_tag_rows = []
    for row in lit_data:
        lists = []
        for dim in TAG_DIMS:
            lists.append(normalize_tags(row.get(f"{dim}_tags", ""), dim))
        merged = merge_tag_lists(lists)
        for tid, t in merged.items():
            tag_map.setdefault(tid, t)
            lit_tag_rows.append((row["lit_id"], tid, t["dim"]))
    img_tag_rows = []
    for row in img_data:
        lists = []
        for dim in TAG_DIMS:
            lists.append(normalize_tags(row.get(f"{dim}_tags", ""), dim))
        # 图像类型 / 对象类型也作为标签维度
        lists.append(normalize_tags(row.get("img_type", ""), "img_type"))
        lists.append(normalize_tags(row.get("obj_type", ""), "obj_type"))
        merged = merge_tag_lists(lists)
        for tid, t in merged.items():
            tag_map.setdefault(tid, t)
            img_tag_rows.append((row["img_id"], tid, t["dim"]))

    lit_counts: dict[str, int] = defaultdict(int)
    for _, tid, _ in lit_tag_rows:
        lit_counts[tid] += 1
    img_counts: dict[str, int] = defaultdict(int)
    for _, tid, _ in img_tag_rows:
        img_counts[tid] += 1

    # ── 自动检测「一码多名称」的码位 ──────────────────────────────
    # 实测：空间分区有 R-0199（环太湖/中国综合/文化创意…9 种），
    #      主题分类有 S-0801（11 种）/S-0802（18 种）/S-0803（9 种）等。
    # 这些码位**不能按代码归一**——否则「环太湖」和「文化创意」会被并成同一个东西。
    # 处置：照旧按**名称**建独立标签（信息不丢），但把这些码位标记为
    #      code_reliable=0，界面/报告据此提示「该码位不可作聚合依据」。
    # 为什么自动检测而不是硬编码：码位表会随教师发布的新版词表变化，
    # 硬编码只能挡住眼前这几个。
    names_by_code: dict[tuple, set] = defaultdict(set)
    for t in tag_map.values():
        if t.get("code"):
            names_by_code[(t["dim"], t["code"])].add(t["name"])
    multi_codes = {k for k, v in names_by_code.items() if len(v) > 1}
    for t in tag_map.values():
        if (t["dim"], t.get("code", "")) in multi_codes:
            t["code_reliable"] = 0
    # 硬编码的名单也保留（万一某码位当前只出现一种名称，将来会变多义）
    for t in tag_map.values():
        if t.get("code") in UNRELIABLE_CODES:
            t["code_reliable"] = 0

    conn.executemany(
        "INSERT OR REPLACE INTO tags (tag_id, dim, code, name, lit_count, img_count, raw_forms, code_reliable) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(tid, t["dim"], t.get("code", ""), t["name"], lit_counts.get(tid, 0),
          img_counts.get(tid, 0), "；".join(t.get("raw_forms", [])), t.get("code_reliable", 1))
         for tid, t in tag_map.items()])
    conn.executemany("INSERT OR REPLACE INTO lit_tags (lit_id, tag_id, dim) VALUES (?,?,?)", lit_tag_rows)
    conn.executemany("INSERT OR REPLACE INTO img_tags (img_id, tag_id, dim) VALUES (?,?,?)", img_tag_rows)
    report["tags"] = len(tag_map)
    report["lit_tag_links"] = len(lit_tag_rows)
    report["img_tag_links"] = len(img_tag_rows)
    report["multi_semantics_codes"] = sorted(f"{d}:{c}" for d, c in multi_codes)

    # 4) 全文 Markdown → docs_fts
    md_files = sorted(cfg.md_dir.glob("*.md")) if cfg.md_dir.is_dir() else []
    lit_by_md = {}
    for row in lit_data:
        p = (row.get("md_path") or "").strip()
        if p:
            lit_by_md[Path(p).name] = row["lit_id"]
    fts_rows = []
    md_chars = {}
    md_pages = {}
    md_imgs = {}
    for f in md_files:
        text = f.read_text(encoding="utf-8", errors="replace")
        lit_id = lit_by_md.get(f.name)
        if lit_id is None:
            m = re.search(r"LIT-(\d{6})", f.name)
            lit_id = f"LIT-{m.group(1)}" if m else None
        if not lit_id:
            continue
        fts_rows.append((f.stem, text, lit_id))
        md_chars[lit_id] = len(text)
        md_pages[lit_id] = len(re.findall(r"<!-- page \d+ of \d+ -->", text))
        md_imgs[lit_id] = len(re.findall(r"!\[[^\]]*\]\(doc:", text))
    if fts_rows:
        conn.executemany("INSERT INTO docs_fts (title, content, lit_id) VALUES (?,?,?)", fts_rows)
    report["docs_fts"] = len(fts_rows)
    report["md_files"] = len(md_files)
    conn.executemany(
        "UPDATE literatures SET md_chars=?, md_pages=?, md_img_refs=? WHERE lit_id=?",
        [(md_chars.get(r["lit_id"], 0), md_pages.get(r["lit_id"], 0),
          md_imgs.get(r["lit_id"], 0), r["lit_id"]) for r in lit_data])

    # 5) 原始 JSON 全文 → raw_json + raw_json_fts
    json_files = sorted(cfg.raw_json_dir.glob("MinerU_json_LIT-*.json")) if cfg.raw_json_dir.is_dir() else []
    raw_rows = []
    total_json_bytes = 0
    for f in json_files:
        m = re.search(r"LIT-(\d{6})", f.name)
        if not m:
            continue
        lit_id = f"LIT-{m.group(1)}"
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        size = f.stat().st_size
        total_json_bytes += size
        if not json_full and json_head:
            text = text[:json_head]
        raw_rows.append((lit_id, f.name, size, text))
    if raw_rows:
        conn.executemany("INSERT OR REPLACE INTO raw_json (lit_id, filename, size_bytes, text) VALUES (?,?,?,?)", raw_rows)
        # 外部内容 FTS：重建索引（不复制正文）
        conn.execute("INSERT INTO raw_json_fts(raw_json_fts) VALUES('rebuild')")
    report["raw_json"] = len(raw_rows)
    report["raw_json_bytes"] = total_json_bytes

    # 6) meta
    set_meta(conn, {
        "schema_version": "1.0",
        "migrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_root": str(src),
        "lit_count": len(lit_data),
        "img_count": len(img_data),
        "tag_count": len(tag_map),
        "raw_json_count": len(raw_rows),
        "docs_fts_count": len(fts_rows),
        "md_total_chars": sum(md_chars.values()),
        "fts_trigram": caps.get("fts_trigram"),
    })
    conn.commit()

    # 7) 图谱（表派生，零抽取）
    from .graph import build_graph_into_db
    g = build_graph_into_db(conn)
    report["graph"] = g

    # 7.1) 确定性布局坐标（前端 physics:false 直接用这组坐标画）
    #   ★ 必须跟着迁移一起算：新环境只 migrate 完是**没有坐标**的，
    #   图谱页会退回力导向 → 每次打开位置都漂。
    #   单独跑 `python -m lzkg rebuild-layout` 也能重算（改图谱后要跑）。
    try:
        from .layout import layout_into_db
        lay = layout_into_db(conn)
        report["layout"] = lay
    except Exception as e:  # 布局失败不该让整个迁移失败
        report["layout"] = {"error": str(e)}
        conn.rollback()

    conn.commit()
    # 收尾：把 WAL 落盘并压缩，避免留下巨大的 -wal 文件
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    except sqlite3.Error as e:
        report["vacuum_note"] = str(e)
    conn.close()
    report["seconds"] = round(time.time() - t0, 1)
    if verbose:
        print(f"[migrate] 完成，用时 {report['seconds']}s")
        for k in ("literatures", "images", "tags", "lit_tag_links", "img_tag_links",
                  "docs_fts", "raw_json", "raw_json_bytes"):
            print(f"    {k}: {report.get(k)}")
        print(f"    graph: {g}")
    return report


if __name__ == "__main__":
    migrate()
