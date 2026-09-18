"""
查 App Store ID 的小工具。

用法：
    python -m tools.resolve "Alta Daily"
    python -m tools.resolve "Doji" --all      # 列出所有候选，自己挑

配置里 apple_id 留空时脚本会自动查，但通用词（如 Alta）容易匹配错，
用这个工具确认一下再填回配置最稳妥。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from collectors.app_stores import HEADERS, ITUNES_SEARCH, resolve_app_id


def list_all(term: str, country: str = "us"):
    resp = requests.get(
        ITUNES_SEARCH,
        params={"term": term, "entity": "software", "country": country, "limit": 15},
        headers=HEADERS, timeout=20,
    )
    resp.raise_for_status()
    for app in resp.json().get("results", []):
        print(f"  {app['trackId']:12} | {app['trackName'][:38]:40s} | "
              f"{(app.get('sellerName') or '')[:24]:26s} | {app.get('genres', [])[:2]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="App 名字")
    parser.add_argument("--all", action="store_true", help="列出全部候选")
    parser.add_argument("--country", default="us")
    args = parser.parse_args()

    if args.all:
        print(f'搜索 "{args.name}" 的全部结果：')
        list_all(args.name, args.country)
        return

    result = resolve_app_id(args.name, country=args.country)
    if result:
        print(f"  apple_id: \"{result['id']}\"")
        print(f"  app_name: {result['name']}")
        print(f"  开发商:   {result['seller']}")
        print(f"  类目:     {result['genres']}")
        print(f"  匹配度:   {result['confidence']}")
        if result["confidence"] != "exact":
            print("\n  名字非完全匹配，建议用 --all 看看全部候选再确认")
    else:
        print(f'  没找到 "{args.name}"。用 --all 看看全部候选：')
        list_all(args.name, args.country)


if __name__ == "__main__":
    main()
