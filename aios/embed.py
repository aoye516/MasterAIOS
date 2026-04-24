"""Shared SiliconFlow embeddings helper. 1024-d, BAAI/bge-large-zh-v1.5 by default."""

from __future__ import annotations

import os

import aiohttp


async def embed_query(query: str, *, timeout_s: float = 30.0) -> list[float]:
    """Embed a single query string via SiliconFlow's /embeddings endpoint."""
    base = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    key = os.environ.get("SILICONFLOW_API_KEY")
    model = os.environ.get("LLM_MODEL_EMBEDDING", "BAAI/bge-large-zh-v1.5")
    if not key:
        raise RuntimeError("SILICONFLOW_API_KEY not set; cannot embed")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{base}/embeddings",
            json={"model": model, "input": [query]},
            headers={"Authorization": f"Bearer {key}"},
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"embeddings API failed {resp.status}: {body[:200]}")
            data = await resp.json()
    return data["data"][0]["embedding"]


async def embed_batch(items: list[str], *, batch_size: int = 16, timeout_s: float = 60.0) -> list[list[float]]:
    """Embed a list of strings; chunked through SiliconFlow's batch endpoint."""
    base = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    key = os.environ.get("SILICONFLOW_API_KEY")
    model = os.environ.get("LLM_MODEL_EMBEDDING", "BAAI/bge-large-zh-v1.5")
    if not key:
        raise RuntimeError("SILICONFLOW_API_KEY not set; cannot embed")

    out: list[list[float]] = []
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(items), batch_size):
            chunk = items[i : i + batch_size]
            async with session.post(
                f"{base}/embeddings",
                json={"model": model, "input": chunk},
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"embeddings API failed {resp.status}: {body[:200]}")
                data = await resp.json()
            out.extend([row["embedding"] for row in data["data"]])
    return out
