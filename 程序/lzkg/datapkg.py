# -*- coding: utf-8 -*-
"""数据包（图片 / 原文 PDF·Markdown / 登记表）的落位与状态。

★ 为什么单独一个模块
  「程序能跑」和「图版能看、原文能读」是**两件事**：
  对接包里带的是**库**（题录、图版元数据、全文索引），
  不带**文件本体**（PDF、图版图片、Markdown —— 机构订阅件，不进公开包）。
  组员把 1.9 GB 的数据包放进 `数据包/` 目录，图版缩略图、原文阅读、
  导入导出预检才真的能用。
  但缺文件时**全是静默降级**（占位图 / 404），人只会以为站点坏了
  → 所以要有一个**能看见**的状态：装没装、装齐没有、解锁了什么。

★ 落位规则：**只认内容，不认目录名**
  数据包 zip 解压后顶层叫「良渚文化原始数据/」，课程布局叫「04_专题研究/」，
  两者**里面都是同样 5 个子目录**。程序按「有没有这几个子目录」来认，
  两个名字都能用 —— 不用重打 1.9 GB 的包。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .config import DATA_DIR, PROGRAM_ROOT, get_config

#: 数据包目录（程序根下）：把解压出来的文件夹整个拖进来即可
DATAPKG_DIRNAME = "数据包"

#: 源数据根目录里必须出现的子目录（用来认「这就是一个数据包」）
MARKER_DIRS = ("05_登记表", "04_文献图像", "03_文献全文")

#: 状态表要报的项：key / 显示名 / 源数据里的子目录 / 依赖它的功能
ITEMS = [
    ("images", "图版图像", "04_文献图像", "图版缩略图、图版检索"),
    ("markdown", "原文 Markdown", "03_文献全文", "阅读原文（Markdown）"),
    ("pdf", "原文 PDF", "01_原始资料", "阅读原文（PDF）"),
    ("registry", "登记表 xlsx", "05_登记表", "登记表导入导出预检"),
]

#: 登记表要认的两份（其余备份件不算）
REGISTRY_FILES = ("文献基本信息登记表_修复.xlsx", "文献图像信息登记表_修复.xlsx")


def datapkg_dir() -> Path:
    """程序根下的数据包目录（不存在也不报错，只当没装）。"""
    return PROGRAM_ROOT / DATAPKG_DIRNAME


def looks_like_source_root(p: Path) -> bool:
    """这个目录是不是一个源数据根（里面有那几个子目录）。"""
    try:
        return any((p / m).is_dir() for m in MARKER_DIRS)
    except OSError:
        return False


def candidate_roots() -> list:
    """数据包目录里所有**像源数据根**的目录（按名字排序，先到先用）。"""
    d = datapkg_dir()
    out = []
    try:
        if not d.is_dir():
            return out
        if looks_like_source_root(d):        # 直接把 5 个子目录拖进 数据包/
            out.append(d)
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and looks_like_source_root(sub):
                out.append(sub)
    except OSError:
        return out
    return out


def _files_in(p: Path) -> set:
    """目录里的文件名集合（一次 scandir，别逐个 stat）。"""
    try:
        return {e.name for e in os.scandir(p) if e.is_file()}
    except OSError:
        return set()


def effective_root():
    """程序**当前实际在用**的源数据根（配置优先，其次自动发现）。

    ★ 报状态要报这个，而不是只报「数据包目录里有没有」——
      本机的源数据在 data/source/（自带副本），组员的在 数据包/ 里，
      站点用的是哪个，只有这里知道。
    """
    try:
        return get_config().source_root
    except Exception:
        return None


def origin(root) -> str:
    """这个源数据根是从哪来的（给界面说明用）。"""
    if root is None:
        return "未找到"
    try:
        r = Path(root).resolve()
    except Exception:
        return "其他位置"
    for c in candidate_roots():
        try:
            if Path(c).resolve() == r:
                return "数据包目录"
        except Exception:
            continue
    try:
        if r == (DATA_DIR / "source").resolve():
            return "程序自带副本"
    except Exception:
        pass
    return "课程目录"


def _root_name(root) -> str:
    """只给**目录名**，不给绝对路径（响应里不出现本机路径）。"""
    try:
        return root.name or DATAPKG_DIRNAME
    except Exception:
        return DATAPKG_DIRNAME


def _count(conn: sqlite3.Connection, sql: str, args=()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0])
    except sqlite3.Error:
        return 0


def _images_item(conn: sqlite3.Connection, root) -> dict:
    """图版：**库里登记的 3162 条，磁盘上真的在的有多少条**。"""
    total = _count(conn, "SELECT count(*) FROM images")
    ready = 0
    if root is not None:
        names = _files_in(root / "04_文献图像")
        if names:
            try:
                rows = conn.execute("SELECT filename FROM images").fetchall()
            except sqlite3.Error:
                rows = []
            ready = sum(1 for r in rows if r[0] in names)
    return {"ready": ready, "total": total}


def _mdpdf_item(conn: sqlite3.Connection, root, col: str, ext: str, subdir: str) -> dict:
    """原文：**库里登记了路径的文献里，有多少篇的文件真的在磁盘上**。

    ★ 分母只算**真路径**：登记表里有 5 篇把 pdf_path 写成占位文字
      （「（无PDF文件）」），那不是路径，不能算「缺」。
      → 这 5 篇单独记在 skipped 里，界面上要写明「另有 N 篇本来就无 PDF」。
    """
    sql = "SELECT %s FROM literatures WHERE %s IS NOT NULL AND %s != ''" % (col, col, col)
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error:
        rows = []
    path_rows = []
    for r in rows:
        rel = (r[0] or "").replace("\\", "/")
        bn = rel.rsplit("/", 1)[-1]
        if bn.lower().endswith(ext):
            path_rows.append(bn)
    names = _files_in(root / subdir) if root is not None else set()
    ready = sum(1 for bn in path_rows if bn in names)
    return {"ready": ready, "total": len(path_rows), "skipped": len(rows) - len(path_rows)}


def _registry_item(conn: sqlite3.Connection, root) -> dict:
    """登记表：两份「最终版」xlsx 齐了没。"""
    total = len(REGISTRY_FILES)
    ready = 0
    if root is not None:
        d = root / "05_登记表" / "00-最终版"
        names = _files_in(d)
        ready = sum(1 for f in REGISTRY_FILES if f in names)
    return {"ready": ready, "total": total}


def status(conn: sqlite3.Connection, root=None) -> dict:
    """数据包状态。

    ★ 口径：**库里登记了多少条，磁盘上真的能打开多少条**。
      不写「不为空」这种判据 —— 数字必须等于实测真值。
    """
    if root is None:
        root = effective_root()
    installed = bool(root is not None and looks_like_source_root(Path(root)))
    items = []
    for key, label, subdir, feature in ITEMS:
        if key == "images":
            r = _images_item(conn, root)
        elif key == "markdown":
            r = _mdpdf_item(conn, root, "md_path", ".md", "03_文献全文")
        elif key == "pdf":
            r = _mdpdf_item(conn, root, "pdf_path", ".pdf", "01_原始资料")
        else:
            r = _registry_item(conn, root)
        ok = bool(installed and r["total"] > 0 and r["ready"] == r["total"])
        items.append({"key": key, "label": label, "dir": subdir,
                      "ready": r["ready"], "total": r["total"], "ok": ok,
                      "skipped": r.get("skipped", 0),
                      "feature": feature})
    missing = [it["label"] for it in items if not it["ok"]]
    if not installed:
        note = ("还没导入数据包：把解压出来的「良渚文化原始数据」文件夹"
                "整个拖进本程序目录下的「%s/」目录，再刷新本页。" % DATAPKG_DIRNAME)
    elif missing:
        note = "源数据在「%s」，但还没齐：%s。" % (_root_name(root), "、".join(missing))
    else:
        note = "数据包已就绪，图版与原文相关功能全部可用。"
    return {
        "installed": installed,
        "root_name": _root_name(root) if root is not None else "",
        "origin": origin(root),
        "dir": DATAPKG_DIRNAME,
        "items": items,
        "features": [{"label": it["feature"], "ok": it["ok"]} for it in items],
        "missing": missing,
        "note": note,
    }
