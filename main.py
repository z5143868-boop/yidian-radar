#!/usr/bin/env python3
"""
衣殿竞品雷达 - 主入口

用法：
    python main.py                  # 采集本周数据 + 生成 diff
    python main.py --dry-run        # 只跑一个竞品，验证配置是否正确
    python main.py --only alta      # 只跑指定竞品

输出：
    data/2026-W38.json      本周原始快照
    data/changes.json       与上周的机械 diff
    data/latest.json        指向最新一周的副本，给分析层读
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from collectors import app_stores, community, news, website
from core.differ import diff_week
from core.runner import Runner, current_week_id, utc_now_iso

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "competitors.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_competitor(comp: dict, runner: Runner, settings: dict) -> dict:
    """
    采集单个竞品的全部数据源。

    每个源走 runner.run，失败被独立捕获，不影响其他源。
    这就是为什么这里没有一个 try/except——容错在 runner 里，不在这里。
    """
    print(f"\n[{comp['name']}] tier {comp.get('tier')}")

    record = {
        "id": comp["id"],
        "name": comp["name"],
        "tier": comp.get("tier", 3),
        "market": "cn" if comp.get("country") == "cn" else "global",
        "app": {},
        "reviews": [],
        "news": [],
        "website": {},
        "community": [],
    }

    prefix = comp["id"]
    country = comp.get("country", "us")     # 国内竞品填 cn，走中国区商店

    # 配置里没填 ID 就按名字自动查。自动查有误匹配风险，
    # 所以解析结果会打印出来，第一次跑的时候扫一眼再把 ID 填回配置。
    apple_id = comp.get("apple_id")
    if not apple_id and comp.get("app_name"):
        r = runner.run(f"{prefix}.resolve",
                       app_stores.resolve_app_id,
                       comp["app_name"], country=country,
                       timeout=settings["timeout_seconds"])
        if r.status != "failed" and r.data:
            apple_id = r.data["id"]
            record["apple_id_resolved"] = r.data
            print(f"  自动解析 App ID: {apple_id} ({r.data['name']}) "
                  f"[{r.data['confidence']}] ← 建议填回配置")
        else:
            print(f"  App ID 未解析出来，跳过应用商店数据")

    # ---- Apple（A 级源，最稳，优先跑）----
    if apple_id:
        r = runner.run(f"{prefix}.apple",
                       app_stores.fetch_apple_app,
                       apple_id, country=country,
                       timeout=settings["timeout_seconds"])
        if r.status != "failed":
            record["app"]["apple"] = r.data

        r = runner.run(f"{prefix}.apple_reviews",
                       app_stores.fetch_apple_reviews,
                       apple_id, country=country,
                       limit=settings["reviews_per_app"],
                       allow_empty=True, timeout=settings["timeout_seconds"])
        if r.status != "failed" and r.data:
            record["reviews"].extend(r.data)

    # ---- Google Play（B 级源，挂了不影响上面）----
    if comp.get("android_id"):
        r = runner.run(f"{prefix}.google_play",
                       app_stores.fetch_google_play, comp["android_id"])
        if r.status != "failed":
            record["app"]["google_play"] = r.data

        r = runner.run(f"{prefix}.gp_reviews",
                       app_stores.fetch_google_play_reviews,
                       comp["android_id"], limit=settings["reviews_per_app"],
                       allow_empty=True)
        if r.status != "failed" and r.data:
            record["reviews"].extend(r.data)

    # ---- 新闻（A 级源）----
    if comp.get("news_query"):
        r = runner.run(f"{prefix}.news",
                       news.fetch_news,
                       comp["news_query"],
                       lookback_days=settings["news_lookback_days"],
                       allow_empty=True, timeout=settings["timeout_seconds"])
        if r.status != "failed" and r.data:
            record["news"].extend(r.data)

    # ---- 创始人动态（社媒的降级方案）----
    if comp.get("founder"):
        r = runner.run(f"{prefix}.founder",
                       news.fetch_founder_news,
                       comp["founder"],
                       lookback_days=settings["news_lookback_days"],
                       allow_empty=True)
        if r.status != "failed" and r.data:
            for item in r.data:
                item["is_founder_news"] = True
            record["news"].extend(r.data)

    # ---- 官网（B 级源）----
    if comp.get("website"):
        r = runner.run(f"{prefix}.website",
                       website.fetch_website,
                       comp["website"], timeout=settings["timeout_seconds"])
        if r.status != "failed":
            record["website"] = r.data

    # ---- Reddit（B 级源）----
    if comp.get("reddit_query"):
        r = runner.run(f"{prefix}.reddit",
                       community.fetch_reddit,
                       comp["reddit_query"],
                       allow_empty=True, timeout=settings["timeout_seconds"])
        if r.status != "failed" and r.data:
            record["community"] = r.data

    return record


# 查询词 → 看板上的分组。看板按这个分 tab，
# 所以加新查询词时记得在这里归一次类，否则会落到「赛道」里。
INDUSTRY_GROUPS = {
    "服装行业 AI": "服装行业",
    "服装 品牌 出海": "服装行业",
    "服装 零售 数字化": "服装行业",
    "新消费 硬件 融资": "新消费硬件",
    "AI 硬件 消费级": "新消费硬件",
    "智能穿戴 消费电子 融资": "新消费硬件",
    "AI 眼镜 出货": "新消费硬件",
    "consumer AI hardware funding": "新消费硬件",
    "AI 陪伴机器人": "AI 陪伴",
    "具身智能 消费": "AI 陪伴",
    "银发经济 AI": "AI 陪伴",
    "AI companion robot elderly": "AI 陪伴",
}


def collect_industry(queries: list[str], runner: Runner, settings: dict) -> list[dict]:
    """
    行业大盘查询。捕捉赛道级信号、新玩家线索，以及衣殿之外
    两条业务线（镜小搭、ANDA）会关心的硬件与陪伴赛道动态。
    """
    items = []
    seen = set()
    for query in queries:
        r = runner.run(f"industry.{query[:20]}",
                       news.fetch_news, query,
                       lookback_days=settings["news_lookback_days"],
                       allow_empty=True, timeout=settings["timeout_seconds"])
        if r.status == "failed" or not r.data:
            continue
        group = INDUSTRY_GROUPS.get(query, "赛道")
        for item in r.data:
            # 一条新闻可能被多个查询词同时命中，按 URL 去重，保留先到的分组
            if item.get("url") in seen:
                continue
            seen.add(item.get("url"))
            item["group"] = group
            item["from_query"] = query
            items.append(item)
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return items


def load_previous_snapshot(current_week: str) -> dict | None:
    """找上一周的快照。按文件名排序取当前周之前的最后一个。"""
    if not DATA_DIR.exists():
        return None
    snapshots = sorted(
        p for p in DATA_DIR.glob("*-W*.json") if p.stem < current_week
    )
    if not snapshots:
        return None
    with open(snapshots[-1], encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只跑第一个竞品，验证配置")
    parser.add_argument("--only", help="只跑指定 id 的竞品")
    args = parser.parse_args()

    config = load_config()
    settings = config["settings"]
    competitors = config["competitors"]

    if args.only:
        competitors = [c for c in competitors if c["id"] == args.only]
        if not competitors:
            print(f"找不到竞品 id: {args.only}")
            return 1
    elif args.dry_run:
        competitors = competitors[:1]

    week = current_week_id()
    print(f"=== 竞品雷达 {week} ===")
    print(f"监控 {len(competitors)} 个竞品\n")

    runner = Runner(delay=settings["request_delay_seconds"],
                    retries=settings.get("retries", 2))

    snapshot = {
        "week": week,
        "collected_at": utc_now_iso(),
        "competitors": [collect_competitor(c, runner, settings) for c in competitors],
    }

    if not args.dry_run and config.get("industry_queries"):
        print("\n[行业大盘]")
        snapshot["industry_news"] = collect_industry(
            config["industry_queries"], runner, settings)

    snapshot["source_health"] = runner.health_report()

    print(f"\n{runner.summary_line()}")

    # 只要有一个源成功就落盘。宁可残缺，不要静默。
    if not runner.has_any_success():
        print("所有数据源均失败，不写入文件。检查网络或配置。")
        return 1

    DATA_DIR.mkdir(exist_ok=True)

    snapshot_path = DATA_DIR / f"{week}.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"快照已写入 {snapshot_path}")

    with open(DATA_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 生成 diff
    previous = load_previous_snapshot(week)
    changes = diff_week(snapshot, previous)
    with open(DATA_DIR / "changes.json", "w", encoding="utf-8") as f:
        json.dump(changes, f, ensure_ascii=False, indent=2)

    if changes.get("first_run"):
        print("首次运行，无对比基准。下周起会有变化检测。")
    else:
        print(f"检测到 {changes['change_count']} 项变化 (对比 {changes['compared_with']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
