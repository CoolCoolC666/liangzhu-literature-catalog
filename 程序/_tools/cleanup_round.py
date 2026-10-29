"""清理本轮临时/作废产物 —— **走回收站，可恢复**，不用永久删除。

保留（长期资产）：
    _tools/scan_coords2.py            坐标精扫器
    _tools/calibrate_vocab.py         受控词表校准器
    _tools/make_coord_ledger.py       坐标证据台账生成器
    data/coord_scan_v2.json(+txt)     精扫结果
    data/controlled_vocab.json        机读词表
    data/受控词表-校准报告.xlsx(+txt) 校准报告
    data/坐标证据台账.xlsx(+txt)      证据台账

清理（临时 / 作废）：
    _tools/scan_coords.py             v1 扫描器（被 v2 取代，且噪声大）
    _tools/_probe_overpass.py         一次性连通性探针
    _tools/_dump_sheets.py            一次性 dump
    data/coord_scan.json(+readable)   v1 噪声报告
    data/overpass_probe.json          探针原始返回
    data/tags_dump.txt / vocab_readable.txt / sheet_dump.json
    data/hd.txt / xdump.txt
    TEMP 下本轮的 _*.py 探针
"""
import os
import tempfile
from pathlib import Path

import warnings
warnings.filterwarnings("ignore")

ROOT = Path(r"<你的项目路径>")
TOOLS = ROOT / "_tools"
DATA = ROOT / "data"

TARGETS = [
    TOOLS / "scan_coords.py",
    TOOLS / "_probe_overpass.py",
    TOOLS / "_dump_sheets.py",
    DATA / "coord_scan.json",
    DATA / "coord_scan_readable.txt",
    DATA / "overpass_probe.json",
    DATA / "tags_dump.txt",
    DATA / "vocab_readable.txt",
    DATA / "sheet_dump.json",
    DATA / "hd.txt",
    DATA / "xdump.txt",
]

tmp = Path(tempfile.gettempdir())
for name in ("_cover_docx.py", "_check_docx.py", "_mcp_probe.py", "_mcp_call.py",
             "_chk.py", "_c2.py", "_c3.py", "_c4.py", "_c5.py", "_c6.py",
             "_geo.py", "_hd.py", "_dump_x.py", "_vc.py", "_coord_out.txt",
             "_ls_xlsx.py", "_x1.py", "_js.py", "_db1.py", "_imgs.py",
             "_tags.py", "_fmt.py", "_coord_out.txt"):
    TARGETS.append(tmp / name)

try:
    from Microsoft.VisualBasic.FileIO import FileSystem, UIOption, RecycleOption
    VS = True
except Exception:
    VS = False

import shutil

done, fail = [], []
for p in TARGETS:
    if not p.exists():
        continue
    try:
        if VS:
            FileSystem.DeleteFile(str(p), UIOption.OnlyErrorDialogs,
                                   RecycleOption.SendToRecycleBin)
        else:
            shutil.move(str(p), str(tmp / ("_trash_" + p.name)))
        done.append(p.name)
    except Exception as e:
        fail.append(f"{p.name}: {e}")

print("RECYCLED:", len(done))
for d in done:
    print("   ", d)
if fail:
    print("FAILED:", len(fail))
    for f in fail:
        print("   ", f)
