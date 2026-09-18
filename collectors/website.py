"""
官网变化检测。稳定性 B 级——对方改版就会挂。

刻意设计成「粗但耐用」：只抓两三个关键字段 + 全文 hash。
抓不到字段就降级成只报 hash 变化。

宁可信息粗一点，也不要一个每个月都要修的东西。
"""

import hashlib
import re

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

# 定价相关的关键词，中英文都覆盖
PRICE_PATTERNS = [
    r"\$\s?\d+(?:\.\d{2})?\s*(?:/\s*(?:mo|month|year|yr))?",
    r"[￥¥]\s?\d+(?:\.\d{2})?",
    r"\b(?:free|freemium)\b",
    r"免费|试用|订阅|会员",
]


def strip_html(html: str) -> str:
    """粗暴去标签。不用 BeautifulSoup 是为了少一个依赖、少一个失效点。"""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
    return strip_html(match.group(1))[:200] if match else ""


def extract_hero(html: str) -> str:
    """主标题：第一个 h1。抓不到就空着，不报错。"""
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S | re.I)
    return strip_html(match.group(1))[:300] if match else ""


def extract_pricing_signals(text: str) -> list[str]:
    """
    从正文里捞定价信号。不做精确解析——
    只要能看出「从免费变成 $9.99/月」这种变化就够了。
    """
    signals = []
    for pattern in PRICE_PATTERNS:
        signals.extend(m.group(0).strip() for m in re.finditer(pattern, text, flags=re.I))
    # 去重保序
    seen = set()
    return [s for s in signals if not (s.lower() in seen or seen.add(s.lower()))][:15]


def fetch_website(url: str, timeout: int = 20) -> dict:
    """
    抓官网首页，提取可对比的字段 + 全文 hash。

    content_hash 是核心：存整页文本太重，
    hash 变了再去看具体改了什么，这样一年的数据也就几十 MB。
    """
    if not url:
        return {}

    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    html = resp.text
    text = strip_html(html)

    return {
        "url": url,
        "title": extract_title(html),
        "hero_copy": extract_hero(html),
        "pricing_signals": extract_pricing_signals(text),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "text_length": len(text),
        # 存前 500 字，hash 变化时能直接看出改了什么，不用再抓一次
        "text_head": text[:500],
    }
