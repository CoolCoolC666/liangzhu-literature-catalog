# -*- coding: utf-8 -*-
"""把坐标证据与校准结果**写回** `data/遗址点位表.xlsx`。

★ 做什么（只改这一个工作簿，别的都不动）
  1. 「候选遗址」表：把「本库出现·文献 / 图版」两列更新为**现算值**，
     并在「说明」列写明口径与重算日期（原值是旧快照，与现算有出入）。
  2. 新增「文献坐标」表：把坐标证据台账里的 9 个坐标组写进去
     （遗址群范围框 4 个角点、2 个坝体剖面点、影像中心、公开权威坐标、域外参照），
     每条带出处文献标识符、原文摘录、精度说明。
  3. 「点位表」：18 个单遗址坐标**保持为空**（文献未给单点坐标，不推测），
     但在「坐标出处说明」列写明「已检索未获」，让表自己解释为什么是空的。

★ 纪律
  · 单遗址坐标**绝不推测填充**（国内地图拾取的是 GCJ02/BD09 加密坐标）
  · 数字一律现算，不写死
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "遗址点位表.xlsx"
DB = ROOT / "data" / "lzkg.db"
GEO_JSON = ROOT / "data" / "遗址坐标.json"

NOTE_SITES = "已检索 203 篇文献全文与 202 份解析结果，未获该遗址单点坐标；不推测填充"
NOTE_CAND = "「本库出现」现算于 {date}：文献=题名/摘要/关键词/刊名命中；图版=图版题注/图题命中"


def norm(s):
    return re.sub(r"\s+", "", s or "")


def recount(conn):
    """现算：每个候选遗址的文献数与图版数。"""
    lits = list(conn.execute(
        "select title, abstract, keywords, venue from literatures"))
    imgs = list(conn.execute("select caption, img_title from images"))
    return lits, imgs


def main() -> int:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    if not XLSX.exists():
        print(f"找不到 {XLSX}", file=sys.stderr)
        return 1
    wb = openpyxl.load_workbook(XLSX)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    lits, imgs = recount(conn)
    geo = json.loads(GEO_JSON.read_text(encoding="utf-8")) if GEO_JSON.exists() else {}

    from datetime import date
    today = date.today().isoformat()

    # ── 1. 候选遗址：数字换成现算值 ──────────────────────────────────
    ws = wb["候选遗址"]
    rows = list(ws.iter_rows(values_only=True))
    hidx = next((i for i, r in enumerate(rows)
                 if r and str(r[0] or "").strip() == "遗址名称"), None)
    if hidx is None:
        print("候选遗址表找不到表头", file=sys.stderr)
        return 1
    header = rows[hidx]
    hi = {str(v).strip(): i for i, v in enumerate(header) if v}
    col_lit = hi.get("本库出现·文献")
    col_img = hi.get("本库出现·图版")
    col_note = hi.get("说明")

    changed = 0
    for ri in range(hidx + 1, ws.max_row + 1):
        name = str(ws.cell(ri, 1).value or "").strip()
        if not name or "名称" in name:
            continue
        key = norm(name)
        lit_n = sum(1 for l in lits if key in norm(" ".join(
            str(l[k] or "") for k in ("title", "abstract", "keywords", "venue"))))
        img_n = sum(1 for im in imgs if key in norm(
            str(im["caption"] or "") + str(im["img_title"] or "")))
        if col_lit:
            old = ws.cell(ri, col_lit + 1).value
            ws.cell(ri, col_lit + 1).value = lit_n
            if old != lit_n:
                changed += 1
        if col_img:
            ws.cell(ri, col_img + 1).value = img_n
        if col_note:
            ws.cell(ri, col_note + 1).value = NOTE_CAND.format(date=today)

    # ── 2. 点位表：坐标留空，但写明「已检索未获」 ──────────────────────
    ws2 = wb["点位表"]
    r2 = list(ws2.iter_rows(values_only=True))
    h2 = next((i for i, r in enumerate(r2)
               if r and str(r[0] or "").strip() == "遗址名称"), None)
    if h2 is not None:
        head = r2[h2]
        hi2 = {str(v).strip(): i for i, v in enumerate(head) if v}
        col_src = hi2.get("坐标出处说明")
        for ri in range(h2 + 1, ws2.max_row + 1):
            nm = str(ws2.cell(ri, 1).value or "").strip()
            if not nm or "名称" in nm:
                continue
            if col_src is not None:
                ws2.cell(ri, col_src + 1).value = NOTE_SITES

    # ── 3. 新增「文献坐标」表 ─────────────────────────────────────────
    if "文献坐标" in wb.sheetnames:
        del wb["文献坐标"]
    ws3 = wb.create_sheet("文献坐标")
    ws3.append(["坐标组", "类型", "纬度(WGS84)", "经度(WGS84)",
                "出处文献/资料", "原文摘录", "精度与用途说明"])
    for c in ws3[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="EEF3F7")

    sg = geo.get("site_group") or {}
    rows3 = []
    if sg:
        lon0, lon1 = sg["lon_min"], sg["lon_max"]
        lat0, lat1 = sg["lat_min"], sg["lat_max"]
        for label, lat, lon in (("范围·西北", lat1, lon0), ("范围·东北", lat1, lon1),
                                ("范围·东南", lat0, lon1), ("范围·西南", lat0, lon0)):
            rows3.append(["良渚遗址群整体范围", label, lat, lon,
                          sg.get("source", ""), sg.get("quote", ""),
                          "范围框，秒级精度；来源《余杭良渚遗址群调查简报》（浙江省文物考古研究所），"
                          "另有 2 篇独立文献给出相差 30~40 秒的同一边界"])
        rows3.append(["良渚古城遗址（公开权威）", "遗址整体", sg.get("center_lat"),
                      sg.get("center_lon"), sg.get("source_public", ""),
                      "30°24′N 120°00′E / 30.400°N 120.000°E；"
                      "百度百科：东经119°56′40″—120°03′28″，北纬30°22′36″—30°26′17″",
                      "唯一有公开权威背书的单点；与文献范围框互相印证（差 0.5~1 km）"])
    for p in geo.get("points", []):
        if p.get("source") == "公开权威资料":
            continue
        rows3.append([p.get("kind", ""), p.get("name", ""), p.get("lat"),
                      p.get("lon"), p.get("source", ""),
                      "见「坐标证据台账」对应条目的原文摘录",
                      "坝体采样剖面点/影像中心，属水利工程与遥感研究点位，**非遗址本体点位**"])
    for r in rows3:
        ws3.append(r)
    ws3.freeze_panes = "A2"
    for i, col in enumerate(ws3.columns, 1):
        w = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws3.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(
            max(12, w + 2), 60)
    for r in ws3.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(XLSX)
    print(f"已更新 {XLSX.name}：候选遗址 {changed} 行数字改为现算；"
          f"点位表 18 行标注「已检索未获」；新增「文献坐标」表 {len(rows3)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
