# -*- coding: utf-8 -*-
"""检索增强：应用层分词（jieba + 领域词典）+ 词级索引 + 查询扩展。

设计依据（全部是 2026-10-31 本机实测，**不是推演**）
--------------------------------------------------------
方案全文见 `02-报告/良渚文献目录站_检索增强方案_20261031.md`。

① **jieba 是可选依赖**。没装 jieba 时程序照常启动、全文检索退回 trigram 模式，
   词级模式与实验室入口置灰并写明原因。
   → **不允许因为一个可选的分词器让整个站起不来。**

② 词级索引用 FTS5 **`unicode61`**（不是 trigram）。
   `unicode61` 按分隔符切 token，所以正文必须**先分词、再用空格连接**，
   它才会把每个词当独立 token。
   ★ 语义 = **词匹配**；trigram 语义 = **子串匹配**。
     实测 16 个查询词，词级相对 trigram **多召回 0 篇**（是子集，不是超集）。
     → 两种模式**并存 + 合并**，绝不能互相替代。

③ 扩展检索**必须过滤单字 token**。
   实测「遗址群」被切成 `遗址/群`，OR 组合多捞出 **74 篇全是假阳性**
   （含原词的 0 篇）；单字「群」单独命中 56 篇、「山」70 篇、「玉」63 篇。

④ 领域词典是**数据文件**（`lzkg/data/dict/lzkg_domain.json`），不是硬编码。
   管理员可在界面里改（见 `api_segment.py`），改动后重建索引即生效
   （实测 201 篇重建 8.0 秒）。

⑤ 索引是**派生物**，词典是**源数据**。`segment_meta` 记 `segment_version` +
   `dict_hash`，任何时候都能回答"这个索引是用哪版词典建的"。

★ 不引入第二套检索引擎（Whoosh / tantivy / Elasticsearch）：
  两套索引 = 两套真值，索引会与库里的正文漂移，检索结果无法复现。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

from .common import now_str
from .config import get_config

# ── 词典文件位置（包内，随发布分发）─────────────────────────────────────
PACKAGE_DIR = Path(__file__).resolve().parent
DICT_REL = Path("data") / "dict" / "lzkg_domain.json"

#: 同义组展开时，单个同义组最多带几个词（防止一个组把召回面炸开）
MAX_SYNONYM_MEMBERS = 12

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+")

_jieba = None
_jieba_failed = False
_applied_hash = ""
_applied_count = 0
_dict_cache: dict | None = None
_dict_cache_stamp: tuple = ()
_dict_cache_hash = ""


# ── 词典读写 ────────────────────────────────────────────────────────────
def dict_path() -> Path:
    """词典文件路径。"""
    return PACKAGE_DIR / DICT_REL


def dict_hash(data: dict | None = None) -> str:
    """词典内容的 sha256（前 16 位够用了，只做版本标记）。"""
    if data is None:
        data = load_dict()
    if data is _dict_cache and _dict_cache_hash:
        return _dict_cache_hash
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def load_dict(path: Path | None = None) -> dict:
    """读领域词典。**读不到就用内置的初始词典**，不让程序起不来。

    ★ **带缓存**（按 mtime 判定新鲜度）：
      `cut()` 每次分词前都要确认一次「词典有没有变」，
      不缓存的话 = 每次切词都读一遍 JSON 文件 → 实测重建索引从 10 秒拖到 **75 秒**。
    """
    p = Path(path) if path else dict_path()
    key = str(p)
    global _dict_cache, _dict_cache_stamp, _dict_cache_hash
    try:
        st = p.stat()
        stamp = (key, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (key, 0, 0)
    if _dict_cache is not None and _dict_cache_stamp == stamp:
        return _dict_cache
    data = None
    try:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = _normalize_dict(raw)
    except Exception:
        data = None
    if data is None:
        data = _normalize_dict(_builtin_dict())
    _dict_cache = data
    _dict_cache_stamp = stamp
    _dict_cache_hash = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return data


def save_dict(data: dict, path: Path | None = None) -> dict:
    """写领域词典。**先备份再写**，写坏了自己能回退。"""
    p = Path(path) if path else dict_path()
    data = _normalize_dict(data)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        try:
            shutil.copy2(p, bak)
        except Exception:
            pass
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return data


def _normalize_dict(data: dict) -> dict:
    """把词典补齐成完整结构（缺项用默认值，不丢用户写的内容）。"""
    out = {
        "version": str(data.get("version") or "1.0"),
        "updated_at": str(data.get("updated_at") or ""),
        "segment_version": int(data.get("segment_version") or 1),
        "words": [],
        "synonyms": [],
        "domain_tails": [],
        "domain_heads": [],
    }
    for w in data.get("words") or []:
        if isinstance(w, str):
            w = {"word": w}
        if not isinstance(w, dict):
            continue
        word = str(w.get("word") or "").strip()
        if not word:
            continue
        out["words"].append({
            "word": word,
            "freq": int(w.get("freq") or 100000),
            "pos": str(w.get("pos") or "n"),
            "group": str(w.get("group") or ""),
            "note": str(w.get("note") or ""),
        })
    for s in data.get("synonyms") or []:
        if isinstance(s, str):
            s = {"group": s, "members": [x.strip() for x in s.split(",") if x.strip()]}
        if not isinstance(s, dict):
            continue
        members = [str(x).strip() for x in (s.get("members") or []) if str(x).strip()]
        if len(members) < 2:
            continue
        out["synonyms"].append({
            "group": str(s.get("group") or members[0]),
            "members": members[:MAX_SYNONYM_MEMBERS],
            "note": str(s.get("note") or ""),
        })
    out["domain_tails"] = [str(x).strip() for x in (data.get("domain_tails") or [])
                           if str(x).strip()]
    out["domain_heads"] = [str(x).strip() for x in (data.get("domain_heads") or [])
                           if str(x).strip()]
    return out


# ── jieba 封装（**可选依赖**）────────────────────────────────────────────
def available() -> bool:
    """jieba 装了没。**没装不是致命错误** —— 检索会退回 trigram 模式。"""
    return _get_jieba() is not None


def jieba_error() -> str:
    """装不上时的原因（给界面显示，别只说「不可用」）。"""
    if _jieba is not None:
        return ""
    try:
        import jieba  # noqa: F401
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return "jieba 初始化失败"


def _get_jieba():
    """惰性加载 jieba + 领域词典。

    ★ 惰性是必须的：jieba 首次 initialize() 要 0.5–3 秒，
      不能让服务启动就等它。
    ★ 加载失败**只记录不抛** —— 一个可选分词器不该让整个站起不来。
    """
    global _jieba, _jieba_failed
    if _jieba is not None or _jieba_failed:
        return _jieba
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import jieba
        jieba.setLogLevel(60)
        jieba.initialize()
        _jieba = jieba
        _jieba_failed = False
    except Exception:
        _jieba_failed = True
        _jieba = None
    return _jieba


def apply_dict_words(data: dict | None = None) -> int:
    """把领域词典喂给 jieba。→ 生效词条数。

    ★ 词频统一给大（100000）—— **词频给大才能压过默认词典的切分倾向**。
      否则「兽面纹」会被切成「兽面/纹」，整个领域词就散了。

    ★ **只在词典变化时重灌**（用 dict_hash 做闸门）。
      原来每次分词都重灌一遍 → jieba 的 add_word 会重建 DAG 缓存，
      201 篇 = 13k 次 add_word → 实测重建索引从 10 秒拖到 **69 秒**。
    """
    j = _get_jieba()
    if j is None:
        return 0
    data = data if data is not None else load_dict()
    global _applied_hash, _applied_count
    h = dict_hash(data)
    if _applied_hash == h:
        return _applied_count
    n = 0
    for w in data.get("words") or []:
        word = (w.get("word") or "").strip()
        if not word:
            continue
        j.add_word(word, freq=int(w.get("freq") or 100000))
        n += 1
    _applied_hash = h
    _applied_count = n
    return n


def reset_dict_cache() -> None:
    """词典改了之后调用 → 下次分词会重新灌词、重新读文件。"""
    global _applied_hash, _applied_count, _dict_cache, _dict_cache_stamp, _dict_cache_hash
    _applied_hash = ""
    _applied_count = 0
    _dict_cache = None
    _dict_cache_stamp = ()
    _dict_cache_hash = ""


def cut(text: str) -> list[str]:
    """分词。没装 jieba 时按标点切（仍可用于退化检索）。"""
    if not text:
        return []
    j = _get_jieba()
    if j is None:
        return [w for w in re.split(r"[\s，。；、！？,.;!?（）()《》“”\"']+", text) if w]
    apply_dict_words()
    return [w.strip() for w in j.cut(text) if w.strip()]


def segment_text(text: str) -> str:
    """分词后用**空格连接**。

    ★ 这是让 FTS5 `unicode61` 当词级索引用 的关键：
      unicode61 按分隔符切 token，正文里有空格，它才把每个词当独立 token。
      不这么做，unicode61 会把一整段中文当成**一个** token（等于没分词）。
    """
    out: list[str] = []
    for seg in _CJK_RUN.findall(text or ""):
        out.append(" ".join(cut(seg)))
    for seg in _ASCII_RUN.findall(text or ""):
        out.append(seg.lower())
    return " ".join(x for x in out if x)


# ── 查询侧 ──────────────────────────────────────────────────────────────
def query_terms(q: str, data: dict | None = None,
                with_synonym: bool = True) -> tuple[list[str], list[str]]:
    """查询串 → (参与匹配的 token, 同义展开出来的 token)。

    ★ 规则（实测得来的）：
      1. 用**同一份领域词典**切词（查询侧与索引侧必须一致）。
      2. **长度 < 2 的 token 一律丢弃**（单字是假阳性的主要来源）。
      3. 过滤后为空 → 退回把原查询串整体当一个词（精确短语，不退回单字）。
      4. `with_synonym=True` 时，命中某个同义组 → 把整组加进来（同义命中）。
      ★ 「纯词级」（不含同义）与「词级+同义」**必须分开**：
        不含同义时词级命中集是精确层的**子集**；加了同义就不再是
        （「良渚文化」经同义组召回 128 篇 > 精确层 120 篇）—— 这正是扩展的价值。
    """
    data = data if data is not None else load_dict()
    raw = (q or "").strip()
    if not raw:
        return [], []
    toks = [t for t in cut(raw) if len(t) >= 2]
    if not toks:
        # 一个可用 token 都没有 → 整体当短语（比退回单字安全得多）
        toks = [raw] if len(raw) >= 1 else []
    if not with_synonym:
        return toks, []
    # 同义扩展
    extra: list[str] = []
    groups = data.get("synonyms") or []
    for g in groups:
        members = [m for m in (g.get("members") or []) if m]
        if not members:
            continue
        if any(any(m in t or t in m for t in toks) for m in members):
            for m in members:
                if m not in toks and m not in extra:
                    extra.append(m)
    return toks, extra


def _fts_phrase(tok: str) -> str:
    """FTS5 短语字面量（内部双引号要转义）。"""
    return '"' + tok.replace('"', '""') + '"'


def word_level_ids(conn: sqlite3.Connection, q: str,
                   data: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """词级检索。→ (命中的 lit_id 列表, 参与匹配的 token, 同义 token)。

    ★ 索引不存在 / 没装 jieba → 返回空，调用方自行降级。
    """
    if not _has_seg_index(conn):
        return [], [], []
    terms, extra = query_terms(q, data)
    if not terms:
        return [], [], []
    all_terms = list(terms) + list(extra)
    expr = " OR ".join(_fts_phrase(t) for t in all_terms)
    try:
        rows = conn.execute(
            "SELECT lit_id FROM docs_seg WHERE docs_seg MATCH ? LIMIT 2000",
            (expr,)).fetchall()
    except sqlite3.OperationalError:
        return [], [], []
    ids = [r["lit_id"] for r in rows if r["lit_id"]]
    return ids, terms, extra


# ── 索引 ────────────────────────────────────────────────────────────────
def _has_seg_index(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='docs_seg'"
    ).fetchone()
    return row is not None


def index_status(conn: sqlite3.Connection) -> dict:
    """索引状态：版本、词典指纹、行数。"""
    data = load_dict()
    st = {"segment_version": data.get("segment_version", 1),
          "dict_hash": dict_hash(data),
          "dict_updated_at": data.get("updated_at", ""),
          "dict_words": len(data.get("words") or []),
          "dict_synonyms": len(data.get("synonyms") or []),
          "jieba": available(),
          "jieba_error": jieba_error(),
          "built": False, "doc_count": 0, "built_at": "", "stale": True}
    if not _has_seg_index(conn):
        st["jieba_error"] = st["jieba_error"] or "词级索引尚未建立"
        return st
    row = conn.execute("SELECT COUNT(*) c FROM docs_seg").fetchone()
    st["doc_count"] = row["c"]
    try:
        m = conn.execute(
            "SELECT segment_version, dict_hash, built_at FROM segment_meta "
            "ORDER BY segment_version DESC, id DESC LIMIT 1").fetchone()
        if m:
            st["built"] = True
            st["segment_version"] = m["segment_version"]
            st["built_dict_hash"] = m["dict_hash"]
            st["built_at"] = m["built_at"] or ""
            # ★ 词典改过但索引没重建 → stale，界面必须提示，不能装作已生效
            st["stale"] = (m["dict_hash"] != st["dict_hash"])
    except sqlite3.OperationalError:
        pass
    return st


def rebuild_index(conn: sqlite3.Connection,
                  progress: Callable[[str], None] | None = None) -> dict:
    """重建词级索引。

    ★ 词典是源数据、索引是派生物 —— 每次重建都记一条 `segment_meta`，
      带 `segment_version` 与 `dict_hash`，这样"索引是用哪版词典建的"永远查得到。
    """
    say = progress or (lambda _s: None)
    if not available():
        return {"ok": False, "error": "本机没装 jieba，无法建立词级索引；"
                                      "检索已退回整句精确模式",
                "jieba_error": jieba_error()}
    data = load_dict()
    say("读取领域词典…")
    apply_dict_words(data)
    say("读取原文…")
    rows = conn.execute("SELECT lit_id, title, content FROM docs_fts").fetchall()
    t0 = time.perf_counter()
    say(f"分词中（{len(rows)} 篇）…")
    ins = []
    token_count = 0
    for i, r in enumerate(rows, 1):
        body = segment_text(r["content"] or "")
        # ★ **title 也要分词** —— 不分词的话，unicode61 会把整段中文标题
        #   当成**一个** token，搜标题里的词（"良渚文化"）就匹配不上。
        #   实测：LIT-000091 的标题含「良渚文化」，精确层命中、词级层不命中，
        #   就是因为标题没分词。
        title = segment_text(r["title"] or "")
        token_count += body.count(" ") + 1
        ins.append((title, body, r["lit_id"]))
        if i % 20 == 0:
            say(f"分词中… {i}/{len(rows)}")
    say("写入词级索引…")
    conn.execute("DROP TABLE IF EXISTS docs_seg")
    conn.execute(
        "CREATE VIRTUAL TABLE docs_seg USING fts5("
        "title, body, lit_id UNINDEXED, tokenize='unicode61')")
    conn.executemany("INSERT INTO docs_seg (title, body, lit_id) VALUES (?,?,?)", ins)
    conn.commit()
    elapsed = time.perf_counter() - t0
    version = int(data.get("segment_version") or 1) + 1
    data["segment_version"] = version
    data["updated_at"] = now_str()
    try:
        save_dict(data)
    except Exception:
        pass
    # ★ **写完盘必须把缓存扔掉重读** —— `data` 是 `_dict_cache` 里的**同一个对象**，
    #   刚才原地改了 segment_version / updated_at，而缓存里的旧哈希还在。
    #   不重读的话：segment_meta 登记的是旧哈希、status 读的是新哈希 → 永远 stale=true，
    #   界面会一直谎报「词典已改但索引还没重建」。
    reset_dict_cache()
    data = load_dict()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS segment_meta ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "segment_version INTEGER, dict_hash TEXT, built_at TEXT,"
            "doc_count INTEGER, token_count INTEGER, note TEXT)")
        conn.execute(
            "INSERT INTO segment_meta (segment_version, dict_hash, built_at,"
            "doc_count, token_count, note) VALUES (?,?,?,?,?,?)",
            (version, dict_hash(data), data["updated_at"], len(ins), token_count,
             "词级索引重建"))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    say(f"完成：{len(ins)} 篇 / {token_count} token / {elapsed:.1f} 秒")
    return {"ok": True, "doc_count": len(ins), "token_count": token_count,
            "segment_version": version, "elapsed_sec": round(elapsed, 2)}


# ── 新词发现 ────────────────────────────────────────────────────────────
#: 虚字：出现在候选首尾 → 多半是被长词夹出来的碎片，不是词
_DROP_CHARS = set("的了和与及或在为是对把被就都也很更最又而且但是所以因为这那之其以于从对为着过又且则乃")

#: 凝固度阈值 / 邻字熵阈值 / 最小频次（实测调出来的，别沿用别的项目的值）
MIN_COHESION = 4.0
MIN_SIDE_ENTROPY = 0.6
MIN_COUNT = 20
TOPN = 300
DOMAIN_BOOST = 3.0


def _sentences(texts: Iterable[str]) -> Iterable[str]:
    for t in texts:
        for s in re.split(r"[。！？；\n\r]", t or ""):
            s = s.strip()
            if s:
                yield s


def _entropy(c: Counter) -> float:
    tot = sum(c.values())
    if tot <= 0:
        return 0.0
    return -sum((v / tot) * math.log(v / tot + 1e-12) for v in c.values())


def _domain_weight(word: str, data: dict) -> float:
    tails = data.get("domain_tails") or []
    heads = data.get("domain_heads") or []
    if any(word.endswith(t) for t in tails) or any(word.startswith(h) for h in heads):
        return DOMAIN_BOOST
    return 0.0


def _splittable(word: str, known: set[str], jieba_known: set[str]) -> bool:
    """能不能切成两个已知词 → 是短语不是词。

    ★ **两侧都必须是多字词**。
      原来的规则（任一侧在通用词典里就算）会误杀「遗址群」
      = 词典里的「遗址」+ 通用词典里的单字「群」—— 而它在语料里出现 827 次。
    """
    a, b = word[:-1], word[-1:]
    if len(a) < 2 or len(b) < 2:
        return False
    if (a in known and b in known) or (a in jieba_known and b in jieba_known):
        return True
    return False


def discover_new_words(texts: Iterable[str], data: dict | None = None,
                       min_count: int = MIN_COUNT,
                       min_entropy: float = MIN_SIDE_ENTROPY,
                       topn: int = TOPN) -> list[dict]:
    """凝固度 + 邻字熵挖候选新词。→ 候选列表（按 score 降序）。

    ★ 结果是**候选，不是结论**。收不收由人定（界面上人工勾选）。
    ★ 六道闸门（每条都对应一个实测教训）：
      ① 频次 ≥ min_count（=5 时所有候选凝固度相同 → 分数没区分度）
      ② 首尾虚字不收
      ③ 已在词典里的不收
      ④ 可切分（两侧都是多字词）→ 淘汰
      ⑤ 邻字熵 ≥ min_entropy（熵=0 的串只在一个固定上下文里出现过）
      ⑥ 领域加权（命中领域后缀/前缀 +3.0）
    """
    data = data if data is not None else load_dict()
    known = {w["word"] for w in (data.get("words") or [])}
    j = _get_jieba()
    jieba_known: set[str] = set()
    if j is not None:
        try:
            jieba_known = set(j.dt.FREQ.keys())
        except Exception:
            jieba_known = set()

    uni: Counter = Counter()
    span: Counter = Counter()
    left: dict[str, Counter] = defaultdict(Counter)
    right: dict[str, Counter] = defaultdict(Counter)
    for seg in _sentences(texts):
        seg = "".join(_CJK_RUN.findall(seg))
        n = len(seg)
        for i, ch in enumerate(seg):
            uni[ch] += 1
        for L in range(2, 7):
            for i in range(n - L + 1):
                w = seg[i:i + L]
                span[w] += 1
                if i > 0:
                    left[w][seg[i - 1]] += 1
                if i + L < n:
                    right[w][seg[i + L]] += 1
    total = sum(uni.values())
    if not total:
        return []

    out: list[dict] = []
    for w, c in span.items():
        if c < min_count or w in known:
            continue
        if w[0] in _DROP_CHARS or w[-1] in _DROP_CHARS:
            continue
        pl = span.get(w[:-1], 0) / total
        pr = span.get(w[1:], 0) / total
        if pl <= 0 or pr <= 0:
            continue
        cohesion = math.log((c / total) / (pl * pr))
        le, re_ = _entropy(left[w]), _entropy(right[w])
        if min(le, re_) < min_entropy:
            continue
        if _splittable(w, known, jieba_known):
            continue
        score = cohesion + 0.6 * min(le, re_) + _domain_weight(w, data)
        if score < MIN_COHESION:
            continue
        out.append({"word": w, "count": c, "cohesion": round(cohesion, 3),
                    "left_entropy": round(le, 3), "right_entropy": round(re_, 3),
                    "score": round(score, 3),
                    "domain": bool(_domain_weight(w, data)),
                    "in_dict": False})
    out.sort(key=lambda d: (-d["score"], -d["count"], d["word"]))
    return out[:topn]


# ── 首批词典 ────────────────────────────────────────────────────────────
def _builtin_dict() -> dict:
    """内置的初始词典（语料实测词频捞出来的，不是拍脑袋写的）。

    ★ 这是**兜底**：正常情况下读的是 `data/dict/lzkg_domain.json`。
    """
    words: list[dict] = []

    def add(group: str, note: str, items: list[str]) -> None:
        for it in items:
            words.append({"word": it, "freq": 100000, "pos": "n",
                          "group": group, "note": note})

    add("器物", "良渚标志性礼器与纹样",
        ["玉琮", "玉璧", "玉钺", "玉璜", "玉器", "神人兽面", "兽面纹", "神人",
         "刻符", "阴刻", "透雕", "浮雕", "纹饰", "弦纹", "网格纹"])
    add("遗址", "良渚遗址群内的单体遗址",
        ["莫角山", "反山", "瑶山", "汇观山", "钟家港", "姜家山", "卞家山", "官井头",
         "美人地", "葡萄畈", "长岗山", "金家山"])
    add("称谓", "正式称谓与遗址群",
        ["良渚古城", "良渚遗址群", "良渚文化遗址群", "良渚文化", "良渚遗址",
         "遗址群"])
    add("水利", "良渚古城水利系统",
        ["城墙", "水坝", "谷口高坝", "平原坝"])
    add("对比文化", "同期与对比考古学文化",
        ["河姆渡", "马家浜", "崧泽", "钱山漾", "广富林", "草鞋山", "寺墩", "福泉山",
         "马桥", "石家河", "屈家岭", "陶寺", "二里头", "龙山文化", "大汶口"])
    add("方法", "考古学通用术语",
        ["考古", "发掘", "出土", "墓葬", "灰坑", "探方", "层位", "分期",
         "聚落", "遗址", "器物", "墓地", "陶器", "石器"])
    return {
        "version": "1.0",
        "updated_at": "2026-10-31",
        "segment_version": 1,
        "words": words,
        "synonyms": [
            {"group": "良渚文化",
             "members": ["良渚文化", "良渚遗址", "良渚文化遗址群", "良渚古城"],
             "note": "同一文化实体的不同写法；用于扩展层召回"},
        ],
        "domain_tails": ["遗址", "古城", "文化", "聚落", "墓地", "玉琮", "遗址群",
                         "博物馆", "文化遗址"],
        "domain_heads": ["良渚", "反山", "瑶山", "莫角山", "汇观山", "钟家港",
                         "姜家山", "卞家山"],
    }
