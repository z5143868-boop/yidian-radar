"""
变化检测：本周 vs 上周。

严格只做机械对比，不做任何价值判断。
「这个变化重不重要」由 Claude 在分析层回答，不在这里。

这条界线是刻意的：一旦这个文件里出现「重要性评分」之类的逻辑，
它会慢慢长成一堆没人敢改的 if-else，而且判断质量永远比不过 LLM。
"""

from statistics import mean


def diff_week(current: dict, previous: dict | None) -> dict:
    """
    对比两周快照，输出纯客观的变化清单。

    previous 为 None 时（第一周）返回空变化列表 + first_run 标记。
    """
    if previous is None:
        return {
            "week": current.get("week"),
            "first_run": True,
            "changes": [],
            "note": "首次采集，无对比基准。第四周起趋势数据才有意义。",
        }

    prev_map = {c["id"]: c for c in previous.get("competitors", [])}
    changes = []

    for comp in current.get("competitors", []):
        prev = prev_map.get(comp["id"])
        if not prev:
            changes.append({
                "competitor": comp["name"],
                "type": "new_competitor",
                "detail": "本周新加入监控名单",
            })
            continue

        changes.extend(_diff_app(comp, prev))
        changes.extend(_diff_news(comp, prev))
        changes.extend(_diff_website(comp, prev))
        changes.extend(_diff_reviews(comp, prev))

    return {
        "week": current.get("week"),
        "first_run": False,
        "compared_with": previous.get("week"),
        "changes": changes,
        "change_count": len(changes),
    }


def _diff_app(comp: dict, prev: dict) -> list[dict]:
    """版本号、评分、评论量的变化。"""
    out = []
    name = comp["name"]

    for store in ("apple", "google_play"):
        now = (comp.get("app") or {}).get(store) or {}
        was = (prev.get("app") or {}).get(store) or {}
        if not now or not was:
            continue

        # 新版本发布
        if now.get("version") and now["version"] != was.get("version"):
            out.append({
                "competitor": name,
                "type": "new_version",
                "store": store,
                "detail": f"{was.get('version')} → {now['version']}",
                "released_at": now.get("released_at"),
                "release_notes": now.get("release_notes", "")[:1500],
            })

        # 评分变动，阈值 0.1 以下是噪音
        now_rating, was_rating = now.get("rating"), was.get("rating")
        if isinstance(now_rating, (int, float)) and isinstance(was_rating, (int, float)):
            delta = round(now_rating - was_rating, 2)
            if abs(delta) >= 0.1:
                out.append({
                    "competitor": name,
                    "type": "rating_change",
                    "store": store,
                    "detail": f"{was_rating} → {now_rating} ({delta:+.2f})",
                    "delta": delta,
                })

        # 评论量突增：可能是营销投放，也可能是出事了
        now_count, was_count = now.get("rating_count"), was.get("rating_count")
        if isinstance(now_count, int) and isinstance(was_count, int) and was_count > 0:
            growth = now_count - was_count
            growth_rate = growth / was_count
            if growth_rate > 0.15 and growth > 100:
                out.append({
                    "competitor": name,
                    "type": "review_surge",
                    "store": store,
                    "detail": f"评论量 +{growth} ({growth_rate:.0%})",
                    "growth": growth,
                })

    return out


def _diff_news(comp: dict, prev: dict) -> list[dict]:
    """新增新闻：按 URL 去重后的增量。"""
    prev_urls = {n["url"] for n in prev.get("news", []) if n.get("url")}
    fresh = [n for n in comp.get("news", []) if n.get("url") not in prev_urls]

    if not fresh:
        return []

    return [{
        "competitor": comp["name"],
        "type": "news",
        "detail": item["title"],
        "date": item.get("date"),
        "source": item.get("source"),
        "url": item.get("url"),
        "summary": item.get("summary", "")[:300],
    } for item in fresh]


def _diff_website(comp: dict, prev: dict) -> list[dict]:
    """官网变化：hash 不同就报，附上能看出差异的字段。"""
    now = comp.get("website") or {}
    was = prev.get("website") or {}

    if not now.get("content_hash") or not was.get("content_hash"):
        return []
    if now["content_hash"] == was["content_hash"]:
        return []

    detail_parts = []
    if now.get("hero_copy") != was.get("hero_copy"):
        detail_parts.append(f"主标题: 「{was.get('hero_copy')}」→「{now.get('hero_copy')}」")
    if now.get("pricing_signals") != was.get("pricing_signals"):
        detail_parts.append(
            f"定价信号: {was.get('pricing_signals')} → {now.get('pricing_signals')}"
        )
    if not detail_parts:
        detail_parts.append("页面内容有变动，关键字段未变（可能是文案微调或新增区块）")

    return [{
        "competitor": comp["name"],
        "type": "website_change",
        "detail": " | ".join(detail_parts),
        "url": now.get("url"),
    }]


def _diff_reviews(comp: dict, prev: dict) -> list[dict]:
    """差评集中度：本周低分评论占比明显高于上周就标出来。"""
    now_reviews = comp.get("reviews", [])
    was_reviews = prev.get("reviews", [])

    if len(now_reviews) < 10 or len(was_reviews) < 10:
        return []

    def low_ratio(reviews):
        low = [r for r in reviews if isinstance(r.get("rating"), int) and r["rating"] <= 2]
        return len(low) / len(reviews)

    now_ratio, was_ratio = low_ratio(now_reviews), low_ratio(was_reviews)

    if now_ratio > was_ratio + 0.15 and now_ratio > 0.25:
        low_reviews = [r for r in now_reviews if (r.get("rating") or 5) <= 2]
        return [{
            "competitor": comp["name"],
            "type": "negative_surge",
            "detail": f"差评占比 {was_ratio:.0%} → {now_ratio:.0%}",
            "samples": [r["body"][:200] for r in low_reviews[:5]],
        }]

    return []


def release_cadence(weekly_files: list[dict], competitor_id: str) -> dict:
    """
    发版节奏统计。用于看板的趋势图。
    需要至少 4 周数据才有意义，不足就返回空。
    """
    if len(weekly_files) < 4:
        return {}

    versions = []
    for snapshot in weekly_files:
        for comp in snapshot.get("competitors", []):
            if comp["id"] != competitor_id:
                continue
            ver = ((comp.get("app") or {}).get("apple") or {}).get("version")
            if ver:
                versions.append((snapshot.get("week"), ver))

    unique_versions = len({v for _, v in versions})
    weeks = len(versions)

    return {
        "weeks_tracked": weeks,
        "releases": unique_versions,
        "avg_weeks_between_releases": round(weeks / unique_versions, 1) if unique_versions else None,
    }
