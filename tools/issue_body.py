"""
把本周变化生成一份 GitHub Issue 正文。

为什么用 Issue 做通知：GitHub 的通知系统成熟、国内收得到，
而且不需要额外配置——建 Issue 时 @ 你，GitHub 就发邮件，
手机装了 GitHub App 还有推送。整条链路都在 GitHub 内部，
不经过第三方，也不依赖任何一个可能失效的中间环节。

这里只写机械事实（谁变了什么），判断和分析在看板上。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD_URL = "https://claude.ai/artifact/D4Xjqa92j2Bt8LWRMzyYkd"

TYPE_LABEL = {
    "new_version": "发布新版本",
    "rating_change": "评分变动",
    "review_surge": "评论量突增",
    "news": "新增报道",
    "website_change": "官网改版",
    "negative_surge": "差评激增",
    "new_competitor": "新入监控",
}


def main():
    data_dir = ROOT / "data"
    snap = json.loads((data_dir / "latest.json").read_text(encoding="utf-8"))
    try:
        changes = json.loads((data_dir / "changes.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        changes = {"changes": [], "first_run": True}

    week = snap.get("week", "")
    comps = snap.get("competitors", [])
    health = snap.get("source_health", {})
    bad = [k for k, v in health.items() if v != "ok"]
    # Reddit 封了数据中心 IP，是已知情况，不当故障看
    real_bad = [k for k in bad if not k.endswith("reddit")]

    out = [f"## {week} 竞品雷达", ""]
    out.append(f"[打开看板 →]({BOARD_URL})　完整分析、差评原文、中文翻译都在看板上")
    out.append("")

    ch = changes.get("changes", [])
    if changes.get("first_run"):
        out.append("首次采集，无对比基准。下周起显示变化。")
        out.append("")
    elif not ch:
        out.append("**本周无变化。** 竞品都没动作。")
        out.append("")
    else:
        by_type = {}
        for c in ch:
            by_type.setdefault(c.get("type", "other"), []).append(c)

        out.append(f"**本周 {len(ch)} 项变化**")
        out.append("")
        for t, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
            out.append(f"**{TYPE_LABEL.get(t, t)}（{len(items)}）**")
            for it in items[:6]:
                detail = str(it.get("detail", "")).replace("\n", " ")[:110]
                out.append(f"- {it.get('competitor', '')}：{detail}")
            if len(items) > 6:
                out.append(f"- …另有 {len(items) - 6} 项")
            out.append("")

    # 差评率排行——口碑缺口往往就是机会
    ranked = []
    for c in comps:
        reviews = c.get("reviews", [])
        if len(reviews) < 20:
            continue
        low = sum(1 for r in reviews if (r.get("rating") or 5) <= 2)
        ranked.append((low / len(reviews), c.get("name", ""), low, len(reviews)))

    if ranked:
        out.append("**差评率 TOP 3**")
        for rate, name, low, total in sorted(ranked, reverse=True)[:3]:
            out.append(f"- {name} {rate:.0%}（{low}/{total}）")
        out.append("")

    out.append("---")
    ok_n = len(health) - len(bad)
    tail = f"数据源 {ok_n}/{len(health)} 正常"
    if real_bad:
        tail += f"，{len(real_bad)} 个异常需要看一眼：{', '.join(real_bad[:3])}"
    tail += "（Reddit 封了 GitHub 的 IP，已知情况，不计入）"
    out.append(tail)

    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main()
