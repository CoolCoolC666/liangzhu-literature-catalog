# -*- coding: utf-8 -*-
"""受控词表：唯一码位 + 同义归并 + 遗址受控词。

★ 为什么要这个模块
  登记表里的四维标签写成「代码 名称」复合串（`S-0802 环境考古`），但实测那些
  **代码是族系代号，不是标签代号**：

    topic/S-0802 → 18 个不同名称      region/R-0199 → 8 个不同名称
    topic/S-0801 → 11 个              topic/S-0803 → 9 个
    topic/S-0804 → 8 个

  按码归一会把「环太湖」和「文化创意」并成同一个东西、把「GIS」和「数字化保护」
  并成同一个东西。所以校准后的受控词表要给出：
    ① **每个受控词一个唯一码**（原先一码多义的 54 个词全部拆开）
    ② **同义归并**（确定等价的合并，不确定的保留）
    ③ **遗址受控词**（18 个候选遗址：分组、出现数、坐标状态）

★ 关键设计：不动 tag_id 规则
  tag_id 仍是 `dim:name`（**名称是身份**），所以
    · 图谱节点仍按名称建 —— 语义不变
    · 唯一码位只改 tags.code 与 code_reliable 两个字段
  只有「同义归并」会真的减少节点（4 组等价词合并），这是预期内的。

★ 纪律
  · 同义白名单是**人工审定的保守清单**，不靠相似度自动合并
  · 码位表落在 `data/controlled_vocab_codes.json`，**可审阅、可回改**
  · 一词多码/一码多词都不允许存在；分不出来的词 code 留空并标不可靠
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
CODES_FILE = _ROOT / "data" / "controlled_vocab_codes.json"

# ── 同义归并白名单（只收**确定等价**的词） ──────────────────────────
# 「数字化保护」「水利与环境」「吴越文化」这类**不合并** ——
# 它们是更具体或范围不同的独立概念，合了会丢信息。
SYNONYMS: dict[str, dict[str, str]] = {
    "topic": {
        "墓葬": "墓葬考古",
        "玉器": "玉器研究",
        "建筑技术": "建筑研究",
        "数字化传播": "数字传播",
    },
}

# ── 组合标签拆分 ────────────────────────────────────────────────────
# 「凌家滩文化/红山文化」是**原表的写法**（斜杠并列），不是一个概念。
# 图谱节点应当是两个文化，而不是一个带斜杠的怪名字。
SPLITS: dict[str, dict[str, list[str]]] = {
    "lineage": {
        "凌家滩文化/红山文化": ["凌家滩文化", "红山文化"],
    },
}


@lru_cache(maxsize=1)
def code_table() -> dict:
    """读受控词表码位文件 → {dim: {name: code}}。文件不存在就返回空。"""
    if not CODES_FILE.exists():
        return {}
    try:
        return json.loads(CODES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def synonym(dim: str, name: str) -> str:
    """同义归并：返回归并后的规范名。"""
    return SYNONYMS.get(dim, {}).get(name, name)


def split(dim: str, name: str) -> list[str]:
    """组合标签拆分 → 多个规范名；不需拆分则返回 [name]。"""
    parts = SPLITS.get(dim, {}).get(name)
    return list(parts) if parts else [name]


def canonical(dim: str, name: str) -> list[str]:
    """规范名列表 = 拆分 → 逐个再过同义归并。"""
    out = []
    for n in split(dim, name):
        c = synonym(dim, n)
        if c not in out:
            out.append(c)
    return out


def code_for(dim: str, name: str) -> str:
    """该受控词的唯一码；没有就返回空串。"""
    return (code_table().get(dim) or {}).get(name, "")


def is_unique_coded(dim: str, name: str) -> bool:
    """这个词在受控词表里有没有唯一码（没有 → 不可按码聚合）。"""
    return bool(code_for(dim, name))
