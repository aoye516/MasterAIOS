"""Shared chat-completion helper.

复用 SiliconFlow 的 OpenAI-兼容接口（与 aios.embed 共享 SILICONFLOW_API_KEY）。
统一在这里出口，避免每个子代理各自写 httpx 调 LLM 的细节。

环境变量：
  SILICONFLOW_API_KEY        必填
  SILICONFLOW_BASE_URL       默认 https://api.siliconflow.cn/v1
  LLM_MODEL_CHAT             默认 Qwen/Qwen2.5-7B-Instruct（便宜够用）
"""

from __future__ import annotations

import os
from typing import Any

import aiohttp


DEFAULT_CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"


async def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    timeout_s: float = 60.0,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Single-shot chat completion. 返回 assistant 的纯文本 content。"""
    base = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    key = os.environ.get("SILICONFLOW_API_KEY")
    chosen_model = model or os.environ.get("LLM_MODEL_CHAT", DEFAULT_CHAT_MODEL)
    if not key:
        raise RuntimeError("SILICONFLOW_API_KEY not set; cannot call chat completion")

    payload: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"chat API failed {resp.status}: {body[:300]}")
            data = await resp.json()
    return data["choices"][0]["message"]["content"]
