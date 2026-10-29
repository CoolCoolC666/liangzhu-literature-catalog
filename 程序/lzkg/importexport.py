# -*- coding: utf-8 -*-
"""F3 导入导出：登记表预检 → 提案 → 审核 → 写主库，以及 xlsx / nbkg 导出。

设计要点
--------
1. **预检不写主库**：只把「有变化的行」写进 `staging`（status='pending'），
   返回 `staging_id`（批次号），由人在「提案与审核」里逐条批准。
2. **列名按名字匹配**（不是按列序）：复用 `sheetio.read_rows_by_column`
   的表头定位 + `registry` 的列定义，**不另写一套**。
3. **批次号**：`IMP-YYYYMMDD-NNNN`。批次号存在 `staging.batch`，
   于是 `POST /api/import/apply` 能按批次挑行，而不必改表结构语义。
4. **写主库只改它该改的列**：upsert 时按列定义给的 `field` 白名单，
   派生列（md_chars / md_pages / md_img_refs）不参与导入 —— 那是迁移算的。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .common import CST, now_str, today_str
from .config import get_config
from .db import connect, count, get_meta, set_meta
from .registry import (VARIANT_FULL, columns, kind, norm_name, pk_field,
                       strip_star)
from .sheetio import norm, read_rows_by_column

KIND_TABLES = ("literatures", "images")

# ── 契约 v3 §3.4：提案的种类 ──────────────────────────────────────────
# `parsed` = 一份 PDF 的解析产物 + 题录；批准后产物从**暂存区**搬进源数据目录。
# ★ 它**不对应任何一张表**，所以不能走 `_upsert` 那条路（那张表是登记表的两张）。
KIND_PARSED = "parsed"


# ── 批次号 ──────────────────────────────────────────────────────────────
def next_batch_id(conn: sqlite3.Connection) -> str:
    """`IMP-YYYYMMDD-NNNN`，NNNN 是当天的批次数（现算，不写死）。"""
    prefix = f"IMP-{today_str()}-"
    n = conn.execute(
        "SELECT COUNT(DISTINCT batch) c FROM staging WHERE batch LIKE ?",
        (prefix + "%",)).fetchone()["c"]
    return f"{prefix}{n + 1:04d}"


# ── 值归一（比较用）─────────────────────────────────────────────────────
def _norm_val(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _cell_val(row: dict, col: str) -> str:
    return _norm_val(row.get(col, ""))


def _pk_of(table: str, row: dict) -> str:
    """从一行（按列名的 dict）里取主键值。"""
    pk_col = kind(table)["pk"]
    pk_n = norm(pk_col)
    for k, v in row.items():
        if norm(k) == pk_n:
            return _norm_val(v)
    return ""


def _row_to_fields(table: str, row: dict, col2name: dict) -> dict:
    """行（按列名）→ 库字段 dict（只含列定义里声明了 field 的列）。"""
    spec = kind(table)
    out = {}
    for col, val in row.items():
        c = spec["by_name"].get(norm(col))
        if c and c.get("field"):
            out[c["field"]] = val
    return out


# ── 预检 ────────────────────────────────────────────────────────────────
def preview_import(conn: sqlite3.Connection, path: Path, table: str,
                   filename: str, operator: str = "") -> dict:
    """逐行 diff 预检。**不写主库**，但把有变化的行写进 `staging`。

    返回契约 §2.1 的结构。
    """
    rows, meta = read_rows_by_column(path, table)
    spec = kind(table)
    pk_field_name = pk_field(table)
    pk_col = norm(spec["pk"])

    missing_required = meta.get("missing_required") or []
    if missing_required:
        raise ValueError("缺少必填列：" + "、".join(missing_required))

    col2name = meta["col2name"]
    # 主键列名 → 库字段的映射（用于在库里查旧行）
    db_rows = {}
    for r in conn.execute(f"SELECT * FROM {table}"):
        db_rows[r[pk_field_name]] = dict(r)

    diffs = []
    n_new = n_changed = n_same = 0
    staging_rows = []
    for row in rows:
        target_id = _pk_of(table, row)
        old = db_rows.get(target_id) if target_id else None
        fields = []
        for col, val in row.items():
            c = spec["by_name"].get(norm(col))
            if not c:
                continue
            field = c.get("field")
            if not field:
                # ★ 这一列**没有对应的库字段**（图像表的「序号」就是这种：
                #   它是表内行号，不是数据列）→ 库里没有它可比。
                #   若照样拿 "" 去比，每一行都会被误报成「变了」。
                continue
            new_v = _norm_val(val)
            if old is None:
                old_v = ""
            else:
                old_v = _norm_val(old.get(field, ""))
            if new_v == old_v:
                continue
            fields.append({"col": norm_name(c["name"]), "old": old_v, "new": new_v,
                           "required": bool(c.get("required"))})
        if old is None:
            op = "new"
            n_new += 1
        elif fields:
            op = "changed"
            n_changed += 1
        else:
            op = "same"
            n_same += 1
            continue
        entry = {"target_id": target_id, "op": op, "fields": fields}
        diffs.append(entry)
        # 有变化的行 → 提案（payload = 整行，按库字段名）
        payload = _row_to_fields(table, row, col2name)
        staging_rows.append((table, target_id, payload, op, fields, operator))

    # ── 写 staging（有变化的行才写）────────────────────────────────
    batch = next_batch_id(conn)
    now = now_str()
    if staging_rows:
        conn.executemany(
            "INSERT INTO staging (kind, target_id, payload, diff, proposer, status, "
            "created_at, batch, filename) VALUES (?,?,?,?,?,?,?,?,?)",
            [(k, target_id,
              json.dumps(payload, ensure_ascii=False),
              json.dumps({"op": op, "fields": fields}, ensure_ascii=False),
              operator or "local", "pending", now, batch, filename)
             for k, target_id, payload, op, fields, operator in staging_rows])
        conn.commit()

    expected = len(columns(table, VARIANT_FULL))
    found = len([v for v in meta.get("header_raw", []) if v
                 and norm(v) in spec["by_name"]])
    return {
        "staging_id": batch,
        "kind": table,
        "filename": filename,
        "columns_found": found,
        "columns_expected": expected,
        "missing_required": missing_required,
        "unknown_columns": meta.get("unknown_columns", []),
        "summary": {"rows_total": len(rows), "rows_new": n_new,
                    "rows_changed": n_changed, "rows_same": n_same},
        "diffs": diffs,
        "staged": len(staging_rows),
        "note": "预检不写主库；确认后在「提案与审核」里逐条批准。",
    }


# ── 写主库 ──────────────────────────────────────────────────────────────
def _upsert(conn: sqlite3.Connection, table: str, target_id: str,
            payload: dict) -> str:
    """按主键 upsert 一行。返回 'inserted' / 'updated' / 'skipped'。

    ★ 只写**列定义里声明了 field 且在库里存在**的列；
      派生列（md_chars / md_pages / md_img_refs）不参与。
    """
    if not target_id:
        return "skipped"
    spec = kind(table)
    fields = []
    for c in spec["columns"]:
        f = c.get("field")
        if f and f in payload:
            fields.append(f)
    if not fields:
        return "skipped"
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    fields = [f for f in fields if f in cols]
    if not fields:
        return "skipped"
    exists = conn.execute(f"SELECT 1 FROM {table} WHERE {pk_field(table)} = ?",
                          (target_id,)).fetchone()
    vals = [payload.get(f, "") for f in fields]
    if exists:
        sets = ", ".join(f"{f}=?" for f in fields)
        conn.execute(f"UPDATE {table} SET {sets} WHERE {pk_field(table)}=?",
                     vals + [target_id])
        return "updated"
    placeholders = ", ".join("?" * len(fields))
    conn.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({placeholders})",
                 vals)
    return "inserted"


def apply_staging(conn: sqlite3.Connection, batch: str,
                  target_ids: list[str] | None = None,
                  operator: str = "local", reviewer: str = "",
                  note: str = "") -> dict:
    """把一批提案写进主库。`target_ids` 为空 → 该批次全部 pending 行。"""
    sql = "SELECT * FROM staging WHERE batch = ? AND status = 'pending'"
    params = [batch]
    if target_ids:
        sql += f" AND target_id IN ({','.join('?' * len(target_ids))})"
        params += list(target_ids)
    rows = [dict(r) for r in conn.execute(sql, params)]
    added = updated = skipped = 0
    by_kind: dict[str, list[dict]] = {}
    parsed_reports: list[dict] = []
    for r in rows:
        kind_ = r["kind"]
        if kind_ == KIND_PARSED:
            # 解析产物提案：搬产物 + upsert 文献行（幂等）
            rep = _approve_parsed(conn, r, reviewer or operator, note)
            parsed_reports.append(rep)
            conn.execute(
                "UPDATE staging SET status='approved', reviewer=?, review_note=?, reviewed_at=? "
                "WHERE id=?", (reviewer or operator, note, now_str(), r["id"]))
            by_kind.setdefault(kind_, []).append(r)
            continue
        payload = json.loads(r["payload"] or "{}")
        res = _upsert(conn, kind_, r["target_id"] or "", payload)
        if res == "inserted":
            added += 1
        elif res == "updated":
            updated += 1
        else:
            skipped += 1
        conn.execute(
            "UPDATE staging SET status='approved', reviewer=?, review_note=?, reviewed_at=? "
            "WHERE id=?", (reviewer or operator, note, now_str(), r["id"]))
        by_kind.setdefault(kind_, []).append(r)
    conn.commit()
    # meta 计数同步（现算，不写死）
    for t in KIND_TABLES:
        set_meta(conn, {f"{'lit' if t == 'literatures' else 'img'}_count": count(conn, t)})
    conn.commit()
    log_kind = next(iter(by_kind), "") if by_kind else ""
    log_id = _write_log(conn, _batch_filename(conn, batch) or batch, log_kind,
                        added, updated, skipped, operator)
    return {"applied": added + updated, "added": added, "updated": updated,
            "skipped": skipped, "batch": batch, "log_id": log_id,
            "parsed": parsed_reports}


def _batch_filename(conn: sqlite3.Connection, batch: str) -> str:
    row = conn.execute(
        "SELECT filename FROM staging WHERE batch = ? LIMIT 1", (batch,)).fetchone()
    return (row["filename"] if row else "") or ""


def _write_log(conn: sqlite3.Connection, filename: str, kind: str,
               added: int, updated: int, skipped: int, operator: str) -> int:
    cur = conn.execute(
        "INSERT INTO import_log (filename, kind, added, updated, skipped, conflicts, "
        "missing_required, missing_columns, operator, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (filename, kind, added, updated, skipped, 0, 0, "", operator, now_str()))
    conn.commit()
    return cur.lastrowid


def write_import_log(conn: sqlite3.Connection, filename: str, kind: str,
                     added: int, updated: int, skipped: int, conflicts: int,
                     missing_required: int, missing_columns: list[str],
                     operator: str) -> int:
    """导入留痕（`import_log`）。"""
    return _write_log(conn, filename, kind, added, updated, skipped, operator)


# ── 契约 v3 §3：解析产物 → 提案 → 入库 ────────────────────────────────
def _staging_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(staging)")}


def _safe_stem(source_file: str) -> str:
    """原文件名的「词干」：去扩展名、去开头的 `LIT-000001_`。

    例：`LIT-000201_原始PDF.pdf` → `原始PDF`
    → 产物名 `MinerU_markdown_LIT-000201_原始PDF.md`（沿用现有约定）。
    """
    import re as _re
    stem = Path(str(source_file or "")).stem.strip() or "原始PDF"
    stem = _re.sub(r"^LIT-\d+[_\-]?", "", stem)
    return stem or "原始PDF"


def create_parsed_proposal(conn: sqlite3.Connection, *, job_id: str, lit_id: str,
                           record: dict, images: list | None = None,
                           artifacts: list | None = None, source_file: str = "",
                           proposer: str = "local") -> dict:
    """把「解析产物 + 题录」合成**一条** `staging` 提案（kind='parsed'）。

    ★ **不写主库**（契约 v3 §3.1：批准前不动权威库）。
    ★ `record` 的键是**登记表列名**（与 `/api/registry/columns` 一致），
      这里按**列名**映射成库字段（不按列序）。
    """
    spec = kind("literatures")
    fields: dict = {}
    columns_map: dict = {}
    diff_fields: list = []
    for col, val in (record or {}).items():
        c = spec["by_name"].get(norm(col))
        if not c or not c.get("field"):
            continue
        f = c["field"]
        if f == "lit_id":
            continue
        fields[f] = val
        columns_map[norm_name(c["name"])] = val
        diff_fields.append({"col": norm_name(c["name"]), "old": "",
                            "new": _norm_val(val),
                            "required": bool(c.get("required"))})
    payload = {
        "record": fields,
        "columns": columns_map,
        "artifacts": artifacts or [],
        "images": images or [],
        "job_id": job_id or "",
        "source_file": source_file or "",
    }
    diff = {"op": "new", "fields": diff_fields}
    cols = ["kind", "target_id", "payload", "diff", "proposer", "status",
            "created_at", "batch", "filename"]
    vals = [KIND_PARSED, lit_id,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(diff, ensure_ascii=False),
            proposer or "local", "pending", now_str(),
            f"MINERU-{job_id or ''}", source_file or ""]
    if "job_id" in _staging_columns(conn):
        cols.append("job_id")
        vals.append(job_id or "")
    cur = conn.execute(
        f"INSERT INTO staging ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        vals)
    conn.commit()
    return {"staging_id": cur.lastrowid, "status": "pending",
            "kind": KIND_PARSED, "target_id": lit_id,
            "note": "已提交，等待管理员审核（批准前不写主库）"}


def _artifact_dests(cfg, lit_id: str, stem: str,
                    artifacts: list) -> dict[str, str]:
    """产物 → 源数据目录里的**目标文件名**（相对 `04_文献全文` 等子目录）。

    ★ 命名沿用现有约定：
      Markdown → `MinerU_markdown_<LIT-id>_<原文件名>.md`
      JSON     → `MinerU_json_<LIT-id>_<原文件名>.json`
      图片     → `IMG-<LIT-id>-<序号>.<扩展名>`
    """
    dests: dict[str, str] = {}
    md_taken = False
    json_taken = False
    img_seq = 0
    for a in artifacts:
        kind = a.get("kind")
        rel = a.get("rel") or ""
        base = a.get("name") or Path(rel).name
        if kind == "md":
            if not md_taken:
                dests[rel] = f"MinerU_markdown_{lit_id}_{stem}.md"
                md_taken = True
            else:
                dests[rel] = f"MinerU_markdown_{lit_id}_{stem}_{Path(base).stem}.md"
        elif kind == "json":
            if not json_taken:
                dests[rel] = f"MinerU_json_{lit_id}_{stem}.json"
                json_taken = True
            else:
                dests[rel] = f"MinerU_json_{lit_id}_{stem}_{Path(base).stem}.json"
        elif kind == "image":
            img_seq += 1
            ext = Path(base).suffix or ".png"
            dests[rel] = f"IMG-{lit_id}-{img_seq:03d}{ext}"
    return dests


def _approve_parsed(conn: sqlite3.Connection, row: dict, reviewer: str,
                    note: str) -> dict:
    """批准一条 `parsed` 提案：产物搬进源数据目录 + 文献行 upsert 进主库。

    ★ **幂等**：产物已搬过（目标文件已存在）就跳过；文献行是 upsert。
    ★ 源数据目录的写入**只发生在这里**（批准时），解析阶段一律只落暂存区。
    """
    from . import mineru_parse

    cfg = get_config()
    payload = json.loads(row.get("payload") or "{}")
    lit_id = row.get("target_id") or ""
    job_id = payload.get("job_id") or ""
    artifacts = payload.get("artifacts") or []
    source_file = payload.get("source_file") or ""
    stem = _safe_stem(source_file)

    md_dir = cfg.md_dir
    json_dir = cfg.raw_json_dir
    img_dir = cfg.image_dir
    for d in (md_dir, json_dir, img_dir):
        d.mkdir(parents=True, exist_ok=True)
    rel_base = cfg.source_root.name

    stage_dir = mineru_parse.staging_root() / job_id if job_id else None
    dests = _artifact_dests(cfg, lit_id, stem, artifacts)

    moved: list[dict] = []
    missing: list[str] = []
    md_name = json_name = ""
    for a in artifacts:
        rel = a.get("rel") or ""
        kind = a.get("kind")
        if kind not in ("md", "json", "image"):
            continue
        name = dests.get(rel)
        if not name:
            continue
        if kind == "md":
            dest = md_dir / name
            md_name = name
        elif kind == "json":
            dest = json_dir / name
            json_name = json_name or name
        else:
            dest = img_dir / name
        src = (stage_dir / rel) if stage_dir else None
        if src is None or not src.exists():
            if dest.exists():
                moved.append({"kind": kind, "file": name, "skipped": True,
                              "note": "产物已入源目录（暂存区已清空）"})
                continue
            missing.append(rel)
            continue
        if dest.exists():
            moved.append({"kind": kind, "file": name, "skipped": True,
                          "note": "目标文件已存在，未重复搬运"})
            continue
        shutil.move(str(src), str(dest))
        moved.append({"kind": kind, "file": name, "skipped": False})

    # ── 文献行 upsert ────────────────────────────────────────────
    fields = dict(payload.get("record") or {})
    fields["lit_id"] = lit_id
    if md_name:
        fields["md_path"] = f"{rel_base}/03_文献全文/{md_name}"
    if json_name:
        fields["pdf_path"] = fields.get("pdf_path") or ""
    if not fields.get("body_id"):
        fields["body_id"] = lit_id
    if not fields.get("parse_status"):
        fields["parse_status"] = "已解析"
    res = _upsert(conn, "literatures", lit_id, fields)

    # 派生列（登记表里没有这三列 → 单独写）
    chars = 0
    pages = 0
    n_img = 0
    # ★ 从**搬完之后的目标文件**读（暂存区里的原件已经被搬走了）
    if md_name:
        try:
            txt = (md_dir / md_name).read_text(encoding="utf-8", errors="replace")
            chars = len(txt)
        except Exception:
            chars = 0
    if json_name:
        try:
            data = json.loads((json_dir / json_name).read_text(
                encoding="utf-8", errors="replace"))
            pl = data.get("pages")
            pages = len(pl) if isinstance(pl, list) else 0
            if not pages:
                pages = int((((data.get("metadata") or {}).get("document") or {})
                             .get("page_count")) or 0)
        except Exception:
            pages = 0
    n_img = len([a for a in artifacts if a.get("kind") == "image"])
    conn.execute("UPDATE literatures SET md_chars=?, md_pages=?, md_img_refs=? WHERE lit_id=?",
                 (chars, pages, n_img, lit_id))

    # ── 图版行 upsert（submit 里的 images 元数据）─────────────────
    meta_by_name = {str(m.get("filename") or ""): m for m in (payload.get("images") or [])}
    n_img_rows = 0
    for a in artifacts:
        if a.get("kind") != "image":
            continue
        rel = a.get("rel") or ""
        name = dests.get(rel) or ""
        if not name:
            continue
        m = meta_by_name.get(a.get("name") or "") or {}
        img_id = Path(name).stem
        page = str(m.get("page") or "")
        row = {
            "img_id": img_id, "lit_id": lit_id, "filename": name,
            "path": f"{rel_base}/04_文献图像/{name}",
            "img_title": m.get("caption") or "", "obj_type": m.get("obj_type") or "",
            "fig_no": m.get("fig_no") or "", "caption": m.get("caption") or "",
            "pdf_page": page, "orig_page": page,
            "review_status": "REV-PENDING｜待人工复核",
        }
        if _upsert(conn, "images", img_id, row) != "skipped":
            n_img_rows += 1

    conn.commit()
    for t in KIND_TABLES:
        set_meta(conn, {f"{'lit' if t == 'literatures' else 'img'}_count": count(conn, t)})
    conn.commit()
    _write_log(conn, source_file or row.get("batch") or "", KIND_PARSED,
               1 if res == "inserted" else 0, 1 if res == "updated" else 0,
               len(missing), reviewer or "local")

    return {"id": row.get("id"), "status": "approved", "applied": True,
            "result": res, "lit_id": lit_id,
            "moved": moved, "missing": missing,
            "image_rows": n_img_rows,
            "chars": chars, "pages": pages,
            "note": ("产物已从暂存区搬进源数据目录，文献行已 upsert 进主库"
                     + ("；有产物缺失，见 missing" if missing else ""))}


# ── 提案与审核 ──────────────────────────────────────────────────────────
def list_staging(conn: sqlite3.Connection, status: str = "", kind_: str = "",
                 page: int = 1, size: int = 50) -> dict:
    where, params = [], []
    if status:
        where.append("status = ?"); params.append(status)
    if kind_:
        where.append("kind = ?"); params.append(kind_)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) c FROM staging{w}", params).fetchone()["c"]
    page = max(1, page)
    size = max(1, min(200, size))
    rows = conn.execute(
        f"SELECT * FROM staging{w} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["diff"] = json.loads(d.get("diff") or "{}")
        except Exception:
            d["diff"] = {}
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except Exception:
            d["payload"] = {}
        # ★ 契约 v3 §3.5：每条提案要能看出「这是哪来的」
        if d.get("kind") == KIND_PARSED:
            arts = d["payload"].get("artifacts") or []
            d["artifacts"] = arts
            d["job_id"] = d["payload"].get("job_id") or d.get("job_id") or ""
            d["source_file"] = d["payload"].get("source_file") or d.get("filename") or ""
            d["artifact_summary"] = {
                "md": len([a for a in arts if a.get("kind") == "md"]),
                "json": len([a for a in arts if a.get("kind") == "json"]),
                "images": len([a for a in arts if a.get("kind") == "image"]),
                "total": len(arts),
            }
        items.append(d)
    return {"items": items, "total": total, "page": page, "size": size}


def review_one(conn: sqlite3.Connection, staging_id: int, approve: bool,
               reviewer: str = "", note: str = "") -> dict:
    """批准（写主库）或驳回（只标状态）。**已完成的不删除，只改 status。**"""
    row = conn.execute("SELECT * FROM staging WHERE id = ?", (staging_id,)).fetchone()
    if row is None:
        raise KeyError(f"提案 {staging_id} 不存在")
    row = dict(row)
    if row["status"] != "pending":
        return {"id": staging_id, "status": row["status"], "applied": False,
                "note": "该提案已处理过，只改状态不重复写入。"}
    if approve:
        # ★ 契约 v3 §3.4：`parsed` 类提案走**另一条路**（搬产物 + upsert 文献行），
        #   不能塞进 `_upsert`（它只认登记表那两张表）。
        if row["kind"] == KIND_PARSED:
            rep = _approve_parsed(conn, row, reviewer, note)
            conn.execute(
                "UPDATE staging SET status='approved', reviewer=?, review_note=?, reviewed_at=? "
                "WHERE id=?", (reviewer or "local", note, now_str(), staging_id))
            conn.commit()
            return rep
        payload = json.loads(row.get("payload") or "{}")
        res = _upsert(conn, row["kind"], row["target_id"] or "", payload)
        conn.execute(
            "UPDATE staging SET status='approved', reviewer=?, review_note=?, reviewed_at=? "
            "WHERE id=?", (reviewer or "local", note, now_str(), staging_id))
        conn.commit()
        for t in KIND_TABLES:
            set_meta(conn, {f"{'lit' if t == 'literatures' else 'img'}_count": count(conn, t)})
        conn.commit()
        # 留痕：谁在什么时候把哪条提案写进了主库
        _write_log(conn, row.get("filename") or row.get("batch") or "",
                   row["kind"], 1 if res == "inserted" else 0,
                   1 if res == "updated" else 0, 0, reviewer or "local")
        return {"id": staging_id, "status": "approved", "applied": True,
                "result": res, "note": "已写主库"}
    conn.execute(
        "UPDATE staging SET status='rejected', reviewer=?, review_note=?, reviewed_at=? "
        "WHERE id=?", (reviewer or "local", note, now_str(), staging_id))
    conn.commit()
    return {"id": staging_id, "status": "rejected", "applied": False,
            "note": "已驳回，未写主库"}


def import_log_page(conn: sqlite3.Connection, page: int = 1, size: int = 50) -> dict:
    total = conn.execute("SELECT COUNT(*) c FROM import_log").fetchone()["c"]
    page = max(1, page)
    size = max(1, min(200, size))
    rows = conn.execute(
        "SELECT * FROM import_log ORDER BY id DESC LIMIT ? OFFSET ?",
        [size, (page - 1) * size]).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "size": size}


# ── 在线录入 ────────────────────────────────────────────────────────────
def create_entry(conn: sqlite3.Connection, table: str, values: dict,
                 proposer: str = "local") -> dict:
    """在线录入 → 写 `staging`（不写主库）。

    `values` 的 key 是**登记表列名**（按列名给，不按列序）。
    """
    spec = kind(table)
    payload = {}
    for col, val in values.items():
        c = spec["by_name"].get(norm(col))
        if c and c.get("field"):
            payload[c["field"]] = val
    target_id = payload.get(pk_field(table)) or ""
    cur = conn.execute(
        "INSERT INTO staging (kind, target_id, payload, diff, proposer, status, "
        "created_at, batch, filename) VALUES (?,?,?,?,?,?,?,?,?)",
        (table, target_id,
         json.dumps(payload, ensure_ascii=False),
         json.dumps({"op": "new",
                     "fields": [{"col": c["name"], "old": "", "new": _norm_val(payload.get(c["field"])),
                                 "required": bool(c.get("required"))}
                                for c in spec["columns"]
                                if c.get("field") and payload.get(c["field"]) not in (None, "")]},
                    ensure_ascii=False),
         proposer or "local", "pending", now_str(), "", ""))
    conn.commit()
    return {"staging_id": cur.lastrowid, "status": "pending"}


# ── 导出 ────────────────────────────────────────────────────────────────
def export_literatures_xlsx(conn: sqlite3.Connection) -> bytes:
    from .templates import build_export_bytes
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM literatures ORDER BY lit_id")]
    meta = get_meta(conn)
    note = (f"导出于 {now_str()}；共 {len(rows)} 篇（库内计数 {meta.get('lit_count', '?')}）；"
            f"数据迁移 {meta.get('migrated_at', '?')}；使用条件以「开放类型/使用条件」列为准。")
    return build_export_bytes("literatures", rows, VARIANT_FULL, note)


def export_images_xlsx(conn: sqlite3.Connection) -> bytes:
    from .templates import build_export_bytes
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM images ORDER BY lit_id, filename")]
    meta = get_meta(conn)
    note = (f"导出于 {now_str()}；共 {len(rows)} 张（库内计数 {meta.get('img_count', '?')}）；"
            f"复核状态按「人工复核状态」列，复核日期非空不代表已复核。")
    return build_export_bytes("images", rows, VARIANT_FULL, note)
