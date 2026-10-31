# -*- coding: utf-8 -*-
"""XML 交换格式与 XML 视图。

★ 为什么选 XML 作为协作格式
  协作链路是「成员改 → 回传 → 管理员审 → 入库」。现有 xlsx 有三个硬伤：
    ① xlsx 是 zip+XML，**改一格就整表重排**，diff 极难读；
    ② 表结构固定（34 列），加字段就得改模板 + 改导入器 + 改文档；
    ③ 没法同时带**原文正文**和**图版锚点**。
  XML 一条记录一个元素，成员能只改自己那一段，回传时服务端逐元素 diff。

★ 一套格式，两处用（用户明确要求「两者都要」）
  · **交换**：`/api/export/xml`（导出）与 `/api/import/xml/preview`（回传预检）
  · **视图**：`/api/literatures/{id}/xml`（这一篇的结构化 XML）
  两处共用下面的 `LITERATURE_XML` 结构 —— **不写两套格式**。

─── 命名空间 ────────────────────────────────────────────────────────
用 `urn:lzkg:literature:1` 这样的稳定 URN，而不是 `http://…`：
后者会被当成「可访问的网址」，反而误导。

─── 安全 ────────────────────────────────────────────────────────────
解析上传的 XML 一律用 `defusedxml`（防 XXE / billion laughs）。
本机已装 defusedxml 0.7.1；没有时**不静默降级到标准库**，
而是明确报错（标准库 ElementTree 对外部实体的防护要看版本，不能赌）。
"""
from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

NS = "urn:lzkg:literature:1"
FORMAT_VERSION = "1.0"

# 参与交换的题录字段（**不含**原文正文与图版二进制，那些走资源端点）
EXCHANGE_FIELDS = [
    ("lit_id", "标识符"),
    ("title", "题名"),
    ("authors", "作者"),
    ("lang", "语种"),
    ("year", "出版年"),
    ("venue", "载体题名"),
    ("publisher", "出版者"),
    ("pages_range", "页码范围"),
    ("abstract", "摘要"),
    ("keywords", "关键词"),
    ("source_url", "来源链接"),
    ("open_type", "开放类型"),
    ("doc_type", "文献类型"),
    ("period_tags", "时间分期标签"),
    ("region_tags", "空间分区标签"),
    ("topic_tags", "主题分类标签"),
    ("lineage_tags", "文化谱系标签"),
    ("clc", "CLC分类号"),
    ("subject_terms", "汉语主题词"),
    ("recorder", "著录人"),
    ("record_date", "著录日期"),
    ("review_status", "审核状态"),
    ("remark", "著录备注"),
]


def _tag(name: str) -> str:
    return "{%s}%s" % (NS, name)


def _register_ns() -> None:
    """把命名空间注册成好认的前缀 `lzkg`。

    ★ 不注册的话 ElementTree 会输出 `ns0:` / `ns1:` —— 机器能读，
      但人看协作文件很难受（这不是给人「随便看看」的格式，是要来回传改的）。
    """
    ET.register_namespace("lzkg", NS)


# ══════════════════════════════════════════════════════════════════
# 一、导出（交换 + 视图共用）
# ══════════════════════════════════════════════════════════════════
def literature_to_xml(lit: dict, images: list[dict] | None = None,
                       tags: dict | None = None,
                       include_images: bool = True,
                       include_fulltext_ref: bool = True) -> str:
    """一篇文献 → XML 字符串。

    `include_images=False` 时只出题录（图版另走资源端点），视图用。
    """
    _register_ns()
    root = ET.Element(_tag("literature"))
    root.set("formatVersion", FORMAT_VERSION)
    root.set("litId", str(lit.get("lit_id") or ""))

    meta = ET.SubElement(root, _tag("metadata"))
    for key, label in EXCHANGE_FIELDS:
        val = lit.get(key)
        if val is None:
            continue
        sval = str(val).strip()
        if not sval:
            continue
        el = ET.SubElement(meta, _tag("field"))
        el.set("name", key)
        el.set("label", label)
        el.text = sval

    # 资源指针（**只给指针与相对路径，不把文件塞进 XML**）
    res = ET.SubElement(root, _tag("resources"))
    if lit.get("pdf_name"):
        e = ET.SubElement(res, _tag("resource"))
        e.set("kind", "pdf")
        e.set("filename", str(lit["pdf_name"]))
        e.set("pages", str(lit.get("pdf_pages") or ""))
        e.set("href", "/api/raw/%s/pdf" % (lit.get("lit_id") or ""))
    if lit.get("md_path"):
        e = ET.SubElement(res, _tag("resource"))
        e.set("kind", "markdown")
        e.set("filename", str(lit["md_path"]).split("/")[-1])
        e.set("chars", str(lit.get("md_chars") or ""))
        e.set("pages", str(lit.get("md_pages") or ""))
        e.set("href", "/api/raw/%s/markdown" % (lit.get("lit_id") or ""))

    # 标签（四维，分组）
    if tags:
        tg = ET.SubElement(root, _tag("tags"))
        for dim, items in tags.items():
            if not items:
                continue
            d = ET.SubElement(tg, _tag("dimension"))
            d.set("name", dim)
            for it in items:
                t = ET.SubElement(d, _tag("tag"))
                t.set("code", str(it.get("code") or ""))
                t.text = str(it.get("name") or "")

    # 图版
    if include_images and images:
        ims = ET.SubElement(root, _tag("figures"))
        for im in images:
            e = ET.SubElement(ims, _tag("figure"))
            e.set("id", str(im.get("img_id") or ""))
            e.set("filename", str(im.get("filename") or ""))
            e.set("href", "/media/img/%s" % (im.get("filename") or ""))
            e.set("figNo", str(im.get("fig_no") or ""))
            e.set("pdfPage", str(im.get("pdf_page") or ""))
            for key in ("img_title", "img_type", "obj_type", "caption",
                        "ai_desc", "review_status", "reviewer", "review_date"):
                v = im.get(key)
                if v is None or not str(v).strip():
                    continue
                s = ET.SubElement(e, _tag("annotation"))
                s.set("field", key)
                s.text = str(v).strip()

    # 全文（**可选**：默认只给指针，视图时才带正文）
    if include_fulltext_ref:
        ft = ET.SubElement(root, _tag("fulltext"))
        ft.set("format", "markdown")
        ft.set("href", "/api/raw/%s/markdown" % (lit.get("lit_id") or ""))
        ft.set("chars", str(lit.get("md_chars") or ""))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!-- 良渚文化文献目录站 · 文献交换格式 v%s -->\n%s' % (FORMAT_VERSION, body))


def dataset_to_xml(rows: list[dict], images_by_lit: dict | None = None,
                    tags_by_lit: dict | None = None) -> str:
    """多篇 → 一个数据集 XML（协作交换用）。"""
    _register_ns()
    root = ET.Element(_tag("dataset"))
    root.set("formatVersion", FORMAT_VERSION)
    root.set("count", str(len(rows)))
    root.set("source", "良渚文化文献目录站")
    for lit in rows:
        node = literature_to_xml(
            lit,
            images=(images_by_lit or {}).get(lit.get("lit_id"), []),
            tags=(tags_by_lit or {}).get(lit.get("lit_id")),
            include_images=True,
            include_fulltext_ref=False)
        # 拆出单篇节点并入数据集
        frag = ET.fromstring(node)
        root.append(frag)
    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!-- 良渚文化文献目录站 · 协作交换数据集 v%s -->\n%s'
            % (FORMAT_VERSION, ET.tostring(root, encoding="unicode")))


# ══════════════════════════════════════════════════════════════════
# 二、解析（回传预检用）
# ══════════════════════════════════════════════════════════════════
def _parse_xml(data: bytes) -> ET.Element:
    """安全解析 XML。**用 defusedxml**，没有就明确报错，不静默降级。"""
    try:
        from defusedxml.ElementTree import fromstring as _safe_fromstring
    except ImportError:
        raise ValueError(
            "本机未装 defusedxml，**不解析**上传的 XML（防 XXE / billion laughs）。"
            "请先 `pip install defusedxml`。")
    return _safe_fromstring(data)


def parse_dataset_xml(data: bytes) -> dict:
    """解析回传的 XML → `{records: [...], errors: [...]}`。

    ★ 只做**解析与字段提取**，不做 diff、不写库 —— 那是 preview/apply 的事。
    ★ 结构不认识就报错，不要「尽量猜着解析」：猜出来的字段会静默错位。
    """
    try:
        root = _parse_xml(data)
    except ValueError:
        raise
    except ET.ParseError as e:
        raise ValueError("XML 格式错误：%s" % e)

    if root.tag == _tag("literature"):
        nodes = [root]
    elif root.tag == _tag("dataset"):
        nodes = list(root.findall(_tag("literature")))
    else:
        raise ValueError(
            "根元素应为 {%s}literature 或 {%s}dataset，实际是 {%s}"
            % (NS, NS, root.tag))

    records: list[dict] = []
    errors: list[str] = []
    for i, node in enumerate(nodes):
        rec: dict = {}
        lit_id = node.get("litId") or ""
        rec["lit_id"] = lit_id
        meta = node.find(_tag("metadata"))
        if meta is None:
            errors.append("第 %d 条缺 metadata" % (i + 1))
        else:
            for f in meta.findall(_tag("field")):
                name = f.get("name") or ""
                if not name:
                    continue
                rec[name] = (f.text or "").strip()
            if not rec.get("lit_id") and lit_id:
                rec["lit_id"] = lit_id
        # 图版标注
        figs = []
        fignode = node.find(_tag("figures"))
        if fignode is not None:
            for fg in fignode.findall(_tag("figure")):
                d = {"img_id": fg.get("id") or "",
                     "filename": fg.get("filename") or ""}
                for an in fg.findall(_tag("annotation")):
                    d[an.get("field") or ""] = (an.text or "").strip()
                figs.append(d)
        if figs:
            rec["_figures"] = figs
        if not rec.get("lit_id"):
            errors.append("第 %d 条缺 lit_id" % (i + 1))
        records.append(rec)
    return {"records": records, "errors": errors, "count": len(records)}
