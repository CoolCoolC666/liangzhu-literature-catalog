# -*- coding: utf-8 -*-
"""SQLite 数据层：schema、连接、检索辅助。

设计要点
--------
1. **FTS5 trigram**：中文子串检索用 trigram 分词器（SQLite ≥ 3.34）。
   但 trigram 要求查询词 ≥ 3 字符 —— 2 字词（如「良渚」在某些语境下仍能命中，
   但「清朗」这类 2 字词会命中 0）。因此 `search_keyword()` 做了**两级降级**：
   ≥3 字符走 FTS5，<3 字符退回 LIKE 扫描。**混合查询时按最长的短板决定**：
   只要有一个词短于阈值，整条查询退回 LIKE，避免半边走索引半边扫描。
2. **连接**：每线程一个连接（sqlite3 连接不能跨线程共享）。
3. **并发读**：开 WAL，读写不互斥。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import get_config

# ── schema ──────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 文献（登记表 34 列全量 + 3 个派生统计列）
CREATE TABLE IF NOT EXISTS literatures (
    lit_id          TEXT PRIMARY KEY,
    seq             INTEGER,
    theme           TEXT,
    doc_type        TEXT,
    title           TEXT,
    authors         TEXT,
    lang            TEXT,
    year            TEXT,
    venue           TEXT,
    publisher       TEXT,
    pages_range     TEXT,
    abstract        TEXT,
    keywords        TEXT,
    source_url      TEXT,
    pdf_name        TEXT,
    pdf_pages       INTEGER,
    pdf_path        TEXT,
    body_id         TEXT,
    md_path         TEXT,
    parse_status    TEXT,
    parse_note      TEXT,
    open_type       TEXT,
    period_tags     TEXT,
    region_tags     TEXT,
    topic_tags      TEXT,
    lineage_tags    TEXT,
    clc             TEXT,
    subject_terms   TEXT,
    recorder        TEXT,
    record_date     TEXT,
    review_status   TEXT,
    remark          TEXT,
    source_type     TEXT,
    source_evidence TEXT,
    md_chars        INTEGER DEFAULT 0,
    md_pages        INTEGER DEFAULT 0,
    md_img_refs     INTEGER DEFAULT 0
);

-- 图像（登记表 26 列全量）
CREATE TABLE IF NOT EXISTS images (
    img_id        TEXT PRIMARY KEY,
    lit_id        TEXT,
    theme         TEXT,
    filename      TEXT,
    path          TEXT,
    img_title     TEXT,
    img_type      TEXT,
    obj_type      TEXT,
    orig_page     TEXT,
    pdf_page      TEXT,
    fig_no        TEXT,
    caption       TEXT,
    ai_desc       TEXT,
    ai_objects    TEXT,
    ai_tool       TEXT,
    ai_prompt     TEXT,
    review_status TEXT,
    reviewer      TEXT,
    review_date   TEXT,
    period_tags   TEXT,
    region_tags   TEXT,
    topic_tags    TEXT,
    lineage_tags  TEXT,
    storage_id    TEXT,
    remark        TEXT
);
CREATE INDEX IF NOT EXISTS idx_img_lit ON images(lit_id);
CREATE INDEX IF NOT EXISTS idx_img_obj ON images(obj_type);
CREATE INDEX IF NOT EXISTS idx_img_type ON images(img_type);

-- 标签（规范化后的节点）
CREATE TABLE IF NOT EXISTS tags (
    tag_id    TEXT PRIMARY KEY,
    dim       TEXT NOT NULL,
    code      TEXT,
    name      TEXT,
    lit_count INTEGER DEFAULT 0,
    img_count INTEGER DEFAULT 0,
    raw_forms TEXT,
    code_reliable INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_tag_dim ON tags(dim);

CREATE TABLE IF NOT EXISTS lit_tags (
    lit_id TEXT, tag_id TEXT, dim TEXT,
    PRIMARY KEY (lit_id, tag_id)
);
CREATE TABLE IF NOT EXISTS img_tags (
    img_id TEXT, tag_id TEXT, dim TEXT,
    PRIMARY KEY (img_id, tag_id)
);

-- 4.0.12 原始 JSON 全文（可检索）
-- ★ 用外部内容 FTS：索引只存倒排，不复制正文（113MB 全文不翻倍）
CREATE TABLE IF NOT EXISTS raw_json (
    rowid      INTEGER PRIMARY KEY,
    lit_id     TEXT,
    filename   TEXT,
    size_bytes INTEGER,
    text       TEXT,
    UNIQUE (lit_id, filename)
);

-- 图谱（L1 表派生，零抽取）
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id TEXT PRIMARY KEY,
    kind    TEXT,
    label   TEXT,
    lit_id  TEXT,
    weight  INTEGER DEFAULT 1,
    x       REAL,          -- 确定性布局坐标（后端算好存这里）
    y       REAL
);
CREATE TABLE IF NOT EXISTS graph_edges (
    src    TEXT,
    dst    TEXT,
    rel    TEXT,
    lit_id TEXT,
    weight INTEGER DEFAULT 1,
    PRIMARY KEY (src, dst, rel, lit_id)
);
CREATE INDEX IF NOT EXISTS idx_edge_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON graph_edges(dst);
CREATE INDEX IF NOT EXISTS idx_edge_rel ON graph_edges(rel);

-- 布局版本：同一批数据的布局算法与参数要可复现
CREATE TABLE IF NOT EXISTS graph_layout (
    version    TEXT PRIMARY KEY,
    algo       TEXT,
    params     TEXT,
    node_count INTEGER,
    edge_count INTEGER,
    created_at TEXT
);

-- ── 账号与会话（P2 草稿1：本地多用户）────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT UNIQUE NOT NULL,
    pass_hash  TEXT NOT NULL,          -- scrypt$n$r$p$salt$dk（与 msyt/auth.py 同一套）
    role       TEXT NOT NULL DEFAULT 'member',   -- admin | member
    status     TEXT NOT NULL DEFAULT 'active',   -- active | pending | disabled
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sess_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS favorites (
    user_id    INTEGER NOT NULL,
    lit_id     TEXT NOT NULL,
    created_at TEXT,
    note       TEXT,
    PRIMARY KEY (user_id, lit_id)
);

-- ── 提案与审核（P2 草稿2：提案—审核制）──────────────────────────────
CREATE TABLE IF NOT EXISTS staging (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- literatures | images
    target_id   TEXT,                   -- 被提议修改的 LIT/IMG 标识符（新增时为空）
    payload     TEXT,                   -- 提议的整行（JSON）
    diff        TEXT,                   -- 字段级差异 JSON：[{"col","old","new"}]
    proposer    TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at  TEXT,
    reviewer    TEXT,
    review_note TEXT,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_staging_status ON staging(status);

-- ── 导入留痕（F3）───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS import_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    filename         TEXT,
    kind             TEXT,
    added            INTEGER DEFAULT 0,
    updated          INTEGER DEFAULT 0,
    skipped          INTEGER DEFAULT 0,
    conflicts        INTEGER DEFAULT 0,
    missing_required INTEGER DEFAULT 0,
    missing_columns  TEXT,
    operator         TEXT,
    created_at       TEXT
);

-- ── 访问留痕（公网成员内网：谁、何时、导了什么）──────────────────
CREATE TABLE IF NOT EXISTS access_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT,                    -- login | export | download | query | ai_ask | edit
    target     TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_acc_time ON access_log(created_at);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, content, lit_id UNINDEXED, tokenize='trigram'
);
-- ★ raw_json_fts 用**外部内容**模式：只存倒排索引，正文仍在 raw_json 表里。
--   113MB 的 JSON 全文因此不会被复制一份，库体积省一半。
CREATE VIRTUAL TABLE IF NOT EXISTS raw_json_fts USING fts5(
    text, content='raw_json', content_rowid='rowid', tokenize='trigram'
);
"""

# ── 词级索引（检索增强 E1）──────────────────────────────────────────────
# ★ 为什么**另建一张表**而不是改 docs_fts：
#   docs_fts 是 trigram（**子串**匹配），是可复现历史检索结果的基线；
#   docs_seg 是 unicode61（**词**匹配），语义不同、两者并存互补。
#   实测：词级相对 trigram **多召回 0 篇**（是子集），所以绝不能替换，只能合并。
#   → 出问题可以一键关掉新层、退回现状。
SEG_SCHEMA = """
-- 词级索引：body 是「分词后用空格连接」的正文。
--   unicode61 按分隔符切 token，所以必须有空格，词才切得开。
CREATE VIRTUAL TABLE IF NOT EXISTS docs_seg USING fts5(
    title, body, lit_id UNINDEXED, tokenize='unicode61'
);
-- 索引是**派生物**，词典是**源数据**。
--   这里记「这个索引是用哪版词典建的」，改词典没重建索引时界面必须提示。
CREATE TABLE IF NOT EXISTS segment_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_version INTEGER,
    dict_hash TEXT,
    built_at TEXT,
    doc_count INTEGER,
    token_count INTEGER,
    note TEXT
);
"""

# 全文检索的最小长度：trigram 至少要 3 个字符，短于此走 LIKE 兜底
MIN_FTS_LEN = 3


# ── 连接 ────────────────────────────────────────────────────────────────
def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else get_config().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> dict:
    """建表 + 建 FTS 虚拟表，并对**旧库**做增量列修补。

    ★ 为什么要有这一步：`CREATE TABLE IF NOT EXISTS` 对**已存在的表**不会加新列。
    所以「给 graph_nodes 加 x/y」这种改动，对老库是不生效的 → 必须显式 ALTER。
    返回能力探测结果。
    """
    conn.executescript(SCHEMA)
    caps = {"fts_trigram": False, "sqlite_version": sqlite3.sqlite_version,
            "added_columns": []}
    # —— 旧库增量修补 ——
    for table, col, decl in (("graph_nodes", "x", "REAL"),
                             ("graph_nodes", "y", "REAL"),
                             # F3 导入：批次号与来源文件名（提案按批次审核）
                             ("staging", "batch", "TEXT"),
                             ("staging", "filename", "TEXT"),
                             # 契约 v3 §3：解析产物提案要能追到解析任务号
                             ("staging", "job_id", "TEXT")):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            caps["added_columns"].append(f"{table}.{col}")
    if caps["added_columns"]:
        conn.commit()
    try:
        conn.executescript(FTS_SCHEMA)
        # 探针：trigram 真的可用吗
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        caps["fts_trigram"] = True
    except sqlite3.OperationalError as e:
        caps["fts_error"] = str(e)
        # 退化：unicode61 + 空格分词（中文效果差，但不至于起不来）
        conn.executescript(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(title, content, lit_id UNINDEXED);"
            "CREATE VIRTUAL TABLE IF NOT EXISTS raw_json_fts USING fts5(text, lit_id UNINDEXED, filename UNINDEXED);")
        caps["fts_trigram"] = False
    # 词级索引（E1）：**建表不等于建索引** —— 内容由 `python -m lzkg reindex` 灌。
    #   这里只保证表在、schema 对，jieba 缺失也不影响启动。
    try:
        conn.executescript(SEG_SCHEMA)
        caps["seg_index"] = True
    except sqlite3.OperationalError as e:
        caps["seg_index"] = False
        caps["seg_error"] = str(e)
    conn.commit()
    return caps


def get_meta(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.Error:
        return {}
    return {r["key"]: r["value"] for r in rows}


def ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """给**已存在的旧库**补上后来新增的列，返回补了哪些。

    ★ 与 `init_db` 里的修补逻辑同源，但可以**在运行时单独调用**：
      `init_db` 只在 migrate / doctor 里跑，而服务启动时不会跑它 ——
      新增的列若不在启动路径上补，导入功能就会报「no such column」。
    """
    added = []
    for table, col, decl in (("graph_nodes", "x", "REAL"),
                             ("graph_nodes", "y", "REAL"),
                             ("staging", "batch", "TEXT"),
                             ("staging", "filename", "TEXT"),
                             ("staging", "job_id", "TEXT")):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            added.append(f"{table}.{col}")
    if added:
        conn.commit()
    return added


def set_meta(conn: sqlite3.Connection, kv: dict) -> None:
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(k, str(v)) for k, v in kv.items()])


def count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    except sqlite3.Error:
        return 0


# ── 检索辅助 ────────────────────────────────────────────────────────────
def _norm_tokens(q: str) -> list[str]:
    """把查询串拆成词：中文按 2-gram 之外不做切分，英文/数字按空白切。"""
    q = (q or "").strip()
    if not q:
        return []
    # 保留中文串整体，同时给英文数字按空白拆
    parts = [p for p in re.split(r"[\s,，;；、]+", q) if p]
    return parts


def needs_like(tokens: Iterable[str]) -> bool:
    """只要有一个词短于 trigram 阈值，整条查询退回 LIKE。"""
    for t in tokens:
        if len(t) < MIN_FTS_LEN:
            return True
    return False


def like_clause(tokens: list[str], cols: list[str]) -> tuple[str, list]:
    """构造 (AND 连接的 LIKE 条件, 参数)。每个词都要在**任一**列命中。"""
    where = []
    params: list = []
    for t in tokens:
        ors, ps = [], []
        for c in cols:
            ors.append(f"{c} LIKE ?")
            ps.append(f"%{t}%")
        where.append("(" + " OR ".join(ors) + ")")
        params += ps
    return " AND ".join(where), params


def fts_query(tokens: list[str]) -> str:
    """把词串拼成 FTS5 MATCH 表达式：每个词加引号（防语法注入），词间 AND。"""
    parts = []
    for t in tokens:
        safe = t.replace('"', '""')
        parts.append(f'"{safe}"')
    return " AND ".join(parts)


def row_to_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}
