# -*- coding: utf-8 -*-
"""AI 提供商适配层：OpenAI 兼容 / Anthropic / 本地接口。

★ 为什么配置存在**浏览器**里
--------------------------
用户的要求是「保存到浏览器，大家可以自行填写选择」——即**每人带自己的 key**，
不共用一个服务端 key。好处：
  · 费用与额度各归各人，谁用谁负责，不会有一个人把大家的额度用光
  · 换模型/换服务商不用改服务端，不用重启
  · 服务端**不落盘、不写日志**任何 key

代价与边界（必须对用户讲清）：
  · 提问时 key 会**随请求发给本机后端**再转发给模型服务商
    → 后端进程能看见它，**因此不能把「不落盘」说成「从未经过后端」**
  · 站点是「成员内部开放」，所以这条路可接受；
    真要挂到完全公开的公网，key 转发这一段就得换成别的方案
  · 前端存 localStorage → **同一台机器上的其他人**（共用电脑）能读到
    → 界面上要写明，别在公用电脑上填

三种协议
--------
| provider    | 协议 | 典型地址 | 鉴权头 |
|-------------|------|----------|--------|
| `openai`    | OpenAI Chat Completions | `https://api.openai.com/v1` | `Authorization: Bearer <key>` |
| `local`     | 同上，但**不强制要 key** | `http://127.0.0.1:11434/v1`（Ollama）/ LM Studio / one-api | 无 |
| `anthropic` | Anthropic Messages | `https://api.anthropic.com/v1` | `x-api-key: <key>` + `anthropic-version` |
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

PROVIDERS = {
    "openai": {
        "label": "OpenAI 兼容（OpenAI / DeepSeek / 通义 / Kimi / one-api 等）",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "needs_key": True,
        "key_hint": "sk-…（也支持大多数中转站的 sk- 开头）",
    },
    "anthropic": {
        "label": "Anthropic（Claude）",
        "default_base": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-5",
        "needs_key": True,
        "key_hint": "sk-ant-…",
    },
    "local": {
        "label": "本地 / 私有接口（OpenAI 兼容，key 可空）",
        "default_base": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5:7b",
        "needs_key": False,
        "key_hint": "本地接口一般留空即可",
    },
}

ANTHROPIC_VERSION = "2023-06-01"


def normalize(cfg: dict | None) -> dict:
    """把前端传来的（或服务端的）配置规整成统一形状。"""
    cfg = dict(cfg or {})
    p = str(cfg.get("provider") or "openai").strip().lower()
    if p not in PROVIDERS:
        p = "openai"
    base = str(cfg.get("api_base") or "").strip().rstrip("/")
    if not base:
        base = PROVIDERS[p]["default_base"]
    model = str(cfg.get("model") or "").strip() or PROVIDERS[p]["default_model"]
    key = str(cfg.get("api_key") or "").strip()
    return {"provider": p, "api_base": base, "model": model, "api_key": key,
            "timeout": int(cfg.get("timeout") or 90)}


def is_ready(cfg: dict) -> bool:
    """配置够不够发一次请求。"""
    spec = PROVIDERS.get(cfg.get("provider") or "", {})
    if not spec:
        return False
    if spec.get("needs_key") and not cfg.get("api_key"):
        return False
    return bool(cfg.get("api_base") and cfg.get("model"))


def _scrub(text: str) -> str:
    """把可能出现在错误信息里的 key 打掉。"""
    import re
    t = str(text or "")
    t = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", t)
    t = re.sub(r"sk-ant-[A-Za-z0-9_\-]{6,}", "sk-ant-***", t)
    return t[:400]


def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_llm(cfg: dict, prompt: str, max_tokens: int = 1200) -> str:
    """按协议调一次模型，返回文本。**异常信息里不含 key。**"""
    p = cfg["provider"]
    base = cfg["api_base"]
    model = cfg["model"]
    key = cfg["api_key"]
    timeout = int(cfg.get("timeout") or 90)

    if p == "anthropic":
        data = _post(
            f"{base}/messages",
            {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
             "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
            timeout)
        blocks = data.get("content") or []
        text = "".join(b.get("text") or "" for b in blocks if isinstance(b, dict))
        if not text.strip():
            raise RuntimeError("模型返回了空内容（content 里没有 text 块）")
        return text.strip()

    # openai / local：同一套 Chat Completions
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = _post(
        f"{base}/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}],
         "temperature": 0.2, "max_tokens": max_tokens},
        headers, timeout)
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except Exception:
        raise RuntimeError(f"返回结构不像 Chat Completions：{_scrub(json.dumps(data)[:200])}")
    if not str(text).strip():
        raise RuntimeError("模型返回了空内容")
    return str(text).strip()


def test_connection(cfg: dict) -> dict:
    """测一次最小调用，验证 base / key / model 三样是不是都对。"""
    import time
    cfg = normalize(cfg)
    if not cfg["api_base"]:
        return {"ok": False, "message": "还没填接口地址"}
    if not cfg["model"]:
        return {"ok": False, "message": "还没填模型名"}
    if PROVIDERS[cfg["provider"]]["needs_key"] and not cfg["api_key"]:
        return {"ok": False, "message": "这个协议需要 API key"}
    t0 = time.time()
    try:
        text = call_llm(cfg, "只回复两个字：正常", max_tokens=16)
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
                "api_base": cfg["api_base"],
                "latency_ms": int((time.time() - t0) * 1000),
                "message": f"连通了，模型回了：{text[:40]}"}
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        hint = {
            400: "请求被拒：地址或参数不对（本地接口要带 /v1 结尾）",
            401: "鉴权失败：key 不对，或没带对头",
            403: "被拒绝：key 没权限，或该地址不允许此模型",
            404: "路径不对：OpenAI 兼容地址一般以 /v1 结尾",
            429: "额度或频率限制",
            500: "服务商内部错误",
            502: "网关错误：地址可能不通",
            503: "服务暂时不可用",
        }.get(code, "请求失败")
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"],
                "api_base": cfg["api_base"], "http_status": code,
                "latency_ms": int((time.time() - t0) * 1000),
                "message": f"HTTP {code}：{hint}", "detail": _scrub(detail)}
    except Exception as e:
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"],
                "api_base": cfg["api_base"],
                "latency_ms": int((time.time() - t0) * 1000),
                "message": f"连不上：{_scrub(type(e).__name__ + ' ' + str(e))}"}
