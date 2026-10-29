# -*- coding: utf-8 -*-
"""MinerU 解析后端（契约 v3 §2）。

★ 为什么**自己**实现 V1 协议客户端，而不是 `from mineru.parser.api_client import MinerUApiParser`
------------------------------------------------------------------------------
本机 MinerU 装在 **uv 工具自己的 venv**（Python 3.12）里，而本站后端跑在
**Python 3.13**。两个环境不通用；把 3.12 的 site-packages 塞进 `sys.path`
也不行（二进制 wheel 在 3.13 上会炸）。
→ 所以按官方客户端的**协议**用 `httpx` 自己实现（上传 / 提交 / 轮询 / 下载都在下面）。

★ 协议（2026-10-29 在本机 mineru 4.0.6 上实测过，形状照抄官方客户端）
------------------------------------------------------------------------------
    健康   GET  {base}/v1/health
    配额   GET  {base}/v1/usage                        （要鉴权）
    上传   POST {base}/v1/uploads  → PUT upload_url → POST /v1/uploads/{id}/complete
    提交   POST {base}/v1/parse/jobs
    轮询   GET  {base}/v1/parse/jobs/{job_id}
    下载   GET  {base}/v1/files/{file_id}/content       （zip，自包含 md/json/图片）

纪律
----
1. **key 只在这一次请求里用完就走**：不落库、不写日志、不回显；
   异常信息里的 key 一定打码（`scrub`）。
2. **本地服务没起就如实说没起**（`reachable:false`），不假装能用、不抛 500。
3. **长任务不挂在 HTTP 请求上**：解析在后台线程里跑，接口只给 `job_id`，前端轮询。
4. **产物只落暂存区**（`data/解析暂存/<job_id>/`），**批准前不写源数据目录**。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import socket
import subprocess
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from .common import now_str
from .config import get_config

TIERS = ("flash", "basic", "standard", "advanced")

DEFAULT_LOCAL_URL = "http://127.0.0.1:8000"
DEFAULT_CLOUD_URL = "https://mineru.net/api"

# 终态（不同版本措辞略有差别，都算完成）
_DONE_STATES = {"completed", "complete", "success", "succeeded", "done", "finished"}
_FAILED_STATES = {"failed", "error", "cancelled", "canceled", "rejected"}

# 图片扩展名（图版产物）
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")


# ══════════════════════════════════════════════════════════════════════
# key 打码
# ══════════════════════════════════════════════════════════════════════
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}")
_KEYISH_RE = re.compile(r"(?i)((?:api[_-]?key|token|secret|authorization)\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]{8,}")


def scrub(text: str, secret: str = "") -> str:
    """把可能出现在错误信息里的 key 打掉。

    ★ 先按**本次请求实际用的那个 key** 精确替换（保证自家 key 一定不漏），
      再补一层通用正则（Bearer 头 / JWT / `api_key=xxx` 形态）。
    """
    t = str(text or "")
    if secret:
        t = t.replace(secret, "***")
    t = _BEARER_RE.sub(r"\1***", t)
    t = _JWT_RE.sub("eyJ***", t)
    t = _KEYISH_RE.sub(r"\1***", t)
    return t[:600]


class MinerUError(RuntimeError):
    """带「错在哪一环」的解析错误。"""

    def __init__(self, stage: str, message: str, secret: str = ""):
        self.stage = stage
        self.message = scrub(message, secret)
        super().__init__(f"{stage}：{self.message}")


# ══════════════════════════════════════════════════════════════════════
# V1 协议客户端
# ══════════════════════════════════════════════════════════════════════
class MinerUClient:
    """MinerU V1 协议客户端（本地与云端**同一份实现**，只差 base_url / api_key）。"""

    def __init__(self, base_url: str, api_key: str = "", timeout: int = 60):
        self.base = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    # ── 内部小工具 ────────────────────────────────────────────────
    def _headers(self, auth: bool = True) -> dict:
        h = {"Accept": "application/json"}
        if auth and self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def _abs_url(self, url: str) -> str:
        """upload_url 可能是相对路径（以 / 开头）→ 拼上 base。"""
        if url.startswith("/"):
            return self.base + url
        return url

    def _same_origin(self, url: str) -> bool:
        try:
            from urllib.parse import urlparse
            a, b = urlparse(url), urlparse(self.base)
            return (a.scheme, a.netloc) == (b.scheme, b.netloc)
        except Exception:
            return False

    # ── 健康 ──────────────────────────────────────────────────────
    def health(self) -> dict:
        """GET {base}/v1/health（**不带 key**：只是探「地址通不通」）。"""
        with httpx.Client(timeout=8.0) as c:
            r = c.get(self._url("/v1/health"), headers=self._headers(auth=False))
        if r.status_code >= 400:
            raise MinerUError("健康检查", f"HTTP {r.status_code}：{r.text[:200]}")
        return r.json()

    def reachable(self) -> tuple[bool, str]:
        """→ (通不通, 说明)。**探不通不抛异常**（status 端点要如实报 false）。"""
        try:
            self.health()
            return True, ""
        except MinerUError as e:
            return False, e.message
        except Exception as e:
            return False, scrub(f"{type(e).__name__}: {e}")

    # ── 上传（云端；本地不需要）──────────────────────────────────
    def upload(self, filename: str, data: bytes, mime: str = "application/pdf") -> str:
        """上传文件 → file_id。"""
        digest = hashlib.sha256(data).hexdigest()
        body = {"filename": filename, "bytes": len(data), "mime_type": mime,
                "purpose": "parse", "sha256sum": digest}
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(self._url("/v1/uploads"), json=body, headers=self._headers())
        except Exception as e:
            raise MinerUError("上传", f"连不上 {self.base}：{scrub(str(e), self.api_key)}",
                              self.api_key)
        if r.status_code >= 400:
            raise MinerUError("上传", f"HTTP {r.status_code}：{scrub(r.text[:300], self.api_key)}",
                              self.api_key)
        info = r.json()
        up_id = info.get("id") or (info.get("file") or {}).get("id") or ""
        if not up_id:
            raise MinerUError("上传", f"返回里没有上传 id：{scrub(json.dumps(info)[:200], self.api_key)}",
                              self.api_key)
        if str(info.get("status") or "") == "completed":
            return (info.get("file") or {}).get("id") or up_id

        # PUT 文件字节
        upload_url = info.get("upload_url") or ""
        if not upload_url:
            raise MinerUError("上传", "返回里没有 upload_url", self.api_key)
        target = self._abs_url(upload_url)
        up_headers = dict(info.get("upload_headers") or {})
        up_headers.setdefault("Content-Type", mime)
        # ★ **同源**才带 Authorization；外部预签名地址带了 key 就等于泄露给第三方
        if self.api_key and self._same_origin(target):
            up_headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=max(self.timeout, 120)) as c:
                r2 = c.put(target, content=data, headers=up_headers)
        except Exception as e:
            raise MinerUError("上传", f"PUT 失败：{scrub(str(e), self.api_key)}", self.api_key)
        if r2.status_code >= 400:
            raise MinerUError("上传", f"PUT 返回 HTTP {r2.status_code}："
                              f"{scrub(r2.text[:300], self.api_key)}", self.api_key)

        # complete
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r3 = c.post(self._url(f"/v1/uploads/{up_id}/complete"),
                            headers=self._headers())
        except Exception as e:
            raise MinerUError("上传", f"complete 失败：{scrub(str(e), self.api_key)}", self.api_key)
        if r3.status_code >= 400:
            raise MinerUError("上传", f"complete 返回 HTTP {r3.status_code}："
                              f"{scrub(r3.text[:300], self.api_key)}", self.api_key)
        fin = r3.json()
        fid = (fin.get("file") or {}).get("id") or up_id
        # 有时候 complete 只是「提交完成」，还要轮询到 status=completed
        for _ in range(30):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    rr = c.get(self._url(f"/v1/uploads/{up_id}"), headers=self._headers())
                st = str((rr.json() or {}).get("status") or "")
            except Exception:
                break
            if st == "completed":
                break
            if st in _FAILED_STATES:
                raise MinerUError("上传", f"上传状态失败：{st}", self.api_key)
            time.sleep(1)
        return fid

    # ── 提交 / 轮询 / 下载 ────────────────────────────────────────
    def submit(self, source: dict, page_range: str = "", tier: str = "",
               ocr_mode: str = "") -> dict:
        """POST /v1/parse/jobs → 任务描述。"""
        f: dict = {"source": source}
        if page_range:
            f["page_range"] = page_range
        body = {"files": [f], "output_formats": ["zip"]}
        if tier:
            body["tier"] = tier
        if ocr_mode:
            body["ocr_mode"] = ocr_mode
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(self._url("/v1/parse/jobs"), json=body, headers=self._headers())
        except Exception as e:
            raise MinerUError("提交", f"连不上 {self.base}：{scrub(str(e), self.api_key)}", self.api_key)
        if r.status_code >= 400:
            hint = {401: "鉴权失败：token 无效或已过期",
                    403: "被拒绝：token 没权限",
                    400: "请求被拒：文件或参数不对",
                    404: "路径不对：这个地址不是 MinerU V1 API",
                    429: "配额或频率限制"}.get(r.status_code, "请求失败")
            raise MinerUError("提交", f"HTTP {r.status_code}（{hint}）："
                              f"{scrub(r.text[:300], self.api_key)}", self.api_key)
        data = r.json()
        if not data.get("job_id"):
            raise MinerUError("提交", f"返回里没有 job_id：{scrub(json.dumps(data)[:200], self.api_key)}",
                              self.api_key)
        return data

    def job(self, job_id: str) -> dict:
        """GET /v1/parse/jobs/{job_id}。"""
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(self._url(f"/v1/parse/jobs/{job_id}"), headers=self._headers())
        except Exception as e:
            raise MinerUError("轮询", f"连不上 {self.base}：{scrub(str(e), self.api_key)}", self.api_key)
        if r.status_code >= 400:
            hint = {401: "鉴权失败：token 无效或已过期",
                    403: "被拒绝：token 没权限",
                    404: "任务号不存在（可能已过期）"}.get(r.status_code, "查询失败")
            raise MinerUError("轮询", f"HTTP {r.status_code}（{hint}）："
                              f"{scrub(r.text[:300], self.api_key)}", self.api_key)
        return r.json()

    def download(self, file_id: str) -> bytes:
        """GET /v1/files/{file_id}/content → 文件字节。"""
        try:
            with httpx.Client(timeout=max(self.timeout, 120)) as c:
                r = c.get(self._url(f"/v1/files/{file_id}/content"), headers=self._headers())
        except Exception as e:
            raise MinerUError("下载", f"连不上 {self.base}：{scrub(str(e), self.api_key)}", self.api_key)
        if r.status_code >= 400:
            raise MinerUError("下载", f"HTTP {r.status_code}："
                              f"{scrub(r.text[:300], self.api_key)}", self.api_key)
        return r.content

    def usage(self) -> dict:
        """GET /v1/usage（要鉴权；失败不抛，返回 {}）。"""
        try:
            with httpx.Client(timeout=15.0) as c:
                r = c.get(self._url("/v1/usage"), headers=self._headers())
            if r.status_code >= 400:
                return {}
            return r.json()
        except Exception:
            return {}


# ══════════════════════════════════════════════════════════════════════
# 任务登记（内存 + 落盘 job.json，重启后仍能列出历史）
# ══════════════════════════════════════════════════════════════════════
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def staging_root() -> Path:
    """暂存区根目录：`01-程序/data/解析暂存/`。"""
    p = get_config().mineru_staging_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_job_id() -> str:
    """`P-YYYYMMDD-NNNN`（契约 v3 §2.3 的形状；NNNN 是当天的任务数，现算，不写死）。"""
    from datetime import datetime
    from .common import CST
    prefix = f"P-{datetime.now(CST).strftime('%Y%m%d')}-"
    n = len([d for d in staging_root().glob(prefix + "*") if d.is_dir()])
    return f"{prefix}{n + 1:04d}"


def _job_file(job_dir: Path) -> Path:
    return job_dir / "job.json"


def _save_job(job: dict) -> None:
    d = Path(job["dir"])
    d.mkdir(parents=True, exist_ok=True)
    _job_file(d).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def load_jobs_from_disk() -> int:
    """把暂存区里的 `job.json` 读回内存（服务重启后仍能列历史）。"""
    root = staging_root()
    loaded = 0
    with _JOBS_LOCK:
        for d in sorted(root.glob("P-*")):
            f = _job_file(d)
            if not d.is_dir() or not f.exists():
                continue
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            job["dir"] = str(d)
            if job.get("status") == "running":
                # 上次进程被杀 → 任务实际已中断，如实标成 failed（不假装还在跑）
                job["status"] = "failed"
                job["error"] = job.get("error") or "服务重启，解析任务中断（可重新提交）"
                job["progress"] = "已中断"
                _save_job(job)
            _JOBS[job["job_id"]] = job
            loaded += 1
    return loaded


def get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        out = dict(job)
        out["result"] = dict(job.get("result") or {})
        return out


def list_jobs(proposer: str = "", is_admin: bool = False, limit: int = 20,
               page: int = 1) -> dict:
    """最近 N 条任务。

    ★ 口径：**自己的在前**；管理员看全部。成员也能看到别人的任务条目
      （只有文件名/状态，没有内容），方便协作时互相知道进展。
    """
    if not _JOBS:
        load_jobs_from_disk()
    with _JOBS_LOCK:
        items = [dict(j) for j in _JOBS.values()]
    items.sort(key=lambda j: (j.get("created_at") or ""), reverse=True)
    if not is_admin:
        mine = [j for j in items if j.get("proposer") == proposer]
        others = [j for j in items if j.get("proposer") != proposer]
        items = mine + others
    total = len(items)
    page = max(1, int(page or 1))
    limit = max(1, min(200, int(limit or 20)))
    page_items = items[(page - 1) * limit: page * limit]
    for j in page_items:
        j.pop("dir", None)
    return {"items": page_items, "total": total, "page": page, "size": limit}


# ══════════════════════════════════════════════════════════════════════
# 状态探测
# ══════════════════════════════════════════════════════════════════════
_CLI_CACHE: dict = {"at": 0.0, "value": {}}


def _cli_info() -> dict:
    """本机 MinerU CLI：装没装、什么版本、在哪。**现查现算**，60 秒缓存。"""
    now = time.time()
    if now - _CLI_CACHE["at"] < 60 and _CLI_CACHE["value"]:
        return dict(_CLI_CACHE["value"])
    info = {"installed": False, "version": "", "path": ""}
    candidates = [Path.home() / ".local" / "bin" / "mineru.exe",
                  Path.home() / ".local" / "bin" / "mineru"]
    for c in candidates:
        if c.exists():
            info["installed"] = True
            info["path"] = str(c)
            break
    if not info["installed"]:
        import shutil
        p = shutil.which("mineru")
        if p:
            info["installed"] = True
            info["path"] = p
    if info["installed"]:
        try:
            out = subprocess.run([info["path"], "--version"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30)
            blob = (out.stdout or "") + (out.stderr or "")
            m = re.search(r"(\d+\.\d+\.\d+)", blob)
            if m:
                info["version"] = m.group(1)
            else:
                info["version"] = (blob.strip().splitlines() or [""])[0][:60]
        except Exception:
            info["version"] = ""
    _CLI_CACHE.update({"at": now, "value": dict(info)})
    return info


def _doclib_running(tcp_url: str) -> bool:
    """TCP 探一下 doclib 服务在不在（**只探活，不依赖它的内部接口**）。"""
    try:
        from urllib.parse import urlparse
        u = urlparse(tcp_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 15980
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except Exception:
        return False


def status_snapshot() -> dict:
    """`GET /api/mineru/status` 的数据。**不含任何密钥。**

    ★ `reachable` 是**真的探一下**得到的：本地打 `/v1/health`，
      云端也打 `/v1/health` 但**不带 key**（匿名探针只为知道地址通不通）。
      探不通 → `reachable:false` + 说明，**不抛 500**。
    """
    cfg = get_config()
    mc = cfg.mineru_cfg
    local_url = str(mc.get("local_api_url") or DEFAULT_LOCAL_URL).rstrip("/")
    cloud_url = str(mc.get("cloud_api_url") or DEFAULT_CLOUD_URL).rstrip("/")
    doclib_tcp = str(mc.get("doclib_tcp") or "http://127.0.0.1:15980")
    tiers = list(mc.get("tiers") or TIERS)
    default_tier = str(mc.get("default_tier") or "standard")

    local = MinerUClient(local_url, timeout=8)
    ok_local, why_local = local.reachable()
    cloud = MinerUClient(cloud_url, timeout=8)
    ok_cloud, why_cloud = cloud.reachable()

    return {
        "cli": _cli_info(),
        "local": {
            "api_url": local_url,
            "reachable": ok_local,
            "tier": default_tier,
            "note": ("本地自托管 API 可用；用 mineru-api.exe --port 8000 起"
                     if ok_local
                     else f"本地解析 API 没起（{why_local or '连不上'}）："
                          f"用 mineru-api.exe --host 127.0.0.1 --port 8000 起"
                          f"（本地来源文件还需 --allow-local-source）"),
        },
        "doclib": {
            "running": _doclib_running(doclib_tcp),
            "tcp": doclib_tcp,
            "indexed_docs": None,
            "note": "文档库服务（mineru server）；索引条数未读取（不依赖它的内部接口）",
        },
        "cloud": {
            "api_url": cloud_url,
            "reachable": ok_cloud,
            "note": ("云端地址可达；配额取决于成员自己的 token"
                     if ok_cloud
                     else f"云端地址探不通：{why_cloud or '连不上'}"),
        },
        "tiers": tiers,
        "default_tier": default_tier,
        "staging_dir": str(cfg.mineru_staging_dir),
        "note": "云端 token 只在这一次请求里用完就走：不落库、不写日志、不回显。",
    }


# ══════════════════════════════════════════════════════════════════════
# 解析产物落暂存区
# ══════════════════════════════════════════════════════════════════════
def _extract_zip(data: bytes, job_dir: Path) -> list[dict]:
    """把 zip 解到暂存区 → 产物清单。

    zip 结构（实测 4.0.6）：`markdown.md` / `middle_json.json`
    / `structured_content.json` / `model_output.json`，有图时还有 `images/…`。
    """
    artifacts: list[dict] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        raise MinerUError("落盘", f"下载回来的不是合法 zip：{scrub(str(e))}")
    used: set[str] = set()
    for info in z.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        base = PurePosixPath(name).name
        low = base.lower()
        if low.endswith(".md"):
            kind, sub = "md", "md"
        elif low.endswith(".json"):
            kind, sub = "json", "json"
        elif low.endswith(_IMAGE_EXT):
            kind, sub = "image", "images"
        else:
            kind, sub = "other", "other"
        # 同名文件（不同目录）不覆盖
        stem, ext = os.path.splitext(base)
        cand = base
        i = 1
        while cand.lower() in used:
            cand = f"{stem}_{i}{ext}"
            i += 1
        used.add(cand.lower())
        target = job_dir / sub / cand
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(name))
        artifacts.append({"kind": kind, "name": cand, "rel": f"{sub}/{cand}",
                          "bytes": int(info.file_size)})
    return artifacts


def _compute_result(job_dir: Path, artifacts: list[dict]) -> dict:
    """产物统计（**现算**：字数/页数/图数都从产物里读，不写死）。"""
    res: dict = {"chars": 0, "pages": 0, "images": 0,
                 "md_file": "", "json_file": "", "images_dir": "", "md_head": ""}
    md = next((a for a in artifacts if a["kind"] == "md"), None)
    if md:
        p = job_dir / md["rel"]
        text = p.read_text(encoding="utf-8", errors="replace")
        res["chars"] = len(text)
        res["md_file"] = md["rel"]
        res["md_head"] = text[:500]
    js = next((a for a in artifacts if a["kind"] == "json"), None)
    if js:
        res["json_file"] = js["rel"]
        try:
            data = json.loads((job_dir / js["rel"]).read_text(encoding="utf-8", errors="replace"))
            pages = data.get("pages")
            if isinstance(pages, list):
                res["pages"] = len(pages)
            if not res["pages"]:
                doc = ((data.get("metadata") or {}).get("document") or {})
                res["pages"] = int(doc.get("page_count") or 0)
        except Exception:
            pass
    imgs = [a for a in artifacts if a["kind"] == "image"]
    res["images"] = len(imgs)
    if imgs:
        res["images_dir"] = "images"
    res["artifacts"] = artifacts
    return res


# ══════════════════════════════════════════════════════════════════════
# 后台跑解析
# ══════════════════════════════════════════════════════════════════════
def _run_job(job: dict, base_url: str, api_key: str, tier: str, pages: str,
             src_path: Path) -> None:
    """后台线程：提交 → 轮询 → 下载 → 解到暂存区。"""
    job_id = job["job_id"]
    job_dir = Path(job["dir"])
    secret = api_key
    try:
        client = MinerUClient(base_url, api_key)
        if job["backend"] == "cloud":
            job["progress"] = "上传文件到云端…"
            _save_job(job)
            data = src_path.read_bytes()
            fid = client.upload(src_path.name, data)
            source = {"type": "file_id", "file_id": fid}
        else:
            job["progress"] = "提交本地解析…"
            _save_job(job)
            source = {"type": "local", "path": str(src_path)}
        resp = client.submit(source, page_range=pages, tier=tier)
        remote_job = resp.get("job_id")
        job["remote_job_id"] = remote_job
        _save_job(job)

        interval = float(get_config().mineru_cfg.get("poll_interval") or 3)
        deadline = time.time() + 60 * 60          # 最多等 1 小时
        status = ""
        while True:
            st = client.job(remote_job)
            status = str(st.get("status") or "").lower()
            prog = st.get("progress") or {}
            done = prog.get("completed")
            total = prog.get("total")
            if isinstance(done, int) and isinstance(total, int) and total:
                job["progress"] = f"已处理 {done}/{total} 个文件"
            else:
                job["progress"] = f"解析中（{status or 'running'}）"
            if status in _FAILED_STATES:
                err = ""
                for f in st.get("files") or []:
                    if f.get("error"):
                        err = str(f["error"])
                        break
                raise MinerUError("解析", f"任务失败（{status}）{err}",
                                  secret)
            if status in _DONE_STATES:
                break
            if time.time() > deadline:
                raise MinerUError("解析", "等待超时（超过 1 小时）", secret)
            _save_job(job)
            time.sleep(interval)

        # 取 zip
        zip_id = ""
        for f in st.get("files") or []:
            of = f.get("output_files") or {}
            z = (of.get("zip") or {}).get("file_id")
            if z:
                zip_id = z
                break
        if not zip_id:
            raise MinerUError("下载", "任务完成但没有 zip 产物（output_files.zip 为空）", secret)
        job["progress"] = "下载产物…"
        _save_job(job)
        blob = client.download(zip_id)
        artifacts = _extract_zip(blob, job_dir)
        result = _compute_result(job_dir, artifacts)
        job["result"] = result
        job["status"] = "done"
        job["progress"] = "完成"
        job["error"] = ""
    except MinerUError as e:
        job["status"] = "failed"
        job["error"] = f"{e.stage}：{e.message}"
        job["progress"] = "失败"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = scrub(f"{type(e).__name__}: {e}", secret)
        job["progress"] = "失败"
    finally:
        _save_job(job)


def start_parse(file_bytes: bytes, filename: str, backend: str = "local",
                api_key: str = "", tier: str = "", pages: str = "",
                proposer: str = "") -> dict:
    """起一个后台解析任务。→ 任务描述（`job_id` + `status=running`）。

    ★ **key 只在这一刻存在于内存里**：传给后台线程，用完即弃，
      不落库、不写日志、不回显（异常信息一律 `scrub` 过）。
    """
    cfg = get_config()
    mc = cfg.mineru_cfg
    backend = (backend or "local").strip().lower()
    if backend not in ("local", "cloud"):
        raise ValueError("backend 只能是 local 或 cloud")
    tiers = list(mc.get("tiers") or TIERS)
    tier = (tier or str(mc.get("default_tier") or "standard")).strip().lower()
    if tier not in tiers:
        raise ValueError(f"tier 只能是 {'/'.join(tiers)} 之一")

    if backend == "cloud":
        base_url = str(mc.get("cloud_api_url") or DEFAULT_CLOUD_URL).rstrip("/")
        # 优先级：请求里的 > 配置里的 > 环境变量 > 匿名
        key = (api_key or "").strip() or cfg.mineru_api_key()
        if not key:
            raise ValueError("云端解析需要 mineru.net token（请在页面上填，或让管理员在配置里放一份）")
    else:
        base_url = str(mc.get("local_api_url") or DEFAULT_LOCAL_URL).rstrip("/")
        key = ""

    job_id = new_job_id()
    job_dir = staging_root() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    src = job_dir / (Path(filename or "upload.pdf").name)
    src.write_bytes(file_bytes)

    job = {
        "job_id": job_id,
        "status": "running",
        "backend": backend,
        "file": src.name,
        "created_at": now_str(),
        "proposer": proposer or "local",
        "progress": "排队中",
        "error": "",
        "result": {},
        "dir": str(job_dir),
        "remote_job_id": "",
        "tier": tier,
        "pages": pages or "",
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    _save_job(job)

    # ★ 传**同一个 dict 对象**（不是副本）：后台线程要就地更新状态/进度，
    #   传副本的话 `_JOBS` 里的记录永远停在「排队中」。
    th = threading.Thread(target=_run_job,
                          args=(job, base_url, key, tier, pages or "", src),
                          daemon=True, name=f"mineru-{job_id}")
    th.start()
    return {"job_id": job_id, "status": "running", "backend": backend,
            "file": src.name, "tier": tier,
            "note": "解析在后台跑，用 GET /api/mineru/jobs/{job_id} 查进度"}


def job_view(job: dict) -> dict:
    """对外的job 视图（**去掉本机绝对路径**）。"""
    out = {k: v for k, v in job.items() if k not in ("dir",)}
    out.setdefault("result", {})
    return out
