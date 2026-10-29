# -*- coding: utf-8 -*-
"""登记表结构的**单一事实来源**的加载器。

★ 为什么要有它：列定义以前散在三个地方
（`migrate.py` 的 COLS、`make_templates.py` 的 DICT、根目录那两份 xlsx 本身），
改一处忘另两处 → 模板与导入器对不上。

现在统一由 `lzkg/schema/registry_columns.json` 定义，
**模板生成器 / 导入器 / 导出器 / 校验**全部读它 → 改一处，全部同步。
"""
from __future__ import annotations

import io
import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "registry_columns.json"

# 模板变体
VARIANT_MINIMAL = "minimal"   # 与根目录那两份空白表同构（文献 32 列 / 图像 26 列）
VARIANT_FULL = "full"         # 完整版（文献 34 列，含数据来源类型与来源判据）

_KINDS = ("literatures", "images")


def strip_star(name: str) -> str:
    """列名尾部的 `*` 是必填标记，**不是列名的一部分**。比对前统一去掉。"""
    return re.sub(r"\*+$", "", (name or "").strip())


# 「（可选）」「(选填)」之类的**选填后缀**：也不是列名的一部分
_OPTIONAL_RE = re.compile(r"[（(](可选|选填)[）)]")


def norm_name(name: str) -> str:
    """列名归一（**列定义与表头比对共用这一套**）。

    规则：
      1. 去掉尾部 `*`（必填标记）
      2. 去掉「（可选）」「(选填)」后缀
      3. 去掉所有空白
      4. 全角括号转半角

    ★ 为什么必须放在 registry（单一事实来源）里而不是各写一份：
      列定义里的键是 `strip_star(c["name"])`（仍带「（可选）」），
      而表头归一会去掉「（可选）」→ 两边对不上，
      「CLC分类号（可选）」这类列就会被误判成「表里没有的列」。
    """
    s = strip_star(str(name or ""))
    s = _OPTIONAL_RE.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s.replace("（", "(").replace("）", ")")


@lru_cache(maxsize=1)
def load() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"缺少列定义文件：{SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _kinds() -> dict:
    data = load()
    out = {}
    for k in _KINDS:
        spec = data[k]
        cols = spec["columns"]
        out[k] = {
            "sheet": spec["sheet"],
            "title": spec["title"],
            "note": spec["note"],
            "columns": cols,
            # 列名（归一后）→ 列定义
            "by_name": {norm_name(c["name"]): c for c in cols},
            # 库字段名 → 列定义
            "by_field": {c["field"]: c for c in cols if c.get("field")},
            # 必填列名（归一后）
            "required": [norm_name(c["name"]) for c in cols if c.get("required")],
            # 主键列：文献=文献标识符；图像=图像标识符
            "pk": "文献标识符" if k == "literatures" else "图像标识符",
        }
    return out


def kind(name: str) -> dict:
    k = _kinds()
    if name not in k:
        raise KeyError(f"未知表：{name}（可用：{list(k)}）")
    return k[name]


def columns(table: str, variant: str = VARIANT_MINIMAL) -> list[dict]:
    """取某张表在某变体下的列定义列表。"""
    spec = kind(table)
    cols = spec["columns"]
    if variant == VARIANT_MINIMAL:
        cols = [c for c in cols if c.get("in_template32", True)]
    elif variant != VARIANT_FULL:
        raise ValueError(f"未知变体：{variant}")
    return cols


def required_columns(table: str, variant: str = VARIANT_FULL) -> list[str]:
    """某变体下**必填**的列名（去 *）。

    ★ 缺列与缺必填要分开看：32 列模板没有「数据来源类型*」，
    但它只对**最终版**是必填 —— 对 32 列模板来说该列不存在，
    所以必填集合要按变体裁剪，否则导入 32 列模板会被误判为「缺必填」。
    """
    spec = kind(table)
    names = {norm_name(c["name"]) for c in columns(table, variant)}
    return [n for n in spec["required"] if n in names]


def pk_field(table: str) -> str:
    """主键的**库字段名**（导入时用它做 upsert 键）。"""
    spec = kind(table)
    return spec["by_name"][spec["pk"]]["field"]
