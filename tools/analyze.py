"""
调 Claude API 生成 data/analysis.json：本周信号 + 中文译文。

这是整套系统里唯一需要花钱的一步，也是唯一需要「判断」的一步。
采集脚本只搬事实，这里负责回答「变化意味着什么」。

用法（Actions 里自动跑，本地调试也一样）：
    export ANTHROPIC_API_KEY=sk-ant-...
    python tools/analyze.py

没有 key 时直接退出，不报错——采集和看板照常，只是这周没有新的信号和译文，
页面会沿用上一版 analysis.json。宁可少一块，不要整条链路挂掉。
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

API_URL = "https://api.anthropic.com/v1/messages"
# 模型 ID 会随版本更新变化。这里给一个默认值，失效时脚本会自动
# 查询账号可用模型并重试，所以不用盯着它。想固定用某个模型就设 RADAR_MODEL。
MODEL = os.environ.get("RADAR_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = 16000

# 衣殿的业务背景，用于判断「这个变化对我们意味着什么」
CONTEXT = """衣殿是 AI 个人形象顾问产品。核心是西蔓 6 维建档（冷暖/明度/纯度 + 量感/直曲/成熟度），
走「半 AI + 真人视频精诊」路线：会前 AI 预判，会中诊断师验证修正，会后 AI 生成报告。
上门和线上两套方案，私域获客。诊断的是人的属性，不是衣服的排列组合——这是跟衣橱管理类产品的根本区别。"""

SYSTEM = """你是衣殿的竞品分析师。每周读竞品监控数据，提炼出最多 5 条值得创始人动作的信号。

硬规则，逐条遵守：

1. 最多 5 条，宁可只有 1 条也不凑数。写不出「对衣殿意味着什么」的条目直接丢掉。
2. 优先找跨竞品的模式。「三家同时被骂同一件事」比「某家发了新版本」有价值得多。
3. 每条的 verdict 必须不同。两条判断雷同说明其中一条不该存在。
4. body 里尽量引用用户差评原文——原文比转述有力得多。英文原文翻成中文再引用。
5. 不要写「市场可能还没准备好」这类正确但无用的话。要么给出具体判断和动作，要么不写这条。

翻译要求：说人话，保留用户原本的情绪和语气。差评里的愤怒、嘲讽要翻出来，不要软化成书面语。
产品名和版本号保持原样。「It's a trap」翻成「这是个陷阱」，不是「这是一个陷阱」。

只输出 JSON，不要任何解释文字，不要 markdown 代码块包裹。"""


def list_models(api_key: str) -> list[str]:
    """
    列出账号可用的模型。模型 ID 会随版本更新变化，写死一个名字
    迟早会在某个周二早上静默失效，所以宁可多一次请求也要问清楚。
    """
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models?limit=50",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    return [m["id"] for m in payload.get("data", [])]


def pick_model(available: list[str]) -> str | None:
    """
    优先 sonnet（判断质量和成本的平衡点），其次 opus，最后 haiku。
    同一系列里取排在最前的——API 返回按新到旧排序。
    """
    for family in ("sonnet", "opus", "haiku"):
        for mid in available:
            if family in mid.lower():
                return mid
    return available[0] if available else None


def call_api(prompt: str, api_key: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL, data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read())
    return "".join(b.get("text", "") for b in payload.get("content", []))


def extract_json(raw: str) -> dict:
    """模型偶尔会套一层 ```json，剥掉再解析。"""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    return json.loads(text)


def build_prompt(snap: dict, changes: dict) -> str:
    """
    只把需要判断的部分喂给模型，不是整个 latest.json。
    整份数据 450KB，塞进去既贵又让模型抓不住重点。
    """
    brief = []
    for c in snap.get("competitors", []):
        reviews = c.get("reviews", [])
        low = [r for r in reviews if (r.get("rating") or 5) <= 2][:6]
        apple = (c.get("app") or {}).get("apple") or {}
        item = {
            "id": c["id"],
            "name": c["name"],
            "tier": c.get("tier"),
            "market": c.get("market"),
            "version": apple.get("version"),
            "released_at": apple.get("released_at"),
            "rating": apple.get("rating_all_versions"),
            "rating_count": apple.get("rating_count"),
            "release_notes": (apple.get("release_notes") or "")[:300],
            "reviews_total": len(reviews),
            "low_count": sum(1 for r in reviews if (r.get("rating") or 5) <= 2),
            "low_samples": [
                {"rating": r.get("rating"), "date": r.get("date"),
                 "title": (r.get("title") or "")[:60],
                 "body": (r.get("body") or "")[:280]}
                for r in low
            ],
            "news": [
                {"date": n.get("date"), "title": n.get("title"),
                 "source": n.get("source"), "url": n.get("url"),
                 "founder": bool(n.get("is_founder_news"))}
                for n in (c.get("news") or [])[:8]
            ],
            "hero": (c.get("website") or {}).get("hero_copy"),
        }
        brief.append(item)

    industry = [
        {"date": n.get("date"), "title": n.get("title"),
         "source": n.get("source"), "url": n.get("url"), "group": n.get("group")}
        for n in (snap.get("industry_news") or [])[:40]
    ]

    return f"""{CONTEXT}

## 本周竞品数据（{snap.get('week')}）

```json
{json.dumps(brief, ensure_ascii=False)}
```

## 与上周的机械差异

```json
{json.dumps(changes.get('changes', [])[:60], ensure_ascii=False)}
```

## 行业动态

```json
{json.dumps(industry, ensure_ascii=False)}
```

---

输出一个 JSON 对象，两个字段：

**signals**：最多 5 条，每条
`{{"sev": "crit|warn|info", "tag": "4字以内标签", "title": "一句话带数字", "body": "支撑事实，引用差评原文", "verdict": "对衣殿意味着什么+建议动作", "jump": "竞品id"}}`

**translations**：按竞品 id 组织的译文，只翻英文内容，中文的不用管
`{{"竞品id": {{"reviews": [{{"t":"标题译文","b":"正文译文"}}, ...按 low_samples 顺序，没有就放 null],
   "news": ["标题译文", ...按 news 顺序，中文的放 null],
   "hero": "官网主标题译文", "release_notes": "更新日志译文"}},
  "__industry__": {{"新闻url": "标题译文"}}}}
"""


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("未设置 ANTHROPIC_API_KEY，跳过分析。看板会沿用上一版 analysis.json。")
        return 0

    snap = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    try:
        changes = json.loads((DATA / "changes.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        changes = {"changes": []}

    prompt = build_prompt(snap, changes)
    model = MODEL
    print(f"提示词 {len(prompt)} 字符，调用 {model}…")

    try:
        raw = call_api(prompt, api_key, model)
        result = extract_json(raw)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        # 模型名失效是最可能的故障：自动问一次账号有哪些模型再重试
        if err.code in (400, 404) and "model" in detail.lower():
            print(f"模型 {model} 不可用，查询账号可用模型…")
            try:
                available = list_models(api_key)
                model = pick_model(available)
                print(f"  可用: {', '.join(available[:6])}")
                if not model:
                    print("  账号下没有可用模型")
                    return 1
                print(f"  改用 {model} 重试")
                raw = call_api(prompt, api_key, model)
                result = extract_json(raw)
            except Exception as retry_err:
                print(f"  重试失败: {type(retry_err).__name__}: {retry_err}")
                return 1
        else:
            print(f"API 报错 {err.code}: {detail}")
            return 1
    except json.JSONDecodeError as err:
        print(f"模型没返回合法 JSON: {err}")
        return 1
    except Exception as err:
        print(f"调用失败: {type(err).__name__}: {err}")
        return 1

    signals = result.get("signals", [])
    if not isinstance(signals, list):
        print("signals 字段不是列表，放弃写入")
        return 1

    # 自己定的规矩自己守：超过 5 条截断
    signals = signals[:5]

    out = {
        "generated_at": snap.get("collected_at", "").replace("T", " ").replace("Z", " UTC"),
        "for_week": snap.get("week"),
        "model": model,
        "signals": signals,
        "translations": result.get("translations", {}),
    }

    # 写入前先确认能被解析回来，别把坏文件推上去
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    json.loads(payload)
    (DATA / "analysis.json").write_text(payload, encoding="utf-8")

    print(f"写入 analysis.json：{len(signals)} 条信号，"
          f"{len(out['translations'])} 个竞品的译文")
    for s in signals:
        print(f"  [{s.get('sev')}] {s.get('title', '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
