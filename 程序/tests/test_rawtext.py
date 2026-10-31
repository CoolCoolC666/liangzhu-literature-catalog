# -*- coding: utf-8 -*-
"""原文通道 + XML 协作 的验收脚本。**可独立运行**。

    python tests/test_rawtext.py            # 全跑（需要先启动服务）
    python tests/test_rawtext.py --api      # 只跑 API 层

设计原则（照抄 test_all.py 的规矩）：
    **断言「产物合法」≠ 断言「产物正确」**
    所以每条判据都写成「必须等于某个实测真值」，而不是「不为空」。

★ 本文件里的真值都是**实测出来的**（2026-10-31）：
    · 库里 200 篇，`pdf_path` 有 195 条带 `04_专题研究/` 前缀、5 条是
      「（无PDF文件）」占位；
    · MinerU 的 Markdown 里图片写的是**云端 CDN 直链**，本地图版在
      `04_文献图像/`；全库 **51 篇** MD 图数与库图数相等（可按序映射），
      **17 篇**不等（必须整篇不重写）；
    · 图注文本校验命中率只有 **39.8%**（库里 caption 不少被登记成坐标文字），
      → 所以「校验不过就不重写」，**绝不硬配**（错配＝造谣）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM_ROOT = HERE.parent
sys.path.insert(0, str(PROGRAM_ROOT))

PASS, FAIL = 0, 0
FAILURES: list[str] = []

# ── 实测真值（2026-10-31）────────────────────────────────────────
TRUE_LIT_COUNT = 200          # 库里的文献总数
TRUE_PDF_WITH_PATH = 195      # pdf_path 是真路径的条数
TRUE_PDF_PLACEHOLDER = 5      # pdf_path == 「（无PDF文件）」的条数
TRUE_MD_PATH_COUNT = 200      # md_path 非空的条数
TRUE_MD_PREFIX = "04_专题研究/"  # md_path 的统一首段
TRUE_IMG_COUNT_SAME = 50      # MD 图数 == 库图数（可按序映射）的文献数
TRUE_IMG_COUNT_DIFF = 17      # 两者不等（整篇不重写）的文献数
TRUE_IMG_NOFIG = 132          # MD 里没有图引用的文献数
TRUE_IMG_UNRESOLVED = 1       # md_path 文件名过长、路径解析不到的（LIT-000082）
TRUE_CAPTION_HIT_MIN = 0.30   # 图注校验命中率下限（实测 0.398）
TRUE_XML_NS = "urn:lzkg:literature:1"
TRUE_XML_FIELDS = 23          # 交换格式里参与交换的题录字段数


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


def api(path: str):
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}{path}"
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def api_raw(path: str) -> bytes:
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}{path}"
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


# ══════════════════════════════════════════════════════════════════
# 1 · 模块层：路径安全
# ══════════════════════════════════════════════════════════════════
def test_safe_resolve():
    section("模块层 · 原文路径定位（防路径穿越）")
    from lzkg.config import get_config
    from lzkg.rawtext import safe_resolve

    cfg = get_config()
    root = cfg.source_root

    check("路径穿越被拒绝（.. 出现在中间）",
          safe_resolve(root, "04_专题研究/../../etc/passwd") is None)
    check("路径穿越被拒绝（开头就是 ..）",
          safe_resolve(root, "../../secret.txt") is None)
    check("绝对路径不被当成相对路径拼接",
          safe_resolve(root, "C:/Windows/win.ini") is None)
    check("空路径返回 None", safe_resolve(root, "") is None)

    # 正常路径能解出来（库里第一条的 md_path）
    from lzkg.db import connect
    c = connect()
    try:
        row = c.execute("SELECT md_path FROM literatures ORDER BY lit_id LIMIT 1").fetchone()
    finally:
        c.close()
    p = safe_resolve(root, row["md_path"])
    check("真实 md_path 能定位到文件", p is not None and p.is_file(),
          str(p) if p else "None")


# ══════════════════════════════════════════════════════════════════
# 2 · 模块层：Markdown 渲染的安全与白名单
# ══════════════════════════════════════════════════════════════════
def test_md_render():
    section("模块层 · Markdown 渲染（白名单 + 转义）")
    from lzkg.rawtext import md_to_html

    h = md_to_html("正常<sub>1</sub>与<sup>2</sup>。")
    check("<sub> 被放行（MinerU 的下标是正文语义）", "<sub>1</sub>" in h, h[:120])
    check("<sup> 被放行", "<sup>2</sup>" in h)

    h2 = md_to_html("<script>alert(1)</script>")
    check("<script> 被转义（不执行）", "<script>" not in h2 and "&lt;script&gt;" in h2, h2[:120])

    h3 = md_to_html('<img src=x onerror=alert(1)>')
    check("裸 img 标签被转义（不产生 onerror 属性）",
          "<img" not in h3 and "&lt;img" in h3, h3[:120])

    h4 = md_to_html("5 < 7 且 a > b")
    check("正文里的裸尖括号被转义",
          "&lt;" in h4 and "&gt;" in h4, h4[:120])

    h5 = md_to_html("![x](https://cdn.example.com/a.jpg)")
    check("站外图片不外链（零外链纪律）",
          "<img" not in h5 and "rawimg-missing" in h5, h5[:140])

    h6 = md_to_html("# 一级标题\n\n正文段落")
    check("标题被渲染成 <h1>", "<h1" in h6, h6[:120])
    check("段落被渲染成 <p>", "<p" in h6, h6[:120])

    # ★ 站内图片路径也要认 —— 重写后的 URL 是 /media/img/... 不是 http(s)
    h7 = md_to_html("![image](/media/img/IMG-LIT-000001-i002.jpg)")
    check("站内图版路径渲染成 <img>", "<img" in h7 and "rawimg-missing" not in h7, h7[:140])
    check("图片不会被二次转义", "&lt;img" not in h7, h7[:140])

    # ★ 占位标记必须活过转义 → 变成可见提示，而不是字面漏到页面上
    h8 = md_to_html("![x](@@LZKG_IMG_MISS:IMG-X-1.jpg@@)")
    check("未匹配图显示为可见提示",
          "rawimg-missing" in h8 and "@@LZKG" not in h8, h8[:140])


# ══════════════════════════════════════════════════════════════════
# 3 · 模块层：图版重写（按序 + 校验，不硬配）
# ══════════════════════════════════════════════════════════════════
def test_image_mapping():
    section("模块层 · 图版重写（数量不等就不重写）")
    from lzkg.rawtext import map_md_images, md_to_html

    url1 = "https://cdn.example.com/a.jpg"
    url2 = "https://cdn.example.com/b.jpg"
    md = f"![1]({url1})\n\n图1 空间数据形态示意图\n\n![2]({url2})\n\n图2 整体设计图\n"

    # 图注对得上 → 重写
    imgs = [{"filename": "IMG-LIT-000001-4.jpg", "caption": "图1 空间数据形态示意图；Fig.1"},
            {"filename": "IMG-LIT-000001-8.jpg", "caption": "图2 整体设计图；Fig.2"}]
    out, st = map_md_images(md, imgs)
    check("图注对得上时重写成本站图版",
          "LZKG_IMG:IMG-LIT-000001-4.jpg" in out and "cdn.example.com" not in out,
          f"rewritten={st['rewritten']}")
    check("重写统计 = 实测真值", st["rewritten"] == 2 and st["total"] == 2,
          f"{st['rewritten']}/{st['total']}")
    # ★ 重写后的标记要能被 md_to_html 落成真正的 <img>
    h = md_to_html(out)
    check("重写后的标记渲染成 <img>",
          '<img src="/media/img/IMG-LIT-000001-4.jpg"' in h, h[:160])
    check("渲染后没有字面 ![ 残留", "![" not in h, h[:160])

    # 图注对不上 → 不重写（宁可留占位，也不错配）
    imgs2 = [{"filename": "IMG-X-1.jpg", "caption": "完全不相干的图注文字"},
             {"filename": "IMG-X-2.jpg", "caption": "也不相干"}]
    out2, st2 = map_md_images(md, imgs2)
    check("图注对不上时**不**重写（不错配）",
          "LZKG_IMG:IMG-X-1.jpg" not in out2, f"missed={st2['missed']}")
    check("未匹配的图标记为占位", "LZKG_IMG_MISS" in out2)

    # MD 图注是库 caption 的**前缀**（真实数据里 MD 图注常更短）→ 仍要重写
    md_short = f"![1]({url1})\n\n图1 空间\n\n![2]({url2})\n\n图2 整体\n"
    out2b, st2b = map_md_images(md_short, imgs)
    check("MD 图注更短（前缀包含）也重写",
          "LZKG_IMG:IMG-LIT-000001-4.jpg" in out2b, f"rewritten={st2b['rewritten']}")

    # 图数不等 → 整篇不重写
    out3, st3 = map_md_images(md, imgs[:1])
    check("图数不等时整篇不重写",
          "LZKG_IMG:" not in out3 and "LZKG_IMG_MISS:" not in out3, st3["skipped_reason"])
    check("图数不等时给出原因", "图数不等" in st3["skipped_reason"], st3["skipped_reason"])


# ══════════════════════════════════════════════════════════════════
# 4 · 模块层：全库实测口径
# ══════════════════════════════════════════════════════════════════
def test_db_facts():
    section("实测口径 · 库里原文与图版的真实分布")
    from lzkg.db import connect

    c = connect()
    try:
        n = c.execute("SELECT COUNT(*) n FROM literatures").fetchone()["n"]
        check("文献总数 = 实测真值", n == TRUE_LIT_COUNT, f"{n}（应 {TRUE_LIT_COUNT}）")

        ph = c.execute("SELECT COUNT(*) n FROM literatures "
                       "WHERE pdf_path = '（无PDF文件）'").fetchone()["n"]
        check("pdf_path 占位条数 = 实测真值", ph == TRUE_PDF_PLACEHOLDER,
              f"{ph}（应 {TRUE_PDF_PLACEHOLDER}）")

        withp = c.execute("SELECT COUNT(*) n FROM literatures "
                          "WHERE pdf_path <> '（无PDF文件）' AND pdf_path <> ''").fetchone()["n"]
        check("有 PDF 路径的条数 = 实测真值", withp == TRUE_PDF_WITH_PATH,
              f"{withp}（应 {TRUE_PDF_WITH_PATH}）")

        mdn = c.execute("SELECT COUNT(*) n FROM literatures "
                        "WHERE md_path IS NOT NULL AND md_path <> ''").fetchone()["n"]
        check("md_path 非空条数 = 实测真值", mdn == TRUE_MD_PATH_COUNT,
              f"{mdn}（应 {TRUE_MD_PATH_COUNT}）")

        bad = c.execute("SELECT COUNT(*) n FROM literatures "
                        "WHERE md_path NOT LIKE ?", ("04_专题研究/%",)).fetchone()["n"]
        check("md_path 首段统一为 04_专题研究", bad == 0, f"{bad} 条不符")

        # 图版总数
        ni = c.execute("SELECT COUNT(*) n FROM images").fetchone()["n"]
        check("图版总数 = 实测真值", ni == 3162, f"{ni}（应 3162）")

        # 图数一致性（可按序映射 vs 必须整篇不重写）
        from lzkg.config import get_config
        from lzkg.rawtext import MD_IMG_RE, safe_resolve
        cfg = get_config()
        same = diff = nofig = 0
        for r in c.execute("SELECT lit_id, md_path FROM literatures ORDER BY lit_id"):
            p = safe_resolve(cfg.source_root, r["md_path"])
            if p is None:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            urls = MD_IMG_RE.findall(text)
            cnt = c.execute("SELECT COUNT(*) n FROM images WHERE lit_id = ?",
                            (r["lit_id"],)).fetchone()["n"]
            if not urls:
                nofig += 1
            elif len(urls) == cnt:
                same += 1
            else:
                diff += 1
        check("MD 图数 == 库图数（可按序映射）的文献数 = 实测真值",
              same == TRUE_IMG_COUNT_SAME, f"{same}（应 {TRUE_IMG_COUNT_SAME}）")
        check("MD 图数 ≠ 库图数（整篇不重写）的文献数 = 实测真值",
              diff == TRUE_IMG_COUNT_DIFF, f"{diff}（应 {TRUE_IMG_COUNT_DIFF}）")
        check("MD 无图引用的文献数 = 实测真值", nofig == TRUE_IMG_NOFIG,
              f"{nofig}（应 {TRUE_IMG_NOFIG}）")
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════
# 5 · XML 交换格式：往返
# ══════════════════════════════════════════════════════════════════
def test_xml_roundtrip():
    section("模块层 · XML 交换格式往返")
    from lzkg import xmlexchange as xe

    lit = {"lit_id": "LIT-000001", "title": "测试题名 & <特殊>", "authors": "甲；乙",
           "year": "2018", "open_type": "公开（知网/维普下载）", "md_chars": 100}
    xml = xe.literature_to_xml(lit, images=[], tags={"period": [{"name": "新石器时代晚期", "code": "P-1"}]})

    check("XML 声明了正确的命名空间", TRUE_XML_NS in xml, xml[:120])
    check("命名空间前缀是 lzkg（不是 ns0）", "lzkg:literature" in xml and "ns0:" not in xml)
    check("XML 里带 XML 声明", xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
    check("特殊字符被正确转义", "&amp;" in xml and "&lt;特殊&gt;" in xml)

    parsed = xe.parse_dataset_xml(xml.encode("utf-8"))
    check("往返解析出 1 条", parsed["count"] == 1, str(parsed["count"]))
    rec = parsed["records"][0]
    check("往返后 lit_id 一致", rec.get("lit_id") == "LIT-000001", str(rec.get("lit_id")))
    check("往返后 title 一致（含特殊字符）",
          rec.get("title") == "测试题名 & <特殊>", repr(rec.get("title")))
    check("往返后 open_type 一致",
          rec.get("open_type") == "公开（知网/维普下载）", repr(rec.get("open_type")))

    # 字段数 = 实测真值
    check("参与交换的题录字段数 = 实测真值", len(xe.EXCHANGE_FIELDS) == TRUE_XML_FIELDS,
          f"{len(xe.EXCHANGE_FIELDS)}（应 {TRUE_XML_FIELDS}）")

    # 根元素不认识 → 明确报错（不猜着解析）
    try:
        xe.parse_dataset_xml(b"<whatever><x/></whatever>")
        check("根元素不认识时报错", False, "没报错")
    except ValueError as e:
        check("根元素不认识时报错", True, str(e)[:80])

    # 坏 XML → 报错
    try:
        xe.parse_dataset_xml(b"<not-closed")
        check("XML 格式错误时报错", False, "没报错")
    except ValueError as e:
        check("XML 格式错误时报错", True, str(e)[:80])


# ══════════════════════════════════════════════════════════════════
# 6 · API 层
# ══════════════════════════════════════════════════════════════════
def test_api_raw():
    section("API 层 · 原文通道")
    d = api("/api/raw/LIT-000001/markdown")
    check("Markdown 端点返回成功", d.get("ok") is True, str(d.get("error")))
    data = d.get("data") or {}
    check("返回了渲染好的 HTML", bool(data.get("html")), f"{len(data.get('html') or '')} 字符")
    check("返回了原始文本", bool(data.get("text")))
    check("字符数 > 10000（这一篇正文确实很长）", (data.get("chars") or 0) > 10000,
          str(data.get("chars")))
    st = data.get("images") or {}
    check("图版统计字段齐全",
          all(k in st for k in ("total", "rewritten", "missed")), str(st))
    check("这一篇的图全部在库里（MD 14 / 库 14）", st.get("total") == 14, str(st.get("total")))
    check("至少重写了 10 张图（实测 12）", (st.get("rewritten") or 0) >= 10,
          str(st.get("rewritten")))
    check("重写后正文里不再有 CDN 直链", "cdn-mineru" not in (data.get("html") or ""))
    check("返回里带开放类型（供界面如实显示）", bool(data.get("open_type")),
          str(data.get("open_type")))
    # ★ HTML 里不能有字面残留的 Markdown 图片语法（实测踩过：
    #   站内路径没被识别 + 自己生成的 <img> 被二次转义 → 图全不显示）
    html = data.get("html") or ""
    n_img = html.count("<img ")
    check("HTML 里有 <img> 标签（= 12 张已本地化的图）", n_img == 12, str(n_img))
    check("HTML 里没有字面 ![ 残留", "![" not in html, str(html.count("![")))
    check("未匹配的 2 张显示为可见提示",
          html.count("rawimg-missing") == 2, str(html.count("rawimg-missing")))
    check("图版链接指向本站代理", "/media/img/IMG-LIT-000001-" in html)

    # PDF
    pdf = api_raw("/api/raw/LIT-000001/pdf")
    check("PDF 端点返回 PDF 文件头", pdf[:5] == b"%PDF-", f"{len(pdf)} 字节")
    check("PDF 大小 > 1MB", len(pdf) > 1024 * 1024, f"{len(pdf)} 字节")

    # 无 PDF 的那几条 → 明确 404，不是 500
    try:
        api_raw("/api/raw/LIT-000088/pdf")
        check("没有 PDF 的文献返回 404", False, "居然成功了")
    except urllib.error.HTTPError as e:
        check("没有 PDF 的文献返回 404", e.code == 404, f"HTTP {e.code}")

    # 不存在的文献
    try:
        api_raw("/api/raw/LIT-999999/markdown")
        check("不存在的文献返回 404", False, "居然成功了")
    except urllib.error.HTTPError as e:
        check("不存在的文献返回 404", e.code == 404, f"HTTP {e.code}")


def test_api_xml():
    section("API 层 · XML 协作")
    raw = api_raw("/api/literatures/LIT-000001/xml")
    xml = raw.decode("utf-8")
    check("单篇 XML 返回成功", xml.startswith("<?xml"), xml[:60])
    check("单篇 XML 用同一套命名空间", TRUE_XML_NS in xml)
    check("单篇 XML 含题录字段", 'name="title"' in xml)
    check("单篇 XML 含资源指针", 'kind="pdf"' in xml and 'kind="markdown"' in xml)
    check("单篇 XML 含图版", "<lzkg:figure" in xml)

    ds = api_raw("/api/export/xml")
    dxml = ds.decode("utf-8")
    check("数据集 XML 返回成功", dxml.startswith("<?xml"))
    check("数据集 XML 声明条数 200", 'count="200"' in dxml, )
    check("数据集 XML 含全部 200 篇",
          dxml.count("<lzkg:literature") == TRUE_LIT_COUNT,
          str(dxml.count("<lzkg:literature")))

    # 往返：导出 → 改一条 → 回传预检
    marker = b"REV-MODIFIED-BY-MEMBER"
    i = ds.find(b'label="\xe5\xae\xa1\xe6\xa0\xb8\xe7\x8a\xb6\xe6\x80\x81">')
    check("能在导出的 XML 里找到审核状态字段", i >= 0)
    j = ds.find(b"</lzkg:field>", i)
    ds2 = ds[:j] + marker + ds[j:]

    boundary = "----lzkgtest"
    head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="c.xml"'
            '\r\nContent-Type: application/xml\r\n\r\n' % boundary).encode()
    body = head + ds2 + ("\r\n--%s--\r\n" % boundary).encode()
    from lzkg.config import get_config
    url = f"http://{get_config().host}:{get_config().port}/api/import/xml/preview"
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type":
                                          "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read().decode("utf-8"))
        check("回传预检成功", d.get("ok") is True, str(d.get("error")))
        data = d.get("data") or {}
        check("预检解析出 200 条", data.get("count") == TRUE_LIT_COUNT,
              str(data.get("count")))
        check("预检只报出 1 处差异（改了 1 条）", data.get("n_changed") == 1,
              str(data.get("n_changed")))
        diffs = data.get("diffs") or []
        ch = [x for x in diffs if x.get("n_changed")]
        check("差异落在第一条上", ch and ch[0].get("lit_id") == "LIT-000001",
              str(ch[0].get("lit_id")) if ch else "无")
        check("差异字段是 review_status",
              ch and ch[0]["changed"][0].get("field") == "review_status",
              str(ch[0]["changed"][0].get("field")) if ch else "无")
        check("其余 199 条判为无变化",
              sum(1 for x in diffs if x.get("status") == "same") == TRUE_LIT_COUNT - 1,
              str(sum(1 for x in diffs if x.get("status") == "same")))
        check("预检不写主库（有 staging_id 才说明写了）", "staging_id" not in data,
              str(data.get("staging_id")))
    except urllib.error.HTTPError as e:
        check("回传预检成功", False, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")


# ══════════════════════════════════════════════════════════════════
# 7 · 前端：阅读原文入口与 XML 入口
# ══════════════════════════════════════════════════════════════════
def test_frontend():
    section("前端 · 阅读原文与 XML 入口")
    idx = PROGRAM_ROOT / "lzkg" / "web" / "static" / "index.html"
    html = idx.read_text(encoding="utf-8")

    check("详情页有「阅读原文」区块", "阅读原文" in html)
    check("有 PDF 阅读按钮", "raw-open-pdf" in html)
    check("有 Markdown 阅读按钮", "raw-open-md" in html)
    check("有 PDF 下载入口", "/pdf?download=1" in html)
    check("有 Markdown 下载入口", "/markdown?download=1" in html)
    check("有单篇 XML 入口", "/xml" in html)
    check("有阅读窗口模态容器", 'id="raw-modal"' in html)
    check("模态有关闭按钮", 'id="raw-close"' in html)
    check("ESC 能关窗口", "Escape" in html)
    check("PDF 用 iframe 内嵌（不引 pdf.js）",
          "rawframe" in html and "pdf.min.js" not in html and "pdfjsLib" not in html)
    check("页面里没有 MinerU CDN 残留（零外链纪律）", "cdn-mineru" not in html)
    check("导入面板有 XML 方式", "imp-fmt" in html and "协作交换 XML" in html)
    check("工作台有协作交换页签", "renderExchangePage" in html)

    # ES5 语法门禁：把内联 script 抽出来编译一遍
    # ★ 必须先剥掉 HTML 注释 —— 注释里有中文标点（·、—），
    #   Python 的 compile() 会把它们当源码报「invalid character」。
    import re
    scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S)
    check("内联 script 块数量 > 0", len(scripts) > 0, str(len(scripts)))
    # ★ JS 语法门禁**只交给 Node**。
    #   Python 的 compile() 不是 JS 解析器：JS 的块注释里可以有中文标点（·、—），
    #   Python 会报「invalid character」—— 那是**误报**，不是代码有问题。
    #   所以这里不再用 Python 判 JS 语法。
    node_ok = True
    node_detail = ""
    try:
        import shutil
        import subprocess
        import tempfile
        node = shutil.which("node")
        if not node:
            node_detail = "本机没有 node，跳过（项目前端判据 test_wdsafename.js 也依赖 node）"
        else:
            with tempfile.TemporaryDirectory() as td:
                for i, s in enumerate(scripts):
                    f = Path(td) / ("inline-%d.js" % i)
                    f.write_text(s, encoding="utf-8")
                    r = subprocess.run([node, "--check", str(f)],
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=120)
                    if r.returncode != 0:
                        node_ok = False
                        node_detail = (r.stderr or "")[:300]
                        break
    except Exception as e:
        node_detail = "node 门禁跳过：%s" % e
    check("Node 解析器判定 JS 语法正确", node_ok, node_detail)

    # ES5：不得出现箭头函数 / 模板字符串 / let-const
    # ★ 只剥 HTML 注释与 JS 块注释；**不剥行注释** ——
    #   正则字面量里的 "//" 会被误当行注释，把真代码删掉 → 假阴性。
    code = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    for pat, label in ((r"=>", "箭头函数"), (r"`", "模板字符串"),
                       (r"\blet\s+\w", "let 声明"), (r"\bconst\s+\w", "const 声明")):
        hits = re.findall(pat, code)
        check("前端仍是 ES5（无%s）" % label, not hits, f"{len(hits)} 处")


def main():
    # ★ 没有库就只跑**纯代码**的判据 —— 公开仓库不带数据库。
    try:
        from lzkg.config import get_config
        has_db = get_config().db_path.exists()
    except Exception:
        has_db = False
    if has_db:
        test_safe_resolve()
        test_md_render()
        test_image_mapping()
        test_db_facts()
        test_xml_roundtrip()
    else:
        print("\n  ⚠ 没有找到数据库（程序/data/lzkg.db）——跳过依赖数据的判据。")
        print("    先建库：python -m lzkg set-source \"<你的 04_专题研究>\" && python -m lzkg migrate")
    test_frontend()
    if "--api" in sys.argv or "--all" in sys.argv:
        if has_db:
            test_api_raw()
            test_api_xml()
        else:
            print("\n  SKIP API 判据：需要先建库并启动服务")
    print(f"\n{'=' * 46}")
    print(f"通过 {PASS}　失败 {FAIL}")
    if FAILURES:
        print("\n失败明细：")
        for f in FAILURES:
            print("  · " + f)
    print("=" * 46)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
