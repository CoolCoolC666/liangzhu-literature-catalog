# -*- coding: utf-8 -*-
"""主库（宁波地域文明探源工程考古数据库 nbkg.nitlab.top）字段映射导出。

本模块只负责「把本库的行变成主库能吃的列」，**不做字段名猜测**：
映射关系写在 `_docs/数据契约.md` §4，本表是它的可执行版本。

关键设计
--------
· **四维标签导出成多值串 + 保留代码**：主库要的是受控词表，导出时把
  `S-0803 文创设计; S-0804 产品开发` 拆成 `S-0803｜文创设计；S-0804｜产品开发`，
  代码不丢（主库按代码归一）。
· **来源三件套完整带出**（链接 / 类型 / 判据痕迹）——这是本库相对通用元数据的
  强项，也是主库「可回溯」要求的关键。
· **开放类型单独一列**：主库据此决定能否公开。
"""
from __future__ import annotations

import sqlite3

from .migrate import TAG_DIMS, normalize_tags

NBKG_COLUMNS = [
    "文献标识符", "建议标识符", "本组主题", "文献类型代码", "题名", "责任者", "语种",
    "出版时间", "载体题名", "出版者", "页码范围", "摘要", "原文关键词",
    "来源链接", "数据来源类型", "来源判据", "开放类型与使用条件",
    "时间分期标签", "空间分区标签", "主题分类标签", "文化谱系标签",
    "标签代码明细", "CLC分类号", "汉语主题词", "著录人", "著录日期", "审核状态",
    "原始PDF文件名", "原始PDF存储地址", "正文存储标识符", "Markdown相对路径",
    "MinerU解析状态", "解析问题备注", "正文字符数", "正文字页数", "图版定位符数",
    "图像条目数", "入库标记",
]


def _split_tags(raw: str, dim: str) -> tuple[str, str]:
    """→ (展示串「名称；名称」, 代码串「S-0803；S-0804」)。"""
    tags = normalize_tags(raw, dim)
    if not tags:
        return "", ""
    names = "；".join(t["name"] for t in tags)
    codes = "；".join(t["code"] for t in tags if t.get("code"))
    return names, codes


def nbkg_export_rows(conn: sqlite3.Connection) -> list[dict]:
    img_count = {r["lit_id"]: r["n"] for r in conn.execute(
        "SELECT lit_id, COUNT(*) n FROM images GROUP BY lit_id")}
    out = []
    for r in conn.execute("SELECT * FROM literatures ORDER BY lit_id"):
        d = dict(r)
        p_names, p_codes = _split_tags(d.get("period_tags", ""), "period")
        r_names, r_codes = _split_tags(d.get("region_tags", ""), "region")
        t_names, t_codes = _split_tags(d.get("topic_tags", ""), "topic")
        l_names, l_codes = _split_tags(d.get("lineage_tags", ""), "lineage")
        code_detail = "；".join(x for x in (p_codes, r_codes, t_codes, l_codes) if x)
        out.append({
            "文献标识符": d["lit_id"],
            "建议标识符": "NB-LIT-" + d["lit_id"].split("-")[-1],
            "本组主题": d.get("theme", ""),
            "文献类型代码": d.get("doc_type", ""),
            "题名": d.get("title", ""),
            "责任者": d.get("authors", ""),
            "语种": d.get("lang", ""),
            "出版时间": d.get("year", ""),
            "载体题名": d.get("venue", ""),
            "出版者": d.get("publisher", ""),
            "页码范围": d.get("pages_range", ""),
            "摘要": d.get("abstract", ""),
            "原文关键词": d.get("keywords", ""),
            "来源链接": d.get("source_url", ""),
            "数据来源类型": d.get("source_type", ""),
            "来源判据": d.get("source_evidence", ""),
            "开放类型与使用条件": d.get("open_type", ""),
            "时间分期标签": p_names,
            "空间分区标签": r_names,
            "主题分类标签": t_names,
            "文化谱系标签": l_names,
            "标签代码明细": code_detail,
            "CLC分类号": d.get("clc", ""),
            "汉语主题词": d.get("subject_terms", ""),
            "著录人": d.get("recorder", ""),
            "著录日期": d.get("record_date", ""),
            "审核状态": d.get("review_status", ""),
            "原始PDF文件名": d.get("pdf_name", ""),
            "原始PDF存储地址": d.get("pdf_path", ""),
            "正文存储标识符": d.get("body_id", ""),
            "Markdown相对路径": d.get("md_path", ""),
            "MinerU解析状态": d.get("parse_status", ""),
            "解析问题备注": d.get("parse_note", ""),
            "正文字符数": d.get("md_chars", 0),
            "正文字页数": d.get("md_pages", 0),
            "图版定位符数": d.get("md_img_refs", 0),
            "图像条目数": img_count.get(d["lit_id"], 0),
            "入库标记": "良渚组·第一阶段·已迁移 2026-10-27",
        })
    return out
