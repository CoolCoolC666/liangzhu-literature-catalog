# -*- coding: utf-8 -*-
"""良渚文化文献目录站 · 运行配置。

配置优先级（后者覆盖前者）：
    内置默认值  →  config.json（程序目录下）  →  环境变量 LZKG_*

刻意不把绝对路径写死在代码里：项目目录可能整体搬走（作业提交、换机）。
source_root 用「相对程序根目录 + 通配定位」的方式解析，搬走也能找到。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── 路径基准 ────────────────────────────────────────────────────────────
PKG_DIR = Path(__file__).resolve().parent          # lzkg/
PROGRAM_ROOT = PKG_DIR.parent                       # 01-程序/
DATA_DIR = PROGRAM_ROOT / "data"
CONFIG_PATH = PROGRAM_ROOT / "config.json"

# 作业根：2-作业/（本程序在 03/01-程序/ 下，往上两级）
ASSIGNMENT_ROOT = PROGRAM_ROOT.parent.parent        # ...\2-作业


def _find_source_root() -> Path:
    """定位 04_专题研究。

    查找顺序（从最省事到最兜底）：
      1. 环境变量 LZKG_SOURCE_ROOT
      2. 程序所在作业根下的 02*(...)/04_专题研究      ← 课程原始布局
      3. 从程序目录往上 4 层，找任意 */04_专题研究      ← 包被解压到别处时也能找到
      4. 兜底返回一个不存在的路径，由 migrate/doctor 给出明确报错

    ★ 不写死绝对路径：整个项目目录搬走、对接包被解压到桌面，都还能找到。
    """
    env = os.environ.get("LZKG_SOURCE_ROOT")
    candidates = []
    if env:
        candidates.append(Path(env))
    if ASSIGNMENT_ROOT.exists():
        for d in sorted(ASSIGNMENT_ROOT.glob("02*")):
            candidates.append(d / "04_专题研究")
        candidates += sorted(ASSIGNMENT_ROOT.glob("*/04_专题研究"))
    # 往上若干层找（对接包被解压到别处时）
    up = PROGRAM_ROOT
    for _ in range(4):
        up = up.parent
        if not up.exists():
            break
        candidates += sorted(up.glob("*/04_专题研究"))
        candidates += sorted(up.glob("*/02*/04_专题研究"))
    for c in candidates:
        if c.is_dir():
            return c.resolve()
    return (candidates[0] if candidates else ASSIGNMENT_ROOT / "04_专题研究")


def _default_config() -> dict:
    return {
        "app": {
            "host": "127.0.0.1",     # 只对本机开放；要给同学手机访问改成 0.0.0.0
            "port": 8771,
            # 本机诊断端点（/api/health/detail 等）是否放行。
            # ★ 挂公网（Cloudflare Tunnel）时建议直接置 false，
            #   免得依赖「客户端地址判断」这种容易被绕过的东西。
            "local_detail": True,
        },
        "source": {
            "root": str(_find_source_root()),
        },
        "db": {
            "path": str(DATA_DIR / "lzkg.db"),
        },
        # ── 鉴权（P2）────────────────────────────────────────────
        # enabled=false 时**全员免登录视为 admin**，方便本机演示；
        # 置 true 才走真实账号体系（users / sessions 表）。
        "auth": {
            "enabled": False,
            "bootstrap": {
                "username": "admin",
                "password": "CHANGE-ME-AT-FIRST-LOGIN",
            },
            "session_days": 7,
        },
        # ── AI 问答（P2）─────────────────────────────────────────
        # 没配 api_key 时**降级**为本地 FTS5 检索摘要（degraded=true），
        # 不报错、不编答案。provider 走 OpenAI 兼容接口。
        "ai": {
            "enabled": True,
            "api_base": "",
            "api_key": "",
            "model": "",
            "timeout": 60,
        },
        # ── MinerU 解析（契约 v3 §2）──────────────────────────────
        # ★ 这里**不放任何密钥**：云端 token 由每个成员在**自己浏览器**里填，
        #   随请求发过来、用完就走（不落盘 / 不写日志 / 不回显）。
        #   `parse_server.remote.api_key` 只是**服务端兜底**（一般留空），
        #   优先级：请求里的 api_key > 配置 > 环境变量 MINERU_API_KEY > 匿名。
        "mineru": {
            "local_api_url": "http://127.0.0.1:8000",
            "cloud_api_url": "https://mineru.net/api",
            "doclib_tcp": "http://127.0.0.1:15980",
            "default_tier": "standard",
            "tiers": ["flash", "basic", "standard", "advanced"],
            # 解析产物的**暂存区**（相对 01-程序/data/）；批准前不写源数据目录
            "staging_dir": "解析暂存",
            "timeout": 60,
            "poll_interval": 3,
            "parse_server": {"remote": {"api_key": ""}},
        },
        # ── 遗址点位表（坐标等人工补，不编造）──────────────────────
        "sites": {
            "path": str(DATA_DIR / "遗址点位表.xlsx"),
        },
        # ── Web Dev（WebDAV 同步）总闸 ─────────────────────────────
        # ★ 各人的 WebDAV 凭据存在**各自浏览器**里，这里**只有开关**，没有凭据。
        #   `enabled=false` 时：界面不渲染入口 + 接口层一律 403（两道锁）。
        #   默认开启 —— 这是各人各配各的功能，不需要管理员逐个授权；
        #   管理员可整体关掉（比如担心公网开放代理被滥用）。
        "webdev": {
            "enabled": True,
        },
        # ── 浏览记录「存本站服务器」总闸 ───────────────────────────
        # ★ 与 Web Dev 是**两条并行**的路，不是替代关系：
        #   · Web Dev（WebDAV）→ 存到**自己的网盘**，跨设备可用，但要自己配一次；
        #   · 存服务器 → 落 `data/record/<身份>/`，**零配置**，换设备登录就能拿回来。
        #   组员嫌配 WebDAV 麻烦，这条路就是给他们的。
        # ★ 隐私边界：各人存在**各自目录**里，接口只返回调用者自己的那份，
        #   互相看不到；管理员能在界面上整体关掉（关掉后接口一律 403）。
        "browse_server": {
            "enabled": True,
        },
        # ── 原文通道（阅读原文 / 下载）────────────────────────────
        # ★ access 三档（用户 2026-10-31 选定 **open**）：
        #   open     —— 登录与否都给原文（当前默认）
        #   login    —— 必须登录
        #   oa_only  —— 只给开放获取/公开类，机构订阅件不给
        # 收紧时**改这里即可**，不用改代码。
        # ★ 不放任何密钥。
        "raw": {
            "access": "open",
        },
        # ── 公网发布（Cloudflare Access）──────────────────────────
        # ★ 这里只放**公开信息**，不放任何密钥：
        #   access_aud 是应用 AUD（公开可查），access_team_domain 用来取
        #   **公开**的验签公钥（/cdn-cgi/access/certs，不需要凭据）。
        "public": {
            "access_enabled": True,
            "access_aud": "0000000000000000000000000000000000000000000000000000000000000000",
            "access_team_domain": "yourteam.cloudflareaccess.com",
            "access_app_id": "00000000-0000-0000-0000-000000000000",
            "domain": "lztp.example.com",
            # ★ 本机诊断端点（含绝对路径）挂公网时置 false —— 总闸，
            #   不依赖「客户端地址判断」这种容易被绕过的东西。
            "local_detail": True,
        },
    }


class Config:
    """轻量配置：读 config.json，缺项用默认值补齐。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CONFIG_PATH
        self.data = _default_config()
        if self.path.exists():
            try:
                user = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in (user or {}).items():
                    if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                        self.data[k].update(v)
                    else:
                        self.data[k] = v
            except Exception:
                # 配置坏了不要让程序起不来：用默认值 + 留个备份
                try:
                    self.path.with_suffix(".json.broken").write_text(
                        self.path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
        env = os.environ.get("LZKG_PORT")
        if env and env.isdigit():
            self.data["app"]["port"] = int(env)
        env_host = os.environ.get("LZKG_HOST")
        if env_host:
            self.data["app"]["host"] = env_host
        env_src = os.environ.get("LZKG_SOURCE_ROOT")
        if env_src:
            self.data["source"]["root"] = env_src
        # 环境变量覆盖「本机演示」开关：自测时用 LZKG_AUTH_ENABLED=1 起一个
        # 真实账号的服务，不污染 config.json。
        env_auth = os.environ.get("LZKG_AUTH_ENABLED")
        if env_auth is not None:
            self.data["auth"]["enabled"] = env_auth.strip().lower() in ("1", "true", "yes", "on")
        env_ai_key = os.environ.get("LZKG_AI_API_KEY")
        if env_ai_key:
            self.data["ai"]["api_key"] = env_ai_key
        env_ai_base = os.environ.get("LZKG_AI_API_BASE")
        if env_ai_base:
            self.data["ai"]["api_base"] = env_ai_base
        env_ai_model = os.environ.get("LZKG_AI_MODEL")
        if env_ai_model:
            self.data["ai"]["model"] = env_ai_model
        # MinerU：云端 token 的**兜底**来源（成员自己在浏览器里填的那份优先）
        env_mineru_key = os.environ.get("LZKG_MINERU_API_KEY")
        if env_mineru_key:
            self.data.setdefault("mineru", {}).setdefault("parse_server", {})["remote"] = \
                {"api_key": env_mineru_key}
        env_mineru_local = os.environ.get("LZKG_MINERU_LOCAL_URL")
        if env_mineru_local:
            self.data.setdefault("mineru", {})["local_api_url"] = env_mineru_local
        # 库路径也支持环境变量覆盖：便于在**副本**上跑迁移做验证，
        # 而不碰正在被使用的正式库；也便于同一台机跑两个实例。
        env_db = os.environ.get("LZKG_DB_PATH")
        if env_db:
            self.data["db"]["path"] = env_db

    # ── 访问器 ────────────────────────────────────────────────────────
    @property
    def host(self) -> str:
        return self.data["app"]["host"]

    @property
    def port(self) -> int:
        return int(self.data["app"]["port"])

    @property
    def db_path(self) -> Path:
        return Path(self.data["db"]["path"])

    @property
    def source_root(self) -> Path:
        return Path(self.data["source"]["root"])

    @property
    def local_detail_enabled(self) -> bool:
        """本机诊断端点是否放行（挂公网时关掉）。

        ★ 优先看 `public.local_detail`：公网发布时这一段单独管，
          与 `app.local_detail` 分开，两处都能关。
        """
        pub = self.data.get("public") or {}
        if "local_detail" in pub:
            return bool(pub.get("local_detail"))
        return bool(self.data.get("app", {}).get("local_detail", True))

    @property
    def public_cfg(self) -> dict:
        """公网发布（Cloudflare Access）相关配置。**不含任何密钥。**"""
        return self.data.get("public", {}) or {}

    @property
    def webdev_enabled(self) -> bool:
        """Web Dev（WebDAV 同步）总闸。默认开启；管理员可在界面里整体关掉。"""
        wd = self.data.get("webdev") or {}
        return bool(wd.get("enabled", True))

    @property
    def browse_server_enabled(self) -> bool:
        """浏览记录「存本站服务器」总闸。

        默认开启 —— 这是各人存各人目录的功能，不需要管理员逐个授权；
        管理员可以整体关掉（关掉后接口一律 403，成员端退回「只存本浏览器」）。
        """
        bs = self.data.get("browse_server") or {}
        return bool(bs.get("enabled", True))

    # ── 派生的数据位置 ────────────────────────────────────────────────
    @property
    def lit_xlsx(self) -> Path:
        return self.source_root / "05_登记表" / "00-最终版" / "文献基本信息登记表_修复.xlsx"

    @property
    def img_xlsx(self) -> Path:
        return self.source_root / "05_登记表" / "00-最终版" / "文献图像信息登记表_修复.xlsx"

    @property
    def md_dir(self) -> Path:
        return self.source_root / "03_文献全文"

    @property
    def raw_json_dir(self) -> Path:
        return self.source_root / "02_解析结果"

    @property
    def image_dir(self) -> Path:
        return self.source_root / "04_文献图像"

    # ── P2 派生配置 ────────────────────────────────────────────────
    @property
    def auth_enabled(self) -> bool:
        return bool(self.data.get("auth", {}).get("enabled", False))

    @property
    def auth_bootstrap(self) -> dict:
        return self.data.get("auth", {}).get("bootstrap", {}) or {}

    @property
    def auth_session_days(self) -> int:
        return int(self.data.get("auth", {}).get("session_days", 7) or 7)

    @property
    def ai_cfg(self) -> dict:
        return self.data.get("ai", {}) or {}

    @property
    def sites_path(self) -> Path:
        return Path(self.data.get("sites", {}).get("path", str(DATA_DIR / "遗址点位表.xlsx")))

    # ── MinerU（契约 v3 §2）────────────────────────────────────────
    @property
    def mineru_cfg(self) -> dict:
        """MinerU 配置段（**不含密钥**的默认形状；密钥另见 `mineru_api_key`）。"""
        return self.data.get("mineru", {}) or {}

    @property
    def mineru_staging_dir(self) -> Path:
        """解析产物的**暂存区**（批准前产物只落在这里）。"""
        rel = str(self.mineru_cfg.get("staging_dir") or "解析暂存")
        p = Path(rel)
        return p if p.is_absolute() else (DATA_DIR / p)

    def mineru_api_key(self) -> str:
        """云端 token 的**服务端兜底**（成员自己在浏览器里填的那份优先）。

        优先级（契约 v3 §2.1）：配置 `parse_server.remote.api_key`
        > 环境变量 `MINERU_API_KEY` > 匿名。
        """
        cfg = self.mineru_cfg
        remote = ((cfg.get("parse_server") or {}).get("remote") or {})
        key = str(remote.get("api_key") or "").strip()
        if key:
            return key
        return str(os.environ.get("MINERU_API_KEY") or "").strip()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_source(self, root: str | Path) -> Path:
        """把数据源路径写进 config.json（对接包交接时用这个命令）。"""
        p = Path(root).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"目录不存在：{p}")
        # 允许直接指到 04_专题研究，或指到它的上一级（自动补 04_专题研究）
        if not (p / "05_登记表").is_dir():
            cand = p / "04_专题研究"
            if (cand / "05_登记表").is_dir():
                p = cand
        self.data["source"]["root"] = str(p.resolve())
        self.save()
        return p

    def summary(self) -> dict:
        return {
            "host": self.host, "port": self.port,
            "db_path": str(self.db_path),
            "source_root": str(self.source_root),
            "lit_xlsx_exists": self.lit_xlsx.exists(),
            "img_xlsx_exists": self.img_xlsx.exists(),
            "md_dir_exists": self.md_dir.exists(),
            "raw_json_dir_exists": self.raw_json_dir.exists(),
        }


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
