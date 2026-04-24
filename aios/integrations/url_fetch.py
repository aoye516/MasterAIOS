"""Fetch a URL and return cleaned-up plain text.

故意写得很轻 —— 不引 readability-lxml/trafilatura 这种重依赖。
对绝大多数文章/博客来说，"删 script/style/nav/footer + 取 <main>/<article>/<body>"
已经够给 LLM 摘要用了。

Used by:
- toolbox.summarize_url （用户丢链接 → 摘要 → 落 archival_memory note）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import aiohttp
from bs4 import BeautifulSoup


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    title: str | None
    text: str
    truncated: bool


_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "aside",
               "iframe", "form", "button", "svg", "header")


def _extract(html: str) -> tuple[str | None, str]:
    """Return (title, plain_text). Strips noisy tags and tries main/article first."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    for t in soup(_NOISE_TAGS):
        t.decompose()

    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text


async def fetch_text(
    url: str,
    *,
    timeout_s: float = 20.0,
    max_chars: int = 12000,
    ua: str = DEFAULT_UA,
) -> FetchedPage:
    """GET *url* and return cleaned-up plain text (truncated at *max_chars*)."""
    headers = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            status = resp.status
            final_url = str(resp.url)
            ctype = resp.headers.get("content-type", "")
            body = await resp.text(errors="replace")

    if "html" not in ctype.lower() and "<html" not in body[:500].lower():
        # Plain text / json / etc. — don't try to parse as HTML.
        text = body
        title = None
    else:
        title, text = _extract(body)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n\n[... 截断 ...]"

    return FetchedPage(
        url=url,
        final_url=final_url,
        status=status,
        title=title,
        text=text,
        truncated=truncated,
    )
