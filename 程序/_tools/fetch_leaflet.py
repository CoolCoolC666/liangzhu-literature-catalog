# -*- coding: utf-8 -*-
"""下载 Leaflet 到**本地** `static/vendor/leaflet/`（不走 CDN、不断网可用）。

★ 为什么本地化
  README 明写「零外部依赖、零 CDN，断网可用」。所以 Leaflet 不能从 unpkg/jsdelivr
  运行时加载 —— 演示现场断网、或 CDN 被墙，整张交互地图就白屏。
  下载到本地后，页面只从自己的站点取，断网照样工作。

★ 只要两个文件
  `leaflet.js` + `leaflet.css`。**不要 marker 图片**：
  地图上的点全部用 `L.circleMarker` / `L.divIcon` 画（颜色可控、样式统一），
  不需要默认的 png 图标，省掉图片资源依赖。

用法：python _tools/fetch_leaflet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "lzkg" / "web" / "static" / "vendor" / "leaflet"

CDN = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/"
FILES = ["leaflet.js", "leaflet.css"]

# 许可：BSD-2-Clause，随包附带许可声明（放 vendor/leaflet/LICENSE）
LICENSE = """Leaflet 1.9.4
Copyright (c) 2010-2022, Vladimir Agafonkin
Copyright (c) 2010-2011, CloudMade
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = DEST / name
        if dest.exists() and dest.stat().st_size > 5000:
            print(f"[skip] {name} ({dest.stat().st_size//1024} KB)")
            continue
        try:
            r = requests.get(CDN + name, timeout=90)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"[ok]   {name}  {len(r.content)//1024} KB")
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            return 1
    (DEST / "LICENSE").write_text(LICENSE, encoding="utf-8")
    total = sum(p.stat().st_size for p in DEST.glob("*") if p.is_file())
    print(f"Leaflet 本地化完成 → {DEST}  合计 {total//1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
