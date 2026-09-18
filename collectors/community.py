"""
社区讨论采集：Reddit 官方 API。稳定性 B 级，免费。

只抓公开搜索结果，不需要 OAuth。
Reddit 对 User-Agent 有要求，必须带一个能识别身份的字符串，
用默认 UA 会直接 429。
"""

import requests

REDDIT_SEARCH = "https://www.reddit.com/search.json"

HEADERS = {
    # Reddit 要求 UA 能识别应用身份，随便填浏览器 UA 反而容易被限流
    "User-Agent": "python:competitor-radar:v1.0 (by /u/yidian_radar)"
}

# 时装/穿搭相关的子版块，搜索时优先看这些
RELEVANT_SUBS = [
    "femalefashionadvice",
    "malefashionadvice",
    "fashion",
    "Anticonsumption",
    "capsulewardrobe",
]


def fetch_reddit(query: str, limit: int = 25, timeout: int = 20) -> list[dict]:
    """
    搜 Reddit 公开讨论。

    比应用商店评论更有价值的地方：评论区里是用户之间的真实对话，
    会聊到「我从 X 换到 Y 因为...」这种竞品迁移原因，
    这是评分数字给不了的信息。
    """
    if not query:
        return []

    resp = requests.get(
        REDDIT_SEARCH,
        params={
            "q": query,
            "sort": "new",
            "limit": limit,
            "t": "month",      # 只看近一个月，更早的没有时效价值
            "type": "link",
        },
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()

    children = resp.json().get("data", {}).get("children", [])

    posts = []
    for child in children:
        post = child.get("data", {})
        posts.append({
            "platform": "reddit",
            "date": _to_date(post.get("created_utc")),
            "title": (post.get("title") or "")[:300],
            "body": (post.get("selftext") or "")[:800],
            "subreddit": post.get("subreddit"),
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
            "url": f"https://reddit.com{post.get('permalink', '')}",
        })

    # 按互动量排序——没人回的帖子没有信号价值
    return sorted(posts, key=lambda p: p["score"] + p["num_comments"] * 2, reverse=True)


def _to_date(timestamp) -> str:
    if not timestamp:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
