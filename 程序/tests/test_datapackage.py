# -*- coding: utf-8 -*-
"""数据包（图片 / 原文）接入的判据。

★ 这套判据盯三件事：
  ① **落位规则按内容认，不按目录名** —— 组员把 zip 解压出来的文件夹拖进去就能用；
  ② **状态数字必须等于实测真值** —— 库里登记了多少条，磁盘上真的能打开多少条；
  ③ **安全不因为「放宽路径判定」而松掉** —— `..` 段仍拒，文件名里的 `...` 不再误拒。

用法：
    python tests/test_datapackage.py --api      # 需要服务在跑
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = os.environ.get("LZKG_BASE", "http://127.0.0.1:8771")
_results: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(f"  {'OK ' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print("")
    print("-- " + title + " --")


def api(path: str):
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def _files(p: Path) -> set:
    try:
        return {e.name for e in os.scandir(p) if e.is_file()}
    except OSError:
        return set()


def test_module_shape():
    section("判据 1 - 模块与端点")
    prog = Path(__file__).resolve().parent.parent
    for rel in ("lzkg/datapkg.py", "lzkg/api_datapkg.py"):
        check("判据1 模块文件存在 " + rel, (prog / rel).is_file(), "")
    from lzkg import datapkg
    from lzkg.api_datapkg import router
    pref = [r.path for r in router.routes]
    check("判据1 端点前缀独立（/api/datapackage）",
          all(p.startswith("/api/datapackage") for p in pref) and pref,
          str(pref))
    src = (prog / "lzkg" / "datapkg.py").read_text(encoding="utf-8")
    check("判据1 落位规则按内容认（MARKER_DIRS）", "MARKER_DIRS" in src, "")


def test_placement_rule():
    section("判据 2 - 落位规则：认内容，不认目录名")
    from lzkg import datapkg
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "随便起个名字_abc123"
        for m in datapkg.MARKER_DIRS:
            (root / m).mkdir(parents=True)
        check("判据2 有那几个子目录就算源数据根（名字无关）",
              datapkg.looks_like_source_root(root), root.name)
        empty = Path(td) / "空目录"
        empty.mkdir()
        check("判据2 空目录不算源数据根", not datapkg.looks_like_source_root(empty), "")
        flat = Path(td) / "平铺"
        for m in datapkg.MARKER_DIRS:
            (flat / m).mkdir(parents=True)
        check("判据2 5 个子目录平铺在数据包目录里也认得出",
              datapkg.looks_like_source_root(flat), "")
    roots = datapkg.candidate_roots()
    ok = all(datapkg.looks_like_source_root(r) for r in roots)
    check("判据1 真实环境下 candidate_roots 里每个都是源数据根", ok,
          f"{[r.name for r in roots]}")
    # ★ 「拖进去就生效」这条链路的**真**判据：把数据包目录临时指到别处，
    #   丢一个名字随意的文件夹进去，candidate_roots 必须把它认成源数据根。
    with tempfile.TemporaryDirectory() as td2:
        pkg = Path(td2) / "数据包"
        pkg.mkdir()
        dropped = pkg / "良渚文化原始数据"
        for m in datapkg.MARKER_DIRS:
            (dropped / m).mkdir(parents=True)
        old_dir = datapkg.datapkg_dir
        try:
            datapkg.datapkg_dir = lambda: pkg
            found = datapkg.candidate_roots()
        finally:
            datapkg.datapkg_dir = old_dir
        check("判据2 拖进数据包目录的文件夹立刻被认出来（零命令零配置）",
              len(found) == 1 and found[0].name == "良渚文化原始数据",
              f"{[f.name for f in found]}")


def test_truth():
    section("判据 3 - 状态口径：等于实测真值")
    from lzkg.db import connect
    from lzkg import datapkg
    from lzkg.config import get_config
    c = connect()
    try:
        st = datapkg.status(c)
        root = get_config().source_root
        img_total = c.execute("SELECT count(*) FROM images").fetchone()[0]
        md_rows = [r[0] or "" for r in c.execute("SELECT md_path FROM literatures") if r[0]]
        pdf_rows = [r[0] or "" for r in c.execute("SELECT pdf_path FROM literatures") if r[0]]
        md_paths = [x for x in md_rows if x.lower().endswith(".md")]
        pdf_paths = [x for x in pdf_rows if x.lower().endswith(".pdf")]
    finally:
        c.close()
    items = {it["key"]: it for it in st["items"]}
    check("判据3 图版应有条数 == images 表行数",
          items["images"]["total"] == img_total == 3162,
          f'total={items["images"]["total"]} db={img_total}')
    check("判据3 原文 Markdown 应有条数 == 库里以 .md 结尾的路径数",
          items["markdown"]["total"] == len(md_paths) == 200,
          f'total={items["markdown"]["total"]} db={len(md_paths)}')
    check("判据3 原文 PDF 应有条数 == 库里以 .pdf 结尾的路径数",
          items["pdf"]["total"] == len(pdf_paths) == 195,
          f'total={items["pdf"]["total"]} db={len(pdf_paths)}')
    check("判据3 占位文字（无此文件）单独记在 skipped，不算缺",
          items["pdf"]["skipped"] == len(pdf_rows) - len(pdf_paths) == 5,
          f'skipped={items["pdf"]["skipped"]}')
    check("判据3 登记表应有 2 份最终版 xlsx",
          items["registry"]["total"] == 2, f'total={items["registry"]["total"]}')
    # 独立实现：直接 scandir 目录 + 查库，不走 datapkg 的算法
    if root is not None and Path(root).is_dir():
        r0 = Path(root)
        names_img = _files(r0 / "04_文献图像")
        names_md = _files(r0 / "03_文献全文")
        names_pdf = _files(r0 / "01_原始资料")
        with connect() as c2:
            fns = [r[0] for r in c2.execute("SELECT filename FROM images")]
        truth_img = sum(1 for f in fns if f in names_img)
        truth_md = sum(1 for x in md_paths if x.replace("\\", "/").rsplit("/", 1)[-1] in names_md)
        truth_pdf = sum(1 for x in pdf_paths if x.replace("\\", "/").rsplit("/", 1)[-1] in names_pdf)
        check("判据3 图版就绪数与独立实现相等",
              items["images"]["ready"] == truth_img,
              f'{items["images"]["ready"]} vs {truth_img}')
        check("判据3 原文 Markdown 就绪数与独立实现相等",
              items["markdown"]["ready"] == truth_md,
              f'{items["markdown"]["ready"]} vs {truth_md}')
        check("判据3 原文 PDF 就绪数与独立实现相等",
              items["pdf"]["ready"] == truth_pdf,
              f'{items["pdf"]["ready"]} vs {truth_pdf}')
        if st["installed"]:
            check("判据3 源数据在位时每一项都齐（ready == total）",
                  all(it["ready"] == it["total"] and it["ok"] for it in st["items"]),
                  str([(it["key"], it["ready"], it["total"]) for it in st["items"]]))


def test_path_safety():
    section("判据 4 - 路径安全：放宽的是误判，不是防护")
    from lzkg.rawtext import safe_resolve
    from lzkg.db import connect
    from lzkg.config import get_config
    root = get_config().source_root
    check("判据4 仍拒绝 .. 段",
          safe_resolve(root, "../etc/passwd") is None, "")
    check("判据4 仍拒绝 中间的 .. 段",
          safe_resolve(root, "03_文献全文/../../secret.md") is None, "")
    check("判据4 仍拒绝 空路径", safe_resolve(root, "") is None, "")
    c = connect()
    try:
        row = c.execute("SELECT md_path FROM literatures WHERE lit_id = ?",
                        ("LIT-000082",)).fetchone()
    finally:
        c.close()
    if row is not None and row[0]:
        check("判据4 文件名里的 ... 不再被误判成路径穿越（LIT-000082 可解析）",
              safe_resolve(root, row[0]) is not None, row[0][-28:])


def test_api():
    section("判据 5 - 状态接口")
    st, r = api("/api/datapackage/status")
    check("判据5 状态端点返回 200", st == 200, f"HTTP {st}")
    d = r.get("data", {})
    cond = (isinstance(d.get("installed"), bool) and isinstance(d.get("origin"), str)
            and isinstance(d.get("items"), list) and len(d.get("items", [])) == 4
            and isinstance(d.get("features"), list) and isinstance(d.get("note"), str))
    check("判据5 返回结构完整", cond, f"items={len(d.get('items', []))}")
    keys = [it.get("key") for it in d.get("items", [])]
    check("判据5 四项分别是 图版 / Markdown / PDF / 登记表",
          keys == ["images", "markdown", "pdf", "registry"], str(keys))
    body = json.dumps(r, ensure_ascii=False)
    from lzkg.config import PROGRAM_ROOT
    check("判据5 响应里不出现本机绝对路径",
          str(PROGRAM_ROOT) not in body and "source_root" not in body, "")


def test_frontend_and_pack():
    section("判据 6 - 前端与打包")
    prog = Path(__file__).resolve().parent.parent
    s = (prog / "lzkg" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    check("判据6 前端有数据包卡片", "id: \"datapkg\"" in s, "")
    check("判据6 前端认得子页路由", "renderDatapkgPage" in s, "")
    check("判据6 前端调的是独立端点 /api/datapackage/status",
          "/api/datapackage/status" in s, "")
    import re
    static_html = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S | re.I)
    ext = re.findall(r"(?:src|href)\s*=\s*[\"\' ]https?://", static_html)
    check("判据6 前端零外链依赖（静态 HTML 的 src/href 不指向 http(s)）", not ext,
          f"外链 {len(ext)} 处")
    bp = prog.parent / "_tools" / "build_package.py"
    btxt = bp.read_text(encoding="utf-8") if bp.is_file() else ""
    for rel in ("lzkg/datapkg.py", "lzkg/api_datapkg.py", "tests/test_datapackage.py"):
        check("判据6 打包必备清单含 " + rel, rel in btxt, "")
    check("判据6 打包有红线：数据包/ 下只许说明文件",
          "数据包/" in btxt and "怎么放这里" in btxt, "")


def main():
    print("=== 数据包接入 - 判据 ===")
    test_module_shape()
    test_placement_rule()
    test_truth()
    test_path_safety()
    if "--api" in sys.argv:
        test_api()
    test_frontend_and_pack()
    passed = sum(1 for ok, _, _ in _results if ok)
    failed = [(n, d) for ok, n, d in _results if not ok]
    print("")
    print(f"=== 结果：通过 {passed} / 失败 {len(failed)} ===")
    for n, d in failed:
        print(f"  FAIL: {n}  {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
