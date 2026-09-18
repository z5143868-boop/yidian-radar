"""
官方发布源采集：newsroom / blog 的 RSS。

为什么单独做这个：新闻搜索是二手的，官方 newsroom 是一手的——
融资、合作、人事变动通常先出现在官方发布，隔一两天才被媒体转载。
早一两天知道，对判断「对方下一步要干什么」有实际价值。

实测结论（2026-09）：
  只有上市公司和有 PR 团队的公司才有正经 RSS。
  Stitch Fix newsroom ✅ 10 条
  Whering / Indyx / Style DNA / Acloset 的 blog 都返回 200 但 0 条目
    （是 SPA 页面不是 RSS，解析不出东西）

所以这个 collector 覆盖面很窄，是锦上添花，不是主力。
配置里没填 official_feed 的竞品直接跳过。
"""

from xml.etree import ElementTree

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


def fetch_official_feed(url: str, lookback_days: int = 8, timeout: int = 20) -> list[dict]:
    """
    抓官方 RSS。复用 news collector 的日期解析和窗口过滤。

    返回结构跟 news 一致，额外带 official=True，
    这样看板上能把官方发布和媒体报道区分开。
    """
    if not url:
        return []

    from .news import _parse_rss_date, _within_lookback, _text, _strip_tags

    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()

    # 返回 200 但不是 RSS 的情况很常见（SPA 页面），解析失败就当没有
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError:
        return []

    items = []
    for item in root.iterfind(".//item"):
        date_str = _parse_rss_date(_text(item, "pubDate"))
        if not _within_lookback(date_str, lookback_days):
            continue
        items.append({
            "date": date_str,
            "title": _text(item, "title")[:300],
            "url": _text(item, "link"),
            "source": "官方发布",
            "summary": _strip_tags(_text(item, "description"))[:400],
            "via": "official",
            "official": True,
        })

    return items
