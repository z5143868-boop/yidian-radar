"""
新闻与资本动态采集。双源架构：Google News RSS 为主，Bing News RSS 为备。

实测数据（2026-09 在 GitHub Actions 同类的美国出口环境）：
  Google News RSS  中文 99 条 / 英文 100 条   ← 主源
  Bing News RSS    中文  0 条 / 英文  10 条   ← 备源，中文不可用

两个源都走公开 RSS，不需要 key，稳定性 A 级。
用双源不是为了凑数：任何一个挂了另一个还能出结果，
而这个 collector 同时承担资本动态、媒体报道、创始人动态三个职责，
是整个系统里最不能断的一环。

注意：query 用具体公司名/产品名，泛概念词查不出东西。
"""

import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
BING_NEWS_RSS = "https://www.bing.com/news/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

RSS_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")

# 语言 → Google News 的地区参数
LOCALES = {
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


# ---------- 垃圾过滤 ----------
# 中文新闻搜索会被 SEO 农场污染：博彩、彩票、棋牌站把热词塞进标题
# 骗收录，实测「AI 眼镜 出货」「智能镜 AI」这类词能带出
# 「新澳门网址登录入口推动智能训练设备上市」这种条目。
# 不过滤的话，看板上真信号会被垃圾稀释，而人只要被垃圾骗过两次就不再看了。

SPAM_SOURCES = {
    "体坛加", "体坛网", "icloudnews.net",
}

SPAM_PATTERNS = re.compile(
    r"澳门|博彩|彩票|棋牌|六合彩|百家乐|真人娱乐|开户送|注册送|"
    r"登录入口|官网地址|app在线|下载安装|九游会|太阳城|威尼斯人|"
    r"电子游戏|老虎机|赌场|下注|投注",
    re.I,
)


def _is_spam(item: dict) -> bool:
    """来源在黑名单里，或标题/摘要命中博彩类关键词，就丢掉。"""
    if item.get("source") in SPAM_SOURCES:
        return True
    blob = (item.get("title") or "") + " " + (item.get("summary") or "")
    return bool(SPAM_PATTERNS.search(blob))


def _parse_rss_date(raw: str) -> str:
    """RSS pubDate → YYYY-MM-DD。解析不了返回空串。"""
    if not raw:
        return ""
    match = RSS_DATE_RE.search(raw)
    if not match:
        return ""
    day, month_abbr, year = match.groups()
    month = MONTHS.get(month_abbr)
    return f"{year}-{month:02d}-{int(day):02d}" if month else ""


def _within_lookback(date_str: str, lookback_days: int) -> bool:
    """过滤回溯窗口外的条目。两个源都会混进旧闻，必须过滤。"""
    if not date_str:
        return False
    try:
        published = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return published >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


def _text(element, tag: str) -> str:
    node = element.find(tag)
    return (node.text or "").strip() if node is not None else ""


def _strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def _detect_lang(query: str) -> str:
    """有中日韩字符就当中文查询处理。"""
    return "zh" if re.search(r"[一-鿿]", query) else "en"


def fetch_google_news(query: str, lookback_days: int = 8, timeout: int = 20) -> list[dict]:
    """
    Google News RSS。主源。

    标题格式是「标题 - 来源名」，来源另有 <source> 标签，
    两处都取，以 source 标签为准。
    """
    if not query:
        return []

    locale = LOCALES[_detect_lang(query)]
    resp = requests.get(
        GOOGLE_NEWS_RSS,
        params={"q": query, **locale},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    items = []
    for item in root.iterfind(".//item"):
        date_str = _parse_rss_date(_text(item, "pubDate"))
        if not _within_lookback(date_str, lookback_days):
            continue

        title = _text(item, "title")
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""

        # 标题尾部的「 - 来源」是冗余，去掉让标题更干净
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]

        items.append({
            "date": date_str,
            "title": title[:300],
            "url": _text(item, "link"),
            "source": source or _tail_source(title),
            "summary": _strip_tags(_text(item, "description"))[:400],
            "via": "google",
        })

    return items


def fetch_bing_news(query: str, lookback_days: int = 8, timeout: int = 20) -> list[dict]:
    """
    Bing News RSS。备源。

    实测中文查询在美国出口返回空，所以中文只靠 Google。
    英文两个源结果有差异，合并后覆盖面更全。
    """
    if not query:
        return []

    resp = requests.get(
        BING_NEWS_RSS,
        params={"q": query, "format": "RSS"},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    items = []
    for item in root.iterfind(".//item"):
        date_str = _parse_rss_date(_text(item, "pubDate"))
        if not _within_lookback(date_str, lookback_days):
            continue
        items.append({
            "date": date_str,
            "title": _text(item, "title")[:300],
            "url": _text(item, "link"),
            "source": _bing_source(item),
            "summary": _strip_tags(_text(item, "description"))[:400],
            "via": "bing",
        })

    return items


def _bing_source(item) -> str:
    """Bing 把来源放在带命名空间的 News:Source 里，直接遍历找。"""
    for child in item:
        if child.tag.endswith("Source") and child.text:
            return child.text.strip()
    return ""


def _tail_source(title: str) -> str:
    """Google 少数条目没有 source 标签，从标题尾部「 - xxx」兜底。"""
    parts = title.rsplit(" - ", 1)
    return parts[1].strip() if len(parts) == 2 and len(parts[1]) < 40 else ""


def fetch_news(query: str, lookback_days: int = 8, timeout: int = 20) -> list[dict]:
    """
    双源合并。任一源失败不影响另一个。

    这是对外的唯一入口，上层不需要知道底下有两个源。
    """
    if not query:
        return []

    collected = []
    for fetcher in (fetch_google_news, fetch_bing_news):
        try:
            collected.extend(fetcher(query, lookback_days=lookback_days, timeout=timeout))
        except Exception as err:
            # 单源失败只记一行，不抛出——另一个源还有机会
            print(f"    [{fetcher.__name__}] {type(err).__name__}: {str(err)[:80]}")

    clean = [it for it in collected if not _is_spam(it)]
    return _dedupe(clean)


def _dedupe(items: list[dict]) -> list[dict]:
    """
    按标题去重。同一条新闻会被多家转载，也会被两个源同时抓到。

    标准化后取前 40 字做 key：去掉空白和标点，避免
    「字节跳动完成2.9亿美元融资」和「字节跳动完成 2.9 亿美元融资」被当成两条。
    """
    seen = set()
    unique = []
    for item in sorted(items, key=lambda x: x["date"], reverse=True):
        key = re.sub(r"[\s\W_]+", "", item["title"].lower())[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def fetch_founder_news(founder_name: str, lookback_days: int = 8) -> list[dict]:
    """
    创始人动态的降级方案。

    抓不到 X/小红书原始发帖，但会影响格局的动作（融资发言、
    战略采访、重大合作）一定会被报道。实测 Alta 创始人的
    CFDA 合作、LVMH 投资、名人背书全都能抓到。

    漏掉的只是日常发推，这个损失可以接受。
    """
    if not founder_name:
        return []
    return fetch_news(f'"{founder_name}"', lookback_days=lookback_days)
