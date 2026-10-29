# -*- coding: utf-8 -*-
"""浏览记录「存本站服务器」—— 零配置的同步落地。

★ 为什么要有这条路
  Web Dev（WebDAV）要每个人自己去坚果云开「第三方应用」、生成应用密码、再填四项配置，
  对只想「浏览过什么别丢」的同学太重。这条路是**零配置**的：
  什么都不用配，记录落到 `data/record/<身份>/browse.json`，换设备登录回来就能恢复。

★ 与 WebDAV 的分工（两条**并行**，不是替代）
  · 存服务器 → 本站磁盘；站开着就能用。数据在本站，管理员能在磁盘上看到文件。
  · WebDAV   → 自己的网盘；站关了也还在。但要自己配一次。
  前端按「谁配了就用谁」的顺序选目标：配了 WebDAV 走网盘，没配就走服务器。

★ 安全边界（这是「服务端存个人数据」，必须写死）
  ① **各存各的目录**，目录名由**服务端**从身份推导，**绝不采用客户端传来的名字**
     —— 否则 `../../../` 就能读写别人的，甚至写到程序目录去。
  ② 读接口**只返回调用者自己那一份**，没有「按用户名读别人」的入口。
  ③ **只存白名单字段**（kind/id/title/year/ts）：就算客户端塞了 password、token
     之类的东西，也进不了磁盘（凭据本来就不该出现在这里）。
  ④ 单份上限 1 MB；**原子写**（先写 `.tmp` 再 `os.replace`），写坏不会毁掉上一版。
  ⑤ 每次覆盖前把旧文件留成 `browse.prev.json`，误操作还能捞回一份。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, PROGRAM_ROOT

MAX_BYTES = 1024 * 1024        # 单份记录上限 1 MB
MAX_ITEMS = 500                # 与前端 BROWSE_MAX 一致

# ★ 白名单：只有这几个字段能落盘。
KEEP_FIELDS = ("kind", "id", "title", "year", "ts")

RECORD_ROOT = DATA_DIR / "record"
FILE_NAME = "browse.json"
PREV_NAME = "browse.prev.json"


class BrowseRecError(Exception):
    """对外只暴露安全信息（不含磁盘细节之外的东西）。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slug(principal) -> str:
    """身份 → 目录名。**只由服务端推导，不接受任何客户端输入。**

    · 本机免登录的管理员（`auth.enabled=false` 时的隐式身份）→ `admin`，
      对应 `data/record/admin/`；
    · 真实登录用户 → `u<id>_<清洗后的用户名>`，带 id 是为了**重名也不串**。
    """
    implicit = bool(getattr(principal, "implicit", False))
    if implicit:
        return "admin"
    name = str(getattr(principal, "username", "") or "")
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", name)[:40].strip("._")
    if not safe:
        safe = "user"
    uid = getattr(principal, "user_id", None)
    if uid is None:
        return safe
    return f"u{int(uid)}_{safe}"


def record_dir(principal) -> Path:
    """该身份的记录目录。带一道「不许越出 record 根」的兜底检查。"""
    d = RECORD_ROOT / _slug(principal)
    root = RECORD_ROOT.resolve()
    try:
        real = d.resolve()
    except OSError:
        raise BrowseRecError("记录目录不可用")
    if real != root and root not in real.parents:
        raise BrowseRecError("记录目录越界，已拒绝")
    return d


def _rel(p: Path) -> str:
    """给界面显示用的相对路径（不暴露本机绝对路径）。"""
    try:
        return str(p.relative_to(PROGRAM_ROOT)).replace("\\", "/")
    except ValueError:
        return p.name


def _ts(item: dict) -> float:
    try:
        return float(item.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_items(raw) -> tuple[list[dict], int]:
    """按白名单清洗条目。返回 (干净条目, 丢弃条数)。"""
    if raw is None:
        return [], 0
    if not isinstance(raw, list):
        raise BrowseRecError("items 必须是数组")
    out: list[dict] = []
    dropped = 0
    for it in raw:
        if not isinstance(it, dict):
            dropped += 1
            continue
        lid = it.get("id")
        if lid is None or str(lid).strip() in ("", "null", "undefined"):
            dropped += 1
            continue
        rec: dict = {}
        for k in KEEP_FIELDS:
            v = it.get(k)
            if v is None:
                continue
            if isinstance(v, (str, int, float)):
                rec[k] = v
            else:
                dropped += 1
                continue
        if "id" not in rec:
            dropped += 1
            continue
        out.append(rec)
    return out, dropped


def save(principal, payload) -> dict:
    """把调用者自己的浏览记录写到服务器（覆盖 + 留上一版）。"""
    if not isinstance(payload, dict):
        raise BrowseRecError("请求体必须是 JSON 对象")
    items, dropped = _clean_items(payload.get("items"))
    items.sort(key=_ts)
    if len(items) > MAX_ITEMS:
        items = items[-MAX_ITEMS:]

    doc = {
        "v": 1,
        "owner": _slug(principal),
        "items": items,
        "count": len(items),
        "updated_at": str(payload.get("updated_at") or "")[:40],
        "saved_at": _now(),
    }
    data = json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise BrowseRecError(
            f"记录太大（{len(data) // 1024} KB），上限 {MAX_BYTES // 1024} KB")

    d = record_dir(principal)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BrowseRecError(f"记录目录建不出来：{type(e).__name__}") from e

    target = d / FILE_NAME
    # 覆盖前把旧版留一份（误操作还能捞回来）
    if target.exists():
        try:
            os.replace(target, d / PREV_NAME)
        except OSError:
            pass

    tmp = d / (FILE_NAME + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)          # 原子替换：不会留下写了一半的文件
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise BrowseRecError(f"写入失败：{type(e).__name__}") from e

    return {"ok": True, "count": len(items), "bytes": len(data),
            "saved_at": doc["saved_at"], "path": _rel(target),
            "dropped": dropped, "message": f"已保存到服务器（{len(items)} 条）。"}


def load(principal) -> dict:
    """读回调用者自己的那份记录。"""
    d = record_dir(principal)
    target = d / FILE_NAME
    base = {"ok": True, "path": _rel(target), "saved_at": ""}
    if not target.exists():
        return dict(base, items=[], count=0, exists=False, message="服务器上还没有你的记录。")
    try:
        if target.stat().st_size > MAX_BYTES:
            raise BrowseRecError("服务器上的记录文件异常大，已拒绝读取")
        doc = json.loads(target.read_text("utf-8"))
    except BrowseRecError:
        raise
    except (OSError, ValueError) as e:
        raise BrowseRecError(f"服务器上的记录文件读不出来：{type(e).__name__}") from e

    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        items = []
    return dict(base, items=items, count=len(items), exists=True,
                saved_at=str(doc.get("saved_at") or ""),
                updated_at=str(doc.get("updated_at") or ""),
                message=f"已从服务器恢复 {len(items)} 条。")


def status(principal) -> dict:
    """当前身份在服务器上的存盘状态（给界面显示「上次同步」）。"""
    d = record_dir(principal)
    target = d / FILE_NAME
    info = {"enabled": True, "usable": True, "dir": _rel(d),
            "exists": target.exists(), "count": 0, "bytes": 0, "saved_at": "",
            "path": _rel(target)}
    if target.exists():
        try:
            info["bytes"] = target.stat().st_size
            doc = json.loads(target.read_text("utf-8"))
            if isinstance(doc, dict):
                info["count"] = len(doc.get("items") or [])
                info["saved_at"] = str(doc.get("saved_at") or "")
        except (OSError, ValueError):
            info["usable"] = False
    return info