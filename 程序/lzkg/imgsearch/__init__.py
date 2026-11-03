# -*- coding: utf-8 -*-
"""图像检索 E2 —— **独立模块**。

★ 为什么独立成一个包（而不是把代码塞进 db.py / app.py）
  检索是全站最容易长歪的地方：一旦图版检索和文献检索共用同一段查询代码、
  同一个索引表，以后改一边就会弄坏另一边，数据也会互相污染。
  → 本模块自带：清洗规则（clean.py）、索引（index.py）、查询（query.py）、端点（api.py）。
  → 与文献检索**唯一的耦合点是 lit_id**（点图版 → 跳文献详情）。

★ 边界（改代码前先看清楚）
  · 索引表：img_fts（自己的表，不碰 docs_fts / docs_seg / raw_json_fts）
  · 端点前缀：/api/imgsearch/*（不复用 /api/images —— 那是「按 lit_id 取图版」）
  · 文本来源：只从库里已有字段来（caption / ai_desc / ai_objects / fig_no / 标签），
    **不重新生成描述**，不产生第二套真值。
  · 复核状态：**只显示，不影响检索**（不做过滤、不做降权、不做排序权重）。

★ 零新依赖：FTS5 是 SQLite 自带的，本模块不装任何包。
"""
from . import clean, index, query  # noqa: F401

__all__ = ["clean", "index", "query"]
