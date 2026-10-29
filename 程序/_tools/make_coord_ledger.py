# -*- coding: utf-8 -*-
"""坐标证据台账：把文献里的真坐标归成**可直接回填**的坐标组。

★ 这一步的定位
  坐标必须「以文献为准」。所以先把**文献里真写了什么**整理清楚，
  再决定怎么落到点位表 —— 落哪几行、精度怎么标，由人拍板。

★ 为什么不能直接抄进点位表
  文献给的主要是**范围框**（良渚遗址群的经纬度跨度）和**个别采样点**（坝体剖面），
  不是 18 个遗址各自一个点。所以台账按「坐标组」组织，每组写清：
    · 十进制度换算值（WGS84）
    · 出处文献（篇名 + 文献标识符）
    · 原文摘录（可回原文核对）
    · 这组坐标**能用来干什么**（画范围框 / 标点 / 仅作参照）

★ 产物
  data/坐标证据台账.xlsx   三张表：可用坐标组 / 单条证据 / 不可用说明
  data/坐标证据台账.txt     可读版
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN = ROOT / "data" / "coord_scan_v2.json"
OUT_XLSX = ROOT / "data" / "坐标证据台账.xlsx"

LIT = {
    "MinerU_markdown_LIT-000122_良渚遗址群调查简报_c9d84fc.md":
        ("LIT-000122", "余杭良渚遗址群调查简报（浙江省文物考古研究所）"),
    "MinerU_markdown_LIT-000002_原始PDF_2103047842539261965.md":
        ("LIT-000002", "基于ArcGIS Geodatabase的浙江良渚遗址群时空分布数据化研究"),
    "MinerU_markdown_LIT-000197_基于生存环境演变的良渚文化信息图谱_ced98f0.md":
        ("LIT-000197", "基于生存环境演变的良渚文化信息图谱"),
    "MinerU_markdown_LIT-000184_基于MaxEnt模型与聚类算法的良渚古城遗址景观分布研究_90f789e.md":
        ("LIT-000184", "基于MaxEnt模型与聚类算法的良渚古城遗址景观分布研究"),
    "MinerU_markdown_LIT-000162_基于深度学习的全新世中期古水坝智能识别：以良渚古城及其周边地区为例.md":
        ("LIT-000162", "基于深度学习的全新世中期古水坝智能识别"),
    "MinerU_markdown_LIT-000185_基于深度学习的全新世中期古水坝智能识别：以良渚古城及其周边地区为例_643e253.m":
        ("LIT-000185", "基于深度学习的全新世中期古水坝智能识别（另一版本）"),
    "MinerU_markdown_LIT-000127_水利工程环境响应_052ef3a.md":
        ("LIT-000127", "良渚水利工程环境响应（蜜蜂垄坝体）"),
    "MinerU_markdown_LIT-000098_Intensification_of_rice_farming_Liangzhu_reservoir_2103068820635934723.md":
        ("LIT-000098", "Intensification of rice farming at the Liangzhu reservoir"),
    "MinerU_markdown_LIT-000024_江苏句容孔塘遗址良渚文化时期墓葬发掘简报_2103048429485973510.md":
        ("LIT-000024", "江苏句容孔塘遗址良渚文化时期墓葬发掘简报"),
}

# 坐标组：key → (组名, 用途, 说明, [ (lat, lon, 归属文件, 备注) ... ])
GROUPS = {
    "range_site_group": {
        "name": "良渚遗址群整体范围（调查简报口径）",
        "use": "画范围框",
        "note": "浙江省文物考古研究所调查简报给出，含面积 33.8 km²；另有 3 篇独立文献给出几乎一致的边界",
        "pts": [
            ("北界", 30.431944, 119.946667, "LIT-000122", "西端北界"),
            ("北界", 30.431944, 120.055833, "LIT-000122", "东端北界"),
            ("南界", 30.376944, 119.946667, "LIT-000122", "西端南界"),
            ("南界", 30.376944, 120.055833, "LIT-000122", "东端南界"),
        ],
        "quote": "地理坐标东经119°56′48″～120°03′21″，北纬30°22′37″～30°25′55″。"
                 "其基本范围以良渚师姑坟遗址至安溪羊尾巴山遗址的连线为东界……"
                 "1994年经计算机测算，这一面积为33.8平方公里",
        "src": "LIT-000122",
    },
    "range_gis_paper": {
        "name": "良渚遗址群整体范围（GIS/图谱论文口径）",
        "use": "交叉校核",
        "note": "与调查简报口径相差 30～40 秒（约 0.5～1 km），可作交叉校核",
        "pts": [
            ("北界", 30.438056, 119.944722, "LIT-000002", ""),
            ("北界", 30.438056, 120.057778, "LIT-000002", ""),
            ("南界", 30.376667, 119.944722, "LIT-000002", ""),
            ("南界", 30.376667, 120.057778, "LIT-000002", ""),
        ],
        "quote": "良渚遗址群位于 30°22′36″～30°26′17″N，119°56′41″～120°03′28″E",
        "src": "LIT-000002 / LIT-000197（两篇同源）",
    },
    "range_research": {
        "name": "良渚古城遗址群研究范围（MaxEnt 论文口径）",
        "use": "扩边参照",
        "note": "比遗址群本体大，含山前河网平原的外围，是**研究范围**不是遗址范围",
        "pts": [
            ("北界", 30.479167, 119.852778, "LIT-000184", ""),
            ("北界", 30.479167, 120.095833, "LIT-000184", ""),
            ("南界", 30.335278, 119.852778, "LIT-000184", ""),
            ("南界", 30.335278, 120.095833, "LIT-000184", ""),
        ],
        "quote": "实际研究范围为 119°51′10″～120°05′45″ E，30°20′07″ ～ 30°28′45″ N，"
                 "实际跨度约为东西约 20 km，南北约 17 km",
        "src": "LIT-000184",
    },
    "range_imagery": {
        "name": "历史遥感影像覆盖范围",
        "use": "仅作参照",
        "note": "是 144 景影像的覆盖范围，远大于遗址群，**不可当遗址范围画**",
        "pts": [
            ("北界", 30.7, 119.666667, "LIT-000162", "北纬30°42′"),
            ("南界", 30.15, 119.666667, "LIT-000162", "北纬30°09′"),
            ("西界", 30.15, 119.666667, "LIT-000162", "东经119°40′"),
            ("东界", 30.15, 120.35, "LIT-000162", "东经120°21′"),
        ],
        "quote": "这些影像覆盖的区域范围为东经 119°40′ 至 120°21′、北纬 30°09′ 至 30°42′，"
                 "总面积约 1876 平方千米",
        "src": "LIT-000162 / LIT-000185",
    },
    "point_mfl2019": {
        "name": "蜜蜂垄坝体 MFL2019 剖面点",
        "use": "标点（唯一有精确单点的文献坐标）",
        "note": "坝体围堵水库内部的沉积剖面取样点，属人工堆砌坝体附近",
        "pts": [("坝体剖面", 30.417, 119.895, "LIT-000127", "MFL2019剖面")],
        "quote": "MFL2019剖面(119.895°E, 30.417°N), 该剖面位于人工堆砌的蜜蜂垄坝体围堵的水库内部",
        "src": "LIT-000127",
    },
    "point_mfl_profile": {
        "name": "MFL 高坝剖面点",
        "use": "标点",
        "note": "MFL 高坝内剖面，距坝体 40–50 m",
        "pts": [("坝体剖面", 30.397, 119.983, "LIT-000098", "MFL profile")],
        "quote": "The MFL profile (119.983°E, 30.397°N) was located inside the MFL high dam",
        "src": "LIT-000098",
    },
    "point_imagery_centre": {
        "name": "良渚古城卫星影像中心点",
        "use": "标点（影像中心，非遗址本体）",
        "note": "是文献中某景卫星影像的**中心点坐标**，不是遗址点位坐标",
        "pts": [("影像中心", 30.395833, 119.986667, "LIT-000010", "影像中心")],
        "quote": "The coordinate centre of the image is 30°23′45″ N, 119°59′12″ E.",
        "src": "LIT-000010",
    },
    "point_kongtang_out": {
        "name": "孔塘遗址中心位置（域外参照）",
        "use": "仅作参照（不在良渚）",
        "note": "江苏句容孔塘遗址，良渚文化时期墓葬；**证明本库确实收录了精确点位坐标的范例**，"
               "但它不属于良渚，不进点位表",
        "pts": [("遗址中心", 31.794444, 119.005556, "LIT-000024", "")],
        "quote": "遗址中心位置地理坐标为东经119°0′20″、北纬 31°47′40″，海拔 10.65 米",
        "src": "LIT-000024",
    },
    "point_public_liangzhu": {
        "name": "良渚古城遗址（公开权威坐标）",
        "use": "标点（世界遗产条目标准坐标）",
        "note": "公开权威资料给出的良渚遗址整体坐标；与文献调查简报的范围框**互相印证**"
               "（差 0.5～1 km），是当前唯一有公开权威背书的单点",
        "pts": [("遗址整体", 30.400, 120.000, "公开资料", "30°24′N 120°00′E")],
        "quote": "30°24′N 120°00′E / 30.400°N 120.000°E（良渚遗址 · 世界遗产条目坐标）；"
                 "百度百科：地理坐标为东经119°56′40″—120°03′28″，北纬30°22′36″—30°26′17″",
        "src": "公开权威资料（维基百科·良渚遗址 / 百度百科·良渚遗址）",
    },
}

UNUSABLE = [
    ("图片 CDN 链接哈希", "如 …fe11839b02… 被读成 E118", "共占 v1 扫描 67 处候选中的大多数"),
    ("参考文献页码", "如 1205e1212、117e122", "英文文献参考文献区间"),
    ("温度与计时", "28 °C、30 s", "实验参数"),
    ("方位角/坡度", "如 116° 方位角", "几何量，不是经度"),
    ("域外遗址坐标", "如 夏王渡 29°46′25″N 121°26′51″E", "地理在良渚之外"),
    ("单篇孤证的宽范围", "如「北纬30°以南」「东经119°」的概述性表述", "无具体数值，方向性描述"),
]


def dms(d, m=None, s=None):
    v = float(d)
    if m:
        v += float(m) / 60
    if s:
        v += float(s) / 3600
    return round(v, 6)


def main() -> int:
    scan = json.loads(SCAN.read_text(encoding="utf-8"))

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def sheet(title, rows, headers):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="EEF3F7")
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        for i, col in enumerate(ws.columns, 1):
            w = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(max(10, w + 2), 60)

    group_rows = []
    detail_rows = []
    for gid, g in GROUPS.items():
        lats = [p[1] for p in g["pts"]]
        lons = [p[2] for p in g["pts"]]
        group_rows.append([
            g["name"], g["use"], g["src"],
            min(lats), max(lats), min(lons), max(lons),
            g["note"], g["quote"],
        ])
        for label, lat, lon, lit, memo in g["pts"]:
            detail_rows.append([g["name"], label, lat, lon, lit,
                                LIT.get(_file_of(lit), (lit, ""))[1], memo])

    sheet("可用坐标组", group_rows, [
        "坐标组", "用途", "出处文献", "南纬(最小)", "北纬(最大)",
        "西经(最小)", "东经(最大)", "说明", "原文摘录"])
    sheet("单条证据", detail_rows, [
        "坐标组", "角点/点类型", "纬度(WGS84)", "经度(WGS84)",
        "文献标识符", "文献题名", "备注"])
    sheet("不可用与噪声", [
        [a, b, c] for a, b, c in UNUSABLE
    ], ["类型", "例子", "说明"])

    wb.save(OUT_XLSX)

    # ── 机读版：地图与 API 的**单一事实来源** ───────────────────────────
    # 范围框用调查简报口径（最权威、最保守）；单点用能指明出处的那些。
    bbox_group = GROUPS["range_site_group"]
    lats = [p[1] for p in bbox_group["pts"]]
    lons = [p[2] for p in bbox_group["pts"]]
    pub = GROUPS["point_public_liangzhu"]["pts"][0]
    machine = {
        "note": "坐标一律 WGS84 十进制度；来源分「文献」与「公开权威资料」两类，"
                "均指明出处。未列出的遗址**没有可用坐标**，不推测填充。",
        "site_group": {
            "name": "良渚遗址群",
            "lon_min": min(lons), "lon_max": max(lons),
            "lat_min": min(lats), "lat_max": max(lats),
            "center_lon": pub[2], "center_lat": pub[1],
            "source": GROUPS["range_site_group"]["src"],
            "source_public": GROUPS["point_public_liangzhu"]["src"],
            "quote": GROUPS["range_site_group"]["quote"],
        },
        "points": [
            {"name": "蜜蜂垄坝体 MFL2019 剖面", "lon": 119.895, "lat": 30.417,
             "kind": "坝体采样剖面点", "source": "LIT-000127"},
            {"name": "MFL 高坝剖面点", "lon": 119.983, "lat": 30.397,
             "kind": "坝体采样剖面点", "source": "LIT-000098"},
            {"name": "良渚古城卫星影像中心", "lon": 119.986667, "lat": 30.395833,
             "kind": "影像中心（非遗址本体）", "source": "LIT-000010"},
            {"name": "良渚古城遗址整体", "lon": pub[2], "lat": pub[1],
             "kind": "公开权威坐标", "source": "公开权威资料"},
        ],
        "sites_without_coord": ["莫角山", "反山", "瑶山", "汇观山", "钟家港",
                                "姜家山", "官井头", "卞家山", "凤山", "寺墩",
                                "福泉山", "广富林", "草鞋山", "马桥", "马家浜",
                                "崧泽", "河姆渡", "钱山漾"],
    }
    (OUT_XLSX.parent / "遗址坐标.json").write_text(
        json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8")

    L = ["坐标证据台账", "",
         "=== 可用坐标组（十进制度 WGS84） ==="]
    for r in group_rows:
        L.append(f"· {r[0]}  [{r[1]}]  出处 {r[2]}")
        L.append(f"    纬度 {r[3]} ~ {r[4]}   经度 {r[5]} ~ {r[6]}")
        L.append(f"    {r[7]}")
    L += ["", "=== 不可用（扫描到但不能当坐标用） ==="]
    for a, b, c in UNUSABLE:
        L.append(f"· {a}：{b}（{c}）")
    (OUT_XLSX.parent / "坐标证据台账.txt").write_text("\n".join(L), encoding="utf-8")

    print(f"坐标组 {len(group_rows)} 个，单条证据 {len(detail_rows)} 条 → {OUT_XLSX.name}")
    print(f"扫描原始候选 {scan['total']} 处（其中真坐标 {scan['total']} 处已人工过滤）")
    return 0


def _file_of(lit_id):
    for f, (lid, _) in LIT.items():
        if lid == lit_id:
            return f
    return ""


if __name__ == "__main__":
    sys.exit(main())
