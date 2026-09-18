"""
应用商店采集：Apple App Store + Google Play。

Apple 走官方公开接口，稳定性 A 级，一两年不用动：
  - iTunes Search API: 版本号、更新日志、评分、评论数
  - Customer Reviews RSS: 用户评论全文

Google Play 走第三方库，稳定性 B 级，大概半年要升级一次库。
挂了不影响 Apple 那部分——这是刻意的分离。
"""

import requests

ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_REVIEWS = "https://itunes.apple.com/{country}/rss/customerreviews/page=1/id={app_id}/sortby=mostrecent/json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CompetitorRadar/1.0)"
}


# 这个赛道的 App 只会出现在这几个类目里。
# 用类目过滤能挡掉大量同名误匹配——实测搜 "Alta" 会命中一个视频剪辑 App，
# 搜 "Doji" 会命中一个办公软件，都是靠这个挡掉的。
EXPECTED_GENRES = {
    # 美区类目
    "Lifestyle", "Shopping", "Photo & Video", "Health & Fitness",
    # 中国区类目（同一个接口按 country 返回本地化名称，必须两套都列）
    "生活", "购物", "摄影与录像", "健康健美", "工具", "效率",
}
# 刻意不含 Utilities / Productivity：这个赛道的 App 不会归在那两类，
# 而同名的网络工具、办公软件全在那里。放进来的话 "Alta" 会匹配到
# Alta Networks、"Doji" 会匹配到 DOJI Office——实测踩过。
# 带这两个类目的正牌 App（如 Acloset、Indyx）都同时挂了 Lifestyle 或 Shopping，
# 不会被误伤。


# 「工具/效率」只在中国区出现（美区返回的是英文类目名），
# 所以把它们列进来不会放宽美区的过滤——Alta Networks 仍然被 Utilities 挡掉。
# 中国区这个赛道的 App 大量挂在「生活+工具」下，不列就全都匹配不上。


def resolve_app_id(app_name: str, developer_hint: str = "",
                   expect_genres: set[str] | None = None,
                   country: str = "us", timeout: int = 20) -> dict:
    """
    按 App 名字查 App Store ID。

    配置文件里不写数字 ID——手工去 App Store 复制 ID 容易抄错，
    而抄错的表现是「查无此 App」，很难一眼看出是配置问题还是接口挂了。
    让脚本自己查，配置里只写人看得懂的名字。

    匹配不确定时宁可返回空。抓错对象比少抓一个源危险得多：
    少一个源会在 source_health 里显示出来，抓错对象则是静默地
    往报告里灌别人家的数据，而且没人会发现。

    返回 {id, name, seller, genres, confidence}，查不到返回 {}。
    """
    if not app_name:
        return {}

    genres = expect_genres or EXPECTED_GENRES

    resp = requests.get(
        ITUNES_SEARCH,
        params={"term": app_name, "entity": "software",
                "country": country, "limit": 20},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return {}

    target = app_name.lower().strip()
    candidates = []

    for app in results:
        name = (app.get("trackName") or "").lower()
        seller = (app.get("sellerName") or "").lower()
        app_genres = set(app.get("genres") or [])

        # 名字必须以目标开头，或目标是名字的完整前缀词
        # "Whering: Your Digital Closet" 匹配 "whering" ✓
        # "Alta Video" 匹配 "alta" ✓ —— 所以还需要类目再筛一道
        if not (name == target or name.startswith(target + " ")
                or name.startswith(target + ":") or name.startswith(target + " -")):
            continue

        genre_ok = bool(app_genres & genres)
        hint_ok = bool(developer_hint) and developer_hint.lower() in seller

        # 类目不符且开发商也对不上 → 大概率是同名的无关 App，直接排除
        if not genre_ok and not hint_ok:
            continue

        candidates.append((
            (name == target, hint_ok, genre_ok, -len(name)),
            app,
        ))

    if not candidates:
        return {}

    _, best = max(candidates, key=lambda x: x[0])
    best_name = (best.get("trackName") or "").lower()

    return {
        "id": str(best.get("trackId")),
        "name": best.get("trackName"),
        "seller": best.get("sellerName"),
        "genres": best.get("genres", []),
        # exact 表示名字完全一致，可以放心用；
        # prefix 表示靠前缀+类目匹配上的，第一次跑建议人工扫一眼
        "confidence": "exact" if best_name == target else "prefix",
    }


def fetch_apple_app(app_id: str, country: str = "us", timeout: int = 20) -> dict:
    """
    Apple 官方 Search API。这是整个系统最稳的一个源。

    返回版本号、更新日志、评分、评论数、价格、更新时间。
    """
    if not app_id:
        return {}

    resp = requests.get(
        ITUNES_LOOKUP,
        params={"id": app_id, "country": country},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    if not results:
        raise ValueError(f"App {app_id} not found in {country} store")

    app = results[0]
    return {
        "version": app.get("version"),
        "released_at": (app.get("currentVersionReleaseDate") or "")[:10],
        "release_notes": (app.get("releaseNotes") or "")[:2000],
        "rating": app.get("averageUserRatingForCurrentVersion"),
        "rating_all_versions": app.get("averageUserRating"),
        "rating_count": app.get("userRatingCount"),
        "price": app.get("formattedPrice"),
        "genres": app.get("genres", []),
        "seller": app.get("sellerName"),
        "app_url": app.get("trackViewUrl"),
    }


def fetch_apple_reviews(app_id: str, country: str = "us",
                        limit: int = 50, timeout: int = 20) -> list[dict]:
    """
    Apple 官方评论 RSS。拿最近的评论全文。

    这是口碑分析的主要数据源——评分只是个数字，
    评论正文才能告诉你用户到底在骂什么。
    """
    if not app_id:
        return []

    url = ITUNES_REVIEWS.format(country=country, app_id=app_id)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()

    feed = resp.json().get("feed", {})
    entries = feed.get("entry", [])

    # 单条评论时 Apple 返回 dict 而非 list，这个坑踩过一次就记住了
    if isinstance(entries, dict):
        entries = [entries]

    reviews = []
    for entry in entries[:limit]:
        # 第一个 entry 有时是 App 信息而非评论，用有没有 rating 区分
        if "im:rating" not in entry:
            continue
        reviews.append({
            "date": (entry.get("updated", {}).get("label") or "")[:10],
            "rating": int(entry.get("im:rating", {}).get("label", 0)),
            "version": entry.get("im:version", {}).get("label"),
            "title": entry.get("title", {}).get("label", "")[:200],
            "body": entry.get("content", {}).get("label", "")[:1000],
            "author": entry.get("author", {}).get("name", {}).get("label"),
        })

    return reviews


def fetch_google_play(package_id: str, lang: str = "en", country: str = "us") -> dict:
    """
    Google Play。靠 google-play-scraper 第三方库。

    库失效时整个函数抛异常，由 runner 接住标记为 failed，
    Apple 那部分照常跑。这就是分离的意义。
    """
    if not package_id:
        return {}

    # 延迟导入：库没装或版本不兼容时，只影响这一个源
    from google_play_scraper import app as gp_app

    info = gp_app(package_id, lang=lang, country=country)
    return {
        "version": info.get("version"),
        "released_at": (info.get("lastUpdatedOn") or ""),
        "release_notes": (info.get("recentChanges") or "")[:2000],
        "rating": info.get("score"),
        "rating_count": info.get("ratings"),
        "review_count": info.get("reviews"),
        "installs": info.get("installs"),
        "price": info.get("price"),
        "app_url": info.get("url"),
    }


def fetch_google_play_reviews(package_id: str, limit: int = 50,
                              lang: str = "en", country: str = "us") -> list[dict]:
    """Google Play 评论。同样是 B 级源，挂了就跳过。"""
    if not package_id:
        return []

    from google_play_scraper import Sort, reviews as gp_reviews

    result, _ = gp_reviews(
        package_id,
        lang=lang,
        country=country,
        sort=Sort.NEWEST,
        count=limit,
    )

    return [{
        "date": r["at"].strftime("%Y-%m-%d") if r.get("at") else "",
        "rating": r.get("score"),
        "version": r.get("reviewCreatedVersion"),
        "title": "",
        "body": (r.get("content") or "")[:1000],
        "author": r.get("userName"),
        "thumbs_up": r.get("thumbsUpCount", 0),
    } for r in result]
