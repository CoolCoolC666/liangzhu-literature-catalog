# -*- coding: utf-8 -*-
"""从「列定义单一事实来源」生成 xlsx 模板。

★ 关键：**模板是代码生成的，不是下载一个附件**。
否则模板与导入器会各自漂移（我们已经吃过一次亏：32 列模板 vs 34 列最终版）。

模板结构与根目录那两份空白登记表完全同构：
    行1  标题        行2  填写说明        行3  （空行）        行4  表头        行5+ 数据
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .registry import VARIANT_FULL, VARIANT_MINIMAL, columns, kind

HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
NOTE_FILL = PatternFill("solid", fgColor="FFF6E0")


def _write_registry_sheet(wb, table: str, variant: str):
    """写出登记表结构的第 1/2/4 行（标题 / 填写说明 / 表头），返回 (ws, cols)。

    ★ 模板与导出**共用这一段**：导出必须与登记表结构同构
      （行1 标题 / 行2 填写说明 / 行3 空 / 行4 表头 / 行5+ 数据）。
    """
    spec = kind(table)
    cols = columns(table, variant)
    ws = wb.active
    ws.title = spec["sheet"]

    ws.cell(row=1, column=1, value=spec["title"]).font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value=spec["note"]).fill = NOTE_FILL

    for i, c in enumerate(cols, start=1):
        cell = ws.cell(row=4, column=i, value=c["name"])
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = c.get("width") or 18
    return ws, cols


def _write_column_dict(wb, cols):
    """第二个 sheet：列说明（数据字典），让表格自我解释。"""
    wd = wb.create_sheet("列说明")
    wd.append(["列号", "列名", "是否必填", "说明", "示例值"])
    for c in cols:
        wd.append([c["no"], c["name"], "必填" if c.get("required") else "选填",
                   c.get("help", ""), c.get("example", "")])
    for i, w in enumerate([6, 26, 9, 62, 34], start=1):
        wd.column_dimensions[get_column_letter(i)].width = w
    for cell in wd[1]:
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
    for row in wd.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    wd.freeze_panes = "A2"


def build_template_bytes(table: str, variant: str = VARIANT_MINIMAL) -> bytes:
    """生成模板 xlsx 的字节流。"""
    wb = Workbook()
    ws, cols = _write_registry_sheet(wb, table, variant)

    # 第 5 行：示例值（让人一眼看出该填什么形态）
    for i, c in enumerate(cols, start=1):
        ws.cell(row=5, column=i, value=c.get("example") or "")

    ws.freeze_panes = "B5"
    _write_column_dict(wb, cols)

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def build_export_bytes(table: str, rows: list[dict], variant: str = VARIANT_FULL,
                       extra_note: str = "") -> bytes:
    """导出**全量数据**的 xlsx 字节流（登记表结构，行5+ 是数据）。

    ★ 与 `build_template_bytes` 共用 `_write_registry_sheet`：
      导出件必须能被本项目的导入器原样读回去（列名一致、表头结构一致），
      否则「导出→改→再导入」这条链就断了。

    `rows` 每行是「库字段名 → 值」的 dict（如 literatures 表的一行）。
    """
    wb = Workbook()
    ws, cols = _write_registry_sheet(wb, table, variant)

    if extra_note:
        # 说明行里追加导出口径（不新增行，结构仍是 行1/2/3/4/5+）
        ws.cell(row=2, column=1, value=f"{kind(table)['note']}｜{extra_note}")

    for r_i, row in enumerate(rows, start=5):
        for c_i, c in enumerate(cols, start=1):
            field = c.get("field")
            if field:
                val = row.get(field, "")
            elif c["name"] == "序号":
                # 图像表的「序号」没有库字段（登记表第 1 列不是数据列）
                val = r_i - 4
            else:
                val = ""
            if val is None:
                val = ""
            ws.cell(row=r_i, column=c_i, value=val)

    ws.freeze_panes = "B5"
    _write_column_dict(wb, cols)

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def template_filename(table: str, variant: str = VARIANT_MINIMAL) -> str:
    """导出用的文件名（ASCII 安全；中文名走 RFC 5987 另传）。"""
    spec = kind(table)
    base = "文献基本信息登记表" if table == "literatures" else "文献图像信息登记表"
    return f"{base}_模板_{'完整版' if variant == VARIANT_FULL else '32列版'}.xlsx"


def template_variants(table: str) -> list[str]:
    return [VARIANT_MINIMAL, VARIANT_FULL]
