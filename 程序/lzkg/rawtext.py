# -*- coding: utf-8 -*-
"""原文资源（PDF / Markdown）与图版重写。

★ 为什么单独一个模块
  「阅读原文」窗口要同时干三件事，而它们的口径各不相同：
    ① **取原文文件**（PDF / Markdown）——要防止路径穿越；
    ② **把 Markdown 渲染成可读 HTML**——要防注入；
    ③ **把 Markdown 里的 CDN 图链接换成本站图版**——要防错配。
  混在一起写，最后一定有一处口径说不清。

─── ③ 图版重写：本模块最要紧的一条纪律 ───────────────────────────
MinerU 解析出来的 Markdown 里，图片写的是**云端 CDN 直链**
（`https://cdn-mineru.openxlab.org.cn/.../<hash>.jpg`）。
本地图版在 `04_文献图像/` 下，文件名是 `IMG-<lit_id>-<序>.<ext>`。

★ 实测结论（全库 200 篇，见 tests/test_rawtext.py）：
  · **MD 里的第 N 张图 ↔ `images` 表按 rowid 的第 N 条**，数量相等时成立；
  · 图注文本可作二次校验，但**只有 39.8% 命中**
    （库里的 `caption` 有不少被登记成了坐标文字，不是图注）。
  → 所以：**按序映射 + 图注校验，校验不过就不重写**。
    宁可让那一张图显示占位符，也**绝不错配**（错配 = 把别的文献的图
    摆到这篇的正文里 = 造谣）。

  数量不相等的 17 篇（LIT-000011/12/15/16/23/26/84/85/87/88…）**一律不重写**。
"""
from __future__ import annotations

import html
import re
import sqlite3
import unicodedata
from pathlib import Path

# ── 图链接：Markdown 图片语法 ──────────────────────────────────────────
# ★ URL 里可能含 "(" 等字符，所以用非贪婪匹配到右括号前的实际 URL；
#   不能用 `[^)]+` 之外的写法，否则会把结尾的 ")" 吃掉（实测踩过）。
# ★ ★ 必须同时认**站内相对路径**（/media/img/xxx.jpg）：
#   图版重写后的 URL 是 `/media/img/...`，只认 http(s) 的话
#   重写过的图会**匹配不上** → 变成字面文本 `![image](/media/img/...)`，
#   页面上就是一堆源码而不是图片（实测踩过）。
MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+|/media/img/[^)\s]+)\)")

# ── 正文里的小标题：MinerU 用 "#"/"##" 标章节 ─────────────────────────
MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
# ── 表格行 ────────────────────────────────────────────────────────────
MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)


def norm_text(s: str) -> str:
    """归一化：全角→半角、去标签、去所有非文字字符。用于文本比对。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[\s\W_]+", "", s)


def _caption_key(caption: str) -> str:
    """从库里的 caption 取出可比对的图注片段。

    ★ 库里的 caption 是「中文图注；英文图注」拼在一起的，
      拿中文段（分号/Fig 之前）比对才不会因为英文差异全盘失配。
    ★ 存成坐标文字的（`119°58'30"东 ...`）比对不出来 → 返回空 → 该对跳过校验。
    """
    n = norm_text(caption)
    if not n:
        return ""
    cn = n.split("Fig")[0]
    # 坐标类 caption：一串数字+东/西/南/北 → 不是图注，不参与校验
    if re.fullmatch(r"[\d°′'\"东南西北分秒]+", cn):
        return ""
    return cn


def _captions_match(cap_db: str, cap_md: str) -> bool:
    """两段图注是否指向同一张图。

    ★ 判定用**双向包含 + 前缀**三重保险，而不是「一方是另一方的子串」：
      只用单向包含会出现「MD 图注是『图1』，库 caption 是『图1 空间数据形态示意图』」
      这种前者是后者前缀的情况被漏掉（实测会漏）。
    ★ 两边都取前 12 字比前缀 —— 前缀相同就足以认定是同一张图，
      后面再长也不影响判定。
    """
    if not cap_db or not cap_md:
        return False
    a = cap_db[:12]
    b = cap_md[:12]
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    return False


def _md_figure_caption(md: str, url: str) -> str:
    """取 MD 里某张图**紧跟其后**的第一行非空文字（图注）。"""
    idx = md.find(url)
    if idx < 0:
        return ""
    after = md[idx + len(url): idx + len(url) + 400]
    for line in after.split("\n"):
        t = norm_text(line)
        if t:
            return t
    return ""


def map_md_images(md: str, images: list[dict]) -> tuple[str, dict]:
    """把 MD 里的 CDN 图链接换成本站图版链接。

    → (新 markdown, 统计 dict)

    ★ 规则（与模块 docstring 一致）：
      1. MD 图数必须等于 `images` 条数，否则**整篇不重写**；
      2. 按序一一对应；
      3. 图注校验通过（或库 caption 不可比对）才重写；
         校验**明确不通过**的，替换成占位符标记 `<!--lzkg-img-miss-->`。
    """
    urls = MD_IMG_RE.findall(md)
    stat = {"total": len(urls), "rewritten": 0, "missed": 0,
            "skipped_reason": "", "pairs": 0}
    if not urls:
        stat["skipped_reason"] = "原文无图"
        return md, stat
    if len(urls) != len(images):
        stat["skipped_reason"] = f"图数不等（原文 {len(urls)} / 库 {len(images)}），整篇不重写"
        return md, stat

    rows = list(images)
    out = md
    # 从后往前替换，避免前面的替换影响后面的偏移
    repl: list[tuple[str, str]] = []
    for i, (alt, url) in enumerate(urls):
        img = rows[i]
        fn = img.get("filename") or ""
        cap_db = _caption_key(img.get("caption") or "")
        cap_md = _md_figure_caption(md, url)
        stat["pairs"] += 1
        # ★ 替换的是**整个图片语法**（`![alt](url)`），不只是 URL：
        #   只换 URL 的话，外层的 `![alt](` 和 `)` 会留在页面上，
        #   变成「![image](<span…>)」这种半截源码（实测踩过）。
        whole = "![%s](%s)" % (alt, url)
        if cap_db and cap_md and not _captions_match(cap_db, cap_md):
            # 校验明确不通过 → 不重写，标占位
            # ★ 占位标记用**不含 HTML 特殊字符**的形式：
            #   它要活过 `_sanitize_inline` 的转义（`<!-- -->` 会被转义成
            #   `&lt;!-- --&gt;`，后面就再也匹配不到了 → 字面漏到页面上）。
            stat["missed"] += 1
            repl.append((whole, "@@LZKG_IMG_MISS:%s@@" % fn))
            continue
        repl.append((whole, "@@LZKG_IMG:%s@@" % fn))
        stat["rewritten"] += 1
    for old, new in reversed(repl):
        out = out.replace(old, new)
    return out, stat


# ── Markdown → 可读 HTML ──────────────────────────────────────────────
# ★ MinerU 的正文里有大量**语义性内联标签**：`<sub>`（下标）、`<sup>`（上标）。
#   直接当 HTML 解析有注入风险；全转义又会让页面出现字面的 "<sub>"（读着像坏掉了）。
#   → 折中：**只放行一个白名单里的无危险标签**，其余一律转义。
_ALLOWED_INLINE = ("sub", "sup", "i", "b", "em", "strong")
_ALLOWED_RE = re.compile(
    r"</?(%s)(\s[^<>]*)?>" % "|".join(_ALLOWED_INLINE), re.I)
# 其余 HTML 标签（script/style/iframe/…）→ 整段转义
_TAG_RE = re.compile(r"<[^<>]{0,200}>")
# 放行标签的占位符（避免和正文里的文本撞车）
_PH = "\x00LZKG%d\x00"


def _sanitize_inline(text: str) -> str:
    """放行白名单内联标签，其余标签转义。

    ★ 两步法（**顺序不能反**）：
      1. 先把**所有**标签临时换成占位符（并记住哪些是白名单）；
      2. 整段文本转义（`&` `<` `>` 都变成实体）；
      3. 再把占位符还原成原来的标签。
      反过来做（先转义再放行）会漏掉正文里的裸 `<`，导致注入。
    """
    allowed: list[str] = []

    def _stash(m: re.Match) -> str:
        tag = m.group(0)
        if _ALLOWED_RE.fullmatch(tag):
            allowed.append(tag)
            return _PH % (len(allowed) - 1)
        # 非白名单标签 → 当普通文本处理（后面整段转义）
        return m.group(0)

    text = _TAG_RE.sub(_stash, text)
    text = html.escape(text, quote=False)
    for i, tag in enumerate(allowed):
        text = text.replace(_PH % i, tag)
    return text


def md_to_html(md: str, base_img_dir: str = "/media/img/") -> str:
    """把 MinerU 的 Markdown 转成**安全的** HTML 片段。

    ★ 安全：非白名单标签全部转义，绝不把原文当 HTML 解析。
    ★ `<sub>`/`<sup>` 放行 —— 它们是 MinerU 标注的**下标/上标**，属正文语义。
    """
    if not md:
        return ""
    # 1) 内联标签：白名单放行，其余转义
    #    ★ 必须**先**做这一步，再做图片替换：
    #      顺序反了的话，自己生成的 <img> 会被这一步当成普通标签**再转义一次**
    #      → 页面上出现字面的 "&lt;img src=...&gt;"，图片根本不显示（实测踩过）。
    text = _sanitize_inline(md)
    # 2) 图片 → <img>（转义之后做：URL 里含特殊字符，但那时已安全）
    def _img(m: re.Match) -> str:
        url = html.escape(m.group(2), quote=True)
        alt = html.escape(m.group(1), quote=True)
        if url.startswith("/media/img/"):
            return '<img src="%s" alt="%s" loading="lazy" class="rawimg">' % (url, alt)
        # 站外直链 → 不外链（零外链纪律），也**不显示完整 URL**
        #   （那是外部服务的标识，留着等于往外递线索）
        return '<span class="rawimg-missing">[图版未本地化：原文里是站外直链]</span>'
    text = MD_IMG_RE.sub(_img, text)
    # 3) 标题
    def _head(m: re.Match) -> str:
        lvl = min(len(m.group(1)), 6)
        return "<h%d class='rawh'>%s</h%d>\n" % (lvl, m.group(2).strip(), lvl)
    text = MD_HEAD_RE.sub(_head, text)
    # 4) 表格：连续的 `|...|` 行 → 包成一个 <table>
    #    ★ 必须成组处理：Markdown 的表是**连续多行**，
    #      逐行处理会得到一堆没有 <table> 包裹的 <tr>（浏览器不认）。
    out: list[str] = []
    tbl: list[str] = []

    def _flush_table():
        if not tbl:
            return
        out.append("<table class='rawtbl'><tbody>%s</tbody></table>" % "".join(tbl))
        tbl.clear()

    for line in text.split("\n"):
        if MD_TABLE_ROW_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue        # 分隔行：跳过（不输出内容）
            tbl.append("<tr>" + "".join("<td>%s</td>" % c for c in cells) + "</tr>")
            continue
        _flush_table()
        out.append(line)
    _flush_table()
    text = "\n".join(out)
    # 5) 段落与换行
    chunks = []
    for para in re.split(r"\n{2,}", text):
        p = para.strip()
        if not p:
            continue
        if p.startswith("<h") or p.startswith("<table"):
            chunks.append(p)
            continue
        chunks.append("<p class='rawp'>%s</p>" % p.replace("\n", "<br>"))
    body = "\n".join(chunks)
    # 6) 图版占位标记 → 真正的 <img> / 可见提示
    #    ★ 这两个标记是 `map_md_images` 留下的**整段图片语法**占位，
    #      必须在这里落成 HTML；不能只换 URL（那样会留下 `![alt](` 半截源码）。
    body = re.sub(
        r"@@LZKG_IMG:([^@]*)@@",
        lambda m: ('<img src="/media/img/%s" alt="图版" loading="lazy" class="rawimg">'
                   % html.escape(m.group(1), quote=True)), body)
    body = re.sub(
        r"@@LZKG_IMG_MISS:([^@]*)@@",
        lambda m: ('<span class="rawimg-missing">[图版未匹配到本地文件：%s]</span>'
                   % html.escape(m.group(1), quote=True)), body)
    return body


# ── 原文文件定位（防路径穿越）────────────────────────────────────────
def safe_resolve(root: Path, rel: str) -> Path | None:
    """把库里的相对路径解析成**真实存在的文件**，越界一律拒绝。

    ★ `pdf_path` / `md_path` 存的是 `04_专题研究/...` 形式，
      而 `root` 通常指向 `04_专题研究` 本身 → 两种拼法都要试。
    ★ 安全：`resolve()` 之后必须仍在 root 之内，否则判非法。
    """
    if not rel or "/" in str(rel) and ".." in str(rel):
        if ".." in str(rel):
            return None
    rel = (rel or "").strip()
    if not rel or ".." in rel:
        return None
    rel = rel.replace("\\", "/")
    cands = [root / rel]
    # 去掉首段（`04_专题研究/...` → 相对 root 本身）
    if "/" in rel:
        cands.append(root / rel.split("/", 1)[1])
    for c in cands:
        try:
            p = c.resolve()
        except Exception:
            continue
        if not p.is_file():
            continue
        try:
            p.relative_to(root.resolve())
        except ValueError:
            continue     # 越界 → 拒绝
        return p
    return None
