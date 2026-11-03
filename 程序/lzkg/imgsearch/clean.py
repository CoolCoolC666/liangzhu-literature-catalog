# -*- coding: utf-8 -*-
"""图版文本清洗：图注中英分列 + AI 描述质量标记 + 标签文本拼接。

★ 为什么要清洗（图注的真实形态，2026-11-03 实测）
  库里的图注是**中英混排 + 全角字符**，例如：
    「图１ 良渚古城遗址空间数据形态示意图；Ｆｉｇ．１ Ｄｉａｇｒａｍ ｏｆ ｔｈｅ ｓｐａｔｉａｌ ｄａｔａ…���
  - 全角字符（１２３；．ＡＢＣ）会让「子串匹配」几乎全废；
  - 中英挤在一段里，索引进去以后中文查询命中的是英文串；
  → 所以先 **NFKC 归一**（全角→半角、全角空格→空格），再**中英分列**。

★ 清洗只做「分列与归一」，**不改写、不纠错、不补充**：
  源数据（登记表）是原件，索引是派生物。
"""
from __future__ import annotations

import re
import unicodedata

#: AI 描述里的**模板兜底句**标记（实测 273 条）。只做标记，不改内容。
GENERIC_AI_MARK = "具体视觉细节以原图为准"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ZH_PREFIX = re.compile(r"^\s*图\s*[0-9０-９一二三四五六七八九十]+\s*[．.、:：]?\s*")
_EN_PREFIX = re.compile(r"^\s*Fig\.?\s*[0-9０-９]+\s*[．.、:：]?\s*", re.I)
_SPLIT_SEP = re.compile(r"[;；]")
_OBJ_SEP = re.compile(r"[;；,，、]+")
_WS = re.compile(r"\s+")


def nfkc(s: str) -> str:
    """全角→半角、兼容字符归一（NFKC）。"""
    if not s:
        return ""
    return unicodedata.normalize("NFKC", str(s))


def split_caption(caption: str):
    """把图注拆成（中文图题, 英文图题）。

    规则：
      ① NFKC 归一（全角字符先转半角）；
      ② 按分号分段；**段内含中日韩字符即归中文侧**，否则归英文侧；
      ③ 中文段去掉「图N」前缀（剥完没剩中文 = 只有图号，图题留空）；英文段去掉「Fig.N」前缀并转小写；
      ④ 两边都去掉首尾空白与重复空白。

    ★ 只有图号、没有图题的行（「图3」「图五六」）→ 中英文图题**都留空**：
      图号另有 fig_no 列，源数据也还在，不需要在这里兜一份。
    """
    s = nfkc(caption)
    if not s.strip():
        return "", ""
    zh_parts = []
    en_parts = []
    for seg in _SPLIT_SEP.split(s):
        seg = seg.strip()
        if not seg:
            continue
        if _CJK_RE.search(seg):
            # ★ 分列判据 = **段里有没有中日韩字符**（不是有没有 Fig 标记）。
            #   中文侧剥掉「图N」前缀；剥完还剩中文才算「图题」——
            #   像「图3」「图五六」这种**只有图号没有图题**的行要留空，
            #   否则「中文图题解析率」会虚高（图号另有 fig_no 列存着）。
            t = _ZH_PREFIX.sub("", seg).strip()
            if t and _CJK_RE.search(t):
                zh_parts.append(t)
        else:
            # 纯英文段：剥掉「Fig.N」前缀并转小写。
            t = _EN_PREFIX.sub("", seg).strip().lower()
            if t:
                en_parts.append(t)
    return " ".join(zh_parts).strip(), " ".join(en_parts).strip()


def clean_ai_objects(s: str) -> str:
    """AI 物体词：多种分隔符统一成空格。"""
    s = nfkc(s)
    if not s.strip():
        return ""
    parts = [p.strip() for p in _OBJ_SEP.split(s) if p.strip()]
    return " ".join(parts)


def is_generic_ai(desc: str) -> bool:
    """这条 AI 描述是不是**模板兜底句**（实测 273 条）。"""
    return GENERIC_AI_MARK in nfkc(desc or "")


def clean_fig_no(s: str) -> str:
    """图号：归一 + 去掉首尾空白。图号另有一列，索引里只放归一后的纯文本。"""
    return nfkc(s or "").strip()


def join_tags(names) -> str:
    """标签名拼成一段文本（空格分隔），供文本层匹配。"""
    out = []
    for n in names or []:
        n = nfkc(str(n)).strip()
        if n:
            out.append(n)
    return " ".join(dict.fromkeys(out))
