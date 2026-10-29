# -*- coding: utf-8 -*-
"""生成对接用的「登记表填写模板」。

模板 = 真实表头 + 一行示例 + 数据字典页。
这样别的组（或以后的数据批次）照着填就能被 `python -m lzkg migrate` 吃进去。

用法：
    python _docs/字段模板/make_templates.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
PROGRAM_ROOT = HERE.parent.parent
sys.path.insert(0, str(PROGRAM_ROOT))

from lzkg.config import get_config          # noqa: E402
from lzkg.migrate import IMG_COLS, LIT_COLS  # noqa: E402

HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
NOTE_FILL = PatternFill("solid", fgColor="FFF6E0")

# 数据字典：列号 → (字段名, 必填, 说明, 示例)
LIT_DICT = {
    1: ("序号", "是", "本表内的顺序号，从 1 开始", "1"),
    2: ("文献标识符", "是", "全程不变的唯一标识。建议 NB-LIT- 六位流水号", "LIT-000001"),
    3: ("本组主题", "是", "本组认领的文化专题", "良渚文化"),
    4: ("文献类型代码", "是", "受控词表代码＋名称，用 ｜ 分隔", "DOC-AR｜学术论文"),
    5: ("题名", "是", "与原文一致，不要用 OCR 改写后的文字", "基于ArcGIS…的设计与实现"),
    6: ("作者/责任者", "否", "多人用 ； 分隔", "金鑫；董少春；王晓琪"),
    7: ("语种", "是", "受控词表代码＋名称", "zh｜中文"),
    8: ("出版/发表时间", "否", "★ 文献出版年，**不是**文化分期年", "2018"),
    9: ("出版物/载体题名", "否", "期刊名 / 学位授予单位 / 出版社", "南京大学学报(自然科学)"),
    10: ("出版者/机构", "否", "", "南京大学"),
    11: ("页码范围", "否", "", "54(1)"),
    12: ("摘要", "否", "", "本文以…"),
    13: ("原文关键词", "否", "原文里的关键词，不要自己改写", "ArcGIS；空间数据库"),
    14: ("来源/获取链接", "否", "★ DOI 或来源站点；没有就写可核实的题录", "DOI:10.13232/…"),
    15: ("原始PDF文件名", "是", "实际磁盘上的文件名", "LIT-000001_原始PDF.pdf"),
    16: ("PDF页数", "是", "整数", "13"),
    17: ("原始PDF存储地址", "是", "相对 04_专题研究 的路径", "01_原始资料/LIT-000001_原始PDF.pdf"),
    18: ("正文存储标识符", "是", "通常与文献标识符相同", "LIT-000001"),
    19: ("Markdown文件名/相对路径", "是", "★ 必须是 **Markdown** 的路径，不要指向 JSON", "03_文献全文/MinerU_markdown_LIT-000001_….md"),
    20: ("MinerU解析状态", "是", "已解析 / 解析失败 等", "已解析"),
    21: ("解析问题/备注", "否", "解析异常、换源、待人工复核等，都要写清", "…"),
    22: ("开放类型/使用条件", "否", "★ 决定能不能对外公开", "开放获取(OA)"),
    23: ("时间分期标签", "是", "受控词表代码＋名称，多值用 ； 分隔", "新石器时代晚期"),
    24: ("空间分区标签", "是", "同上", "R-0101 良渚古城"),
    25: ("主题分类标签", "是", "同上", "S-0801 数据库; S-0803 GIS"),
    26: ("文化谱系标签", "是", "同上", "良渚文化"),
    27: ("CLC分类号", "否", "可选", "K878"),
    28: ("汉语主题词", "否", "可选", "良渚古城；GIS"),
    29: ("著录人", "否", "谁填的", "张三"),
    30: ("著录日期", "否", "ISO 短横线格式", "2026-10-27"),
    31: ("审核状态", "否", "受控词表", "REV-PENDING｜已审核"),
    32: ("著录备注", "否", "", "—"),
    33: ("数据来源类型", "是", "受控词表代码＋名称", "S1｜中国知网(CNKI)"),
    34: ("来源判据（痕迹）", "否", "★ 凭什么认定来源（PDF 内嵌指纹等）", "producer=TTKN / creator=ReaderEx_DIS…"),
}

IMG_DICT = {
    1: ("序号", "是", "", "1"),
    2: ("图像标识符", "是", "建议 IMG-六位流水号", "IMG-LIT-000001-004"),
    3: ("关联文献标识符", "是", "★ 必须能在文献表里找到", "LIT-000001"),
    4: ("本组主题", "是", "", "良渚文化"),
    5: ("图像文件名", "是", "实际磁盘文件名", "IMG-LIT-000001-004.jpg"),
    6: ("图像相对路径", "是", "相对 04_专题研究 的路径", "04_文献图像/IMG-LIT-000001-004.jpg"),
    7: ("图像题名", "否", "", "良渚古城遗址空间数据形态示意图"),
    8: ("图像类型代码", "是", "受控词表代码＋名称", "IMG-SCHEMATIC｜示意图"),
    9: ("图像对象类型代码", "是", "受控词表代码＋名称", "IMGOBJ-SITE｜遗址"),
    10: ("原文页码", "否", "与 PDF页码不一致时两个都保留", "166"),
    11: ("PDF页码", "否", "", "4"),
    12: ("图号/图版号", "否", "图1 / 图版三", "图1"),
    13: ("图注", "否", "★ 从 Markdown 里取，别自己改写", "图1 良渚古城遗址空间数据形态示意图"),
    14: ("AI初标描述", "否", "AI 生成的描述，**不是结论**", "图像为示意图，主要展示…"),
    15: ("AI提取对象/关键词", "否", "", "数据；分析结果"),
    16: ("AI工具/模型", "否", "★ AI 初标必须留痕", "MinerU 3.4.4"),
    17: ("AI提示词编号/版本", "否", "", "无"),
    18: ("人工复核状态", "否", "★ 判定「已复核」**只看这一列**，不要用复核日期", "REV-PASS｜通过"),
    19: ("复核人", "否", "人工签署才写；无人复核就留空", "李四"),
    20: ("复核日期", "否", "填数据核对日；**日期非空不代表已复核**", "2026-10-27"),
    21: ("时间分期标签", "否", "与文献表同格式", "新石器时代晚期"),
    22: ("空间分区标签", "否", "", "R-0101 良渚古城"),
    23: ("主题分类标签", "否", "", "S-0801 数据库; S-0803 GIS"),
    24: ("文化谱系标签", "否", "", "良渚文化"),
    25: ("存储记录标识符", "是", "回源锚点", "LIT-000001"),
    26: ("标注备注", "否", "", "MinerU自动提取；待人工复核"),
}


def build(src_xlsx: Path, out_xlsx: Path, dict_map: dict, ncols: int, title: str):
    wb_src = openpyxl.load_workbook(src_xlsx, read_only=True, data_only=True)
    ws_src = wb_src.worksheets[0]
    rows = list(ws_src.iter_rows(values_only=True))
    wb_src.close()
    head = rows[3]                      # 第 4 行是表头
    note1 = rows[0][0] or ""
    note2 = rows[1][0] or ""

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "主表"
    ws.cell(row=1, column=1, value=f"{title}（模板）").font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value=note1).fill = NOTE_FILL
    ws.cell(row=3, column=1, value=note2).fill = NOTE_FILL
    for c in range(1, ncols + 1):
        h = head[c - 1] if c - 1 < len(head) else ""
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = 22
    # 示例行
    for c in range(1, ncols + 1):
        d = dict_map.get(c)
        ws.cell(row=5, column=c, value=(d[3] if d else ""))
    ws.freeze_panes = "B5"

    # 数据字典页
    wd = wb.create_sheet("数据字典")
    wd.append(["列号", "列名", "必填", "说明", "示例值"])
    for c in range(1, ncols + 1):
        d = dict_map.get(c)
        if d:
            wd.append([c, d[0], d[1], d[2], d[3]])
    for i, w in enumerate([6, 26, 6, 62, 34], start=1):
        wd.column_dimensions[get_column_letter(i)].width = w
    for c in range(1, 6):
        wd.cell(row=1, column=c).font = Font(bold=True)
        wd.cell(row=1, column=c).fill = HEAD_FILL
    for r in wd.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wd.freeze_panes = "A2"

    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_xlsx)
    print(f"已生成 {out_xlsx.name}")


def main():
    cfg = get_config()
    build(cfg.lit_xlsx, HERE / "文献基本信息登记表_模板.xlsx", LIT_DICT, 34, "宁波地域文明探源工程（二期）文献基本信息登记表")
    build(cfg.img_xlsx, HERE / "文献图像信息登记表_模板.xlsx", IMG_DICT, 26, "宁波地域文明探源工程（二期）文献图像信息登记表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
