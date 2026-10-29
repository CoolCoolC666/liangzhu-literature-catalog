# -*- coding: utf-8 -*-
"""生成「遗址点位表.xlsx」——**空坐标模板 + 候选清单**。

★ 为什么坐标留空
-----------------
国内地图拾取的坐标是**加密坐标**（高德 GCJ02 / 百度 BD09），
而论文里的坐标是 **WGS84**。两套坐标混用会引入**几百米的系统误差**，
在考古遗址尺度（探方、灰坑、城墙段）上足以把点位画到隔壁地块。

所以这里的规矩是：
  · **坐标以文献为准**，人工从原始文献里抄 WGS84 坐标；
  · 地图只用来「看位置对不对」，不反过来补坐标；
  · 在坐标补齐之前，地图功能自动退化成「空间分区文献数」统计图。

本脚本只负责：**把候选遗址名列出来，并把本库里的出现情况算出来**。
出现情况是现算的（每次跑都会跟着数据走），坐标列永远是空的。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

# 候选遗址清单：良渚古城遗址群为核心，外围同时期遗址为参照。
# 这里只给**名称**，坐标一律留空等人工从文献里抄 WGS84。
CANDIDATES = [
    # （遗址名, 分组说明）
    ("莫角山", "良渚古城遗址群·宫殿区"),
    ("反山", "良渚古城遗址群·高等级墓地"),
    ("瑶山", "良渚古城遗址群·祭坛"),
    ("汇观山", "良渚古城遗址群·祭坛"),
    ("钟家港", "良渚古城遗址群·考古工作区"),
    ("姜家山", "良渚古城遗址群·墓地"),
    ("官井头", "良渚古城遗址群·聚落"),
    ("卞家山", "良渚古城遗址群·聚落"),
    ("凤山", "良渚古城遗址群·制玉作坊"),
    ("寺墩", "环太湖·同期遗址参照"),
    ("福泉山", "环太湖·同期遗址参照"),
    ("广富林", "环太湖·同期遗址参照"),
    ("草鞋山", "环太湖·同期遗址参照"),
    ("马桥", "环太湖·同期遗址参照"),
    ("马家浜", "崧泽—良渚过渡参照"),
    ("崧泽", "崧泽—良渚过渡参照"),
    ("河姆渡", "更早文化参照"),
    ("钱山漾", "良渚文化·地域扩展参照"),
]

COLUMNS = [
    ("遗址名称", "候选清单见「候选遗址」工作表；本表只登记坐标，不登记文献字段"),
    ("纬度(WGS84)", "十进制度，北正南负；**必须是从文献抄的 WGS84**，不是地图拾取值"),
    ("经度(WGS84)", "十进制度，东正西负；**必须是从文献抄的 WGS84**，不是地图拾取值"),
    ("坐标出处文献标识符", "填本库的 LIT-xxxxxx；填了就表示这个坐标有文献可回溯"),
    ("坐标出处说明", "如「第 3 页 图 2 经纬度标注」；写清是哪张图哪一页，便于复核"),
    ("备注", "如「与现有标注偏差 120m，待复核」"),
]

# ★ 列名必须能被后端 `statsapi.load_sites()` 认出来——它是**子串匹配**：
#   遗址名称 / 纬度 / 经度 / 来源文献|source_lit_id|文献标识符 / 备注|note
#   改列名时先看那里，不然后端读不到（表现为「候选 0 条」）。

# ── 区域代表点 ──────────────────────────────────────────────────────
# 用户要求：「先试着做**区域区块的统一坐标**，之后可以制作更加准确的」。
# ★ 只给**真正的地理区域**配代表点：
#   「文化创意」「数字化」「参考案例」是**主题分类**，「跨遗址」是**比较研究口径**，
#   「中国综合」「浙江综合」是**范围型分类**——给它们一个点就是在编造位置，
#   所以一律「不上图」，但仍列在表里（说明为什么不上图）。
# ★ 精度只到**两位小数**（约 ±1.1 km）：这是「区域代表点」的示意精度，
#   绝不能当成遗址实测坐标用。细化坐标以「点位表」工作表为准。
REGIONS = [
    # (区域名, 纬度, 经度, 是否上图, 说明)
    ("良渚古城", 30.39, 120.00, "是", "良渚古城遗址核心区（余杭区良渚街道一带）"),
    ("良渚遗址群", 30.39, 120.00, "是", "良渚古城及周边遗址群，代表点与核心区同址"),
    ("良渚全域", 30.40, 120.00, "是", "良渚街道及外围，代表点略向北移"),
    ("良渚博物院", 30.38, 120.00, "是", "良渚博物院（遗址公园内）"),
    ("环太湖", 31.20, 120.30, "是", "太湖区域中心，用作环太湖类文献的示意代表点"),
    ("姚江流域", 30.05, 121.15, "是", "余姚—姚江下游一带"),
    ("跨遗址", None, None, "否", "跨遗址比较研究口径，**不是地理范围**，不配坐标"),
    ("中国综合", None, None, "否", "全国范围综述，单点无意义，不配坐标"),
    ("浙江综合", None, None, "否", "省级范围综述，单点无意义，不配坐标"),
    ("文化创意", None, None, "否", "主题分类，非地理范围，不配坐标"),
    ("数字化", None, None, "否", "主题分类，非地理范围，不配坐标"),
    ("参考案例", None, None, "否", "外部案例，非本库地理范围，不配坐标"),
]

REGION_COLUMNS = [
    ("区域名", "与库里 tags.dim='region' 的名称一致；改名会对不上"),
    ("纬度(WGS84)", "两位小数即可（区域代表点约 ±1.1 km）；**不是遗址实测坐标**"),
    ("经度(WGS84)", "两位小数即可（区域代表点约 ±1.1 km）；**不是遗址实测坐标**"),
    ("是否上图", "是/否。主题类与范围类分区一律「否」——给它们配点等于编造位置"),
    ("坐标依据", "这一行代表点是怎么定的、精度到多少"),
    ("备注", "如「待用文献实测坐标替换」"),
]


def scan_db(db_path: Path) -> dict:
    """统计每个候选遗址在本库里的出现情况（现算，不写死）。"""
    if not db_path.exists():
        return {}
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    out: dict[str, dict] = {}
    try:
        for name, _ in CANDIDATES:
            n_lit = c.execute(
                "SELECT COUNT(*) n FROM literatures WHERE title LIKE ? OR abstract LIKE ? "
                "OR keywords LIKE ? OR venue LIKE ? OR source_evidence LIKE ?",
                (f"%{name}%", f"%{name}%", f"%{name}%", f"%{name}%", f"%{name}%")).fetchone()["n"]
            n_img = c.execute(
                "SELECT COUNT(*) n FROM images WHERE caption LIKE ? OR img_title LIKE ? "
                "OR ai_desc LIKE ?",
                (f"%{name}%", f"%{name}%", f"%{name}%")).fetchone()["n"]
            n_tag = c.execute(
                "SELECT COUNT(*) n FROM tags WHERE name = ?", (name,)).fetchone()["n"]
            out[name] = {"literatures": n_lit, "images": n_img, "tags": n_tag}
    finally:
        c.close()
    return out


def build(db_path: Path, out_path: Path) -> dict:
    stats = scan_db(db_path)
    wb = Workbook()

    # ── 工作表 1：点位表（空坐标，等人工补）──────────────────
    ws = wb.active
    ws.title = "点位表"
    ws["A1"] = "良渚文化遗址点位表（WGS84）"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))
    ws["A2"] = ("坐标以原始文献为准（WGS84 十进制度），不从地图拾取——国内地图拾取的是加密坐标，"
                "与文献坐标混用会产生几百米系统误差。坐标未填的遗址不会出现在点位图上。")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(COLUMNS))
    ws.row_dimensions[2].height = 46
    ws["A3"] = ""
    for i, (name, _help) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=4, column=i, value=name)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DCE6F1")
    r = 5
    for name, _ in CANDIDATES:
        ws.cell(row=r, column=1, value=name)
        for i in range(2, len(COLUMNS) + 1):
            ws.cell(row=r, column=i, value=None)
        r += 1
    widths = [18, 18, 18, 26, 40, 32]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A5"

    # ── 工作表 2：候选遗址（说明 + 本库出现情况，现算）──────
    ws2 = wb.create_sheet("候选遗址")
    ws2["A1"] = "候选遗址清单（名字来自领域常识，出现情况由本库现算）"
    ws2["A1"].font = Font(bold=True, size=13)
    ws2.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    ws2["A2"] = ("「本库出现」= 题名/摘要/关键词/来源刊名里提到该遗址的文献条数与提到它的图版条数。"
                 "数字每次跑都重算，不是固定值。出现次数为 0 只说明本库没提到，不等于该遗址不存在。")
    ws2["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws2.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)
    ws2.row_dimensions[2].height = 40
    hdr = ["遗址名称", "分组", "本库出现·文献", "本库出现·图版", "本库是否作为标签", "说明"]
    for i, h in enumerate(hdr, start=1):
        c = ws2.cell(row=4, column=i, value=h)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DCE6F1")
    r = 5
    for name, group in CANDIDATES:
        s = stats.get(name, {})
        ws2.cell(row=r, column=1, value=name)
        ws2.cell(row=r, column=2, value=group)
        ws2.cell(row=r, column=3, value=s.get("literatures", 0))
        ws2.cell(row=r, column=4, value=s.get("images", 0))
        ws2.cell(row=r, column=5, value="是" if s.get("tags", 0) else "否")
        ws2.cell(row=r, column=6, value="")
        r += 1
    for i, w in enumerate([16, 26, 16, 16, 18, 30], start=1):
        ws2.column_dimensions[chr(64 + i)].width = w
    ws2.freeze_panes = "A5"

    # ── 工作表 3：区域代表点（区域区块的统一坐标）────────────────
    ws3 = wb.create_sheet("区域代表点")
    ws3["A1"] = "空间分区代表点（区域区块的统一坐标）"
    ws3["A1"].font = Font(bold=True, size=13)
    ws3.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(REGION_COLUMNS))
    ws3["A2"] = ("这是**区域代表点**，只表示「这个分区的文献大概在哪个方向」，"
                 "精度到两位小数（约 ±1.1 km），**不是遗址实测坐标**。"
                 "只有真正的地理区域才配点；主题类（文化创意/数字化/参考案例）"
                 "与范围类（中国综合/浙江综合/跨遗址）分区一律「否」——"
                 "给它们配一个点就是编造位置。细分坐标以「点位表」工作表为准。")
    ws3["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws3.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(REGION_COLUMNS))
    ws3.row_dimensions[2].height = 56
    for i, (name, _h) in enumerate(REGION_COLUMNS, start=1):
        c = ws3.cell(row=4, column=i, value=name)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DCE6F1")
    r = 5
    for name, lat, lon, on_map, basis in REGIONS:
        ws3.cell(row=r, column=1, value=name)
        ws3.cell(row=r, column=2, value=lat)
        ws3.cell(row=r, column=3, value=lon)
        ws3.cell(row=r, column=4, value=on_map)
        ws3.cell(row=r, column=5, value=basis)
        ws3.cell(row=r, column=6, value="待用文献实测坐标替换" if on_map == "是" else "—")
        r += 1
    for i, w in enumerate([18, 16, 16, 12, 46, 26], start=1):
        ws3.column_dimensions[chr(64 + i)].width = w
    ws3.freeze_panes = "A5"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"out": str(out_path), "candidates": len(CANDIDATES),
            "mentioned": sum(1 for s in stats.values() if s["literatures"] or s["images"])}


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    db = root / "data" / "lzkg.db"
    out = root / "data" / "遗址点位表.xlsx"
    print(build(db, out))
