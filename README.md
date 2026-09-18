# 衣殿竞品雷达

每周自动采集竞品的 App 动态、资本动态、媒体报道、用户反馈，输出一份「本周变了什么」。

采集层跑在 GitHub Actions，分析层跑在 Claude，数据存在 git 里。
这份 README 只讲怎么跑起来和踩过哪些坑，设计思路见设计方案文档。

## 快速开始

```bash
pip install -r requirements.txt

python main.py --only alta     # 先单跑一个，确认配置没问题
python main.py                 # 跑全部
```

输出：

```
data/2026-W38.json    本周快照
data/changes.json     与上周的机械 diff
data/latest.json      最新一周的副本，给分析层读
```

## 首次运行实测结果（2026-09-18，13 个竞品）

| 数据源 | 结果 |
|---|---|
| Apple App 信息 | 11/13 拿到版本号、评分、更新日志 |
| Apple 用户评论 | 5 家各 50 条（接口有限流，重试后可拿） |
| 新闻（双源） | Stitch Fix 8 条、Daydream 8 条 |
| 官网 | 11/13 成功 |
| Google Play | 包名对的能拿（Whering v3.4.33 / 5M+ 安装） |
| Reddit | 该出口 IP 被 403，换环境可能正常 |

没拿到的都是可降级的部分，核心数据齐了。

## 踩过的坑

**1. App Store ID 不能手填**

手工从商店链接复制 ID 很容易抄错，抄错的表现是「查无此 App」，
分不清是配置错了还是接口挂了。脚本改成按名字自动查。

但自动查也有误匹配：搜 `Alta` 会命中 Alta Networks（一个网络工具），
搜 `Doji` 会命中 DOJI Office。解决办法是用 App Store 类目过滤——
这个赛道的 App 只会在 Lifestyle / Shopping / Photo & Video / Health & Fitness 里，
不会在 Utilities 或 Productivity。加了类目过滤后 Doji 和 Essembl 都能正确匹配。

`Alta` 这种通用词仍然查不准，所以配置里填了实测验证过的 ID。
配置文件里带 `# 实测验证` 注释的都是跑通过的。

**2. 中文新闻只能靠 Google News**

Bing News RSS 的中文查询在美国出口返回 0 条，加 `mkt=zh-CN` 或 `cc=CN`
参数反而会被重定向到 HTML 首页。Google News RSS 中文返回 99 条，
源包括新浪财经、36氪、第一财经。

GitHub Actions 的出口也在美国，所以这个限制在生产环境同样存在。
双源架构里 Bing 只是英文的补充。

**3. 中文泛词查出来全是 SEO 垃圾**

搜「AI 穿搭」返回的前几条是彩票网站塞关键词的内容。
中文查询必须带明确意图词（「融资」「智能衣橱」），或者直接用公司名。

**4. Apple 评论接口会间歇性返回空**

同一个 URL，第一次返回空 feed，隔几秒重试返回 50 条。
不是 URL 格式问题——四种 URL 变体都测过，都是这个表现。
所以 runner 里的重试是必需的，不是保险。

**5. 「本周没新闻」不是故障**

最早的实现把空结果一律标成 partial，结果 source_health 一片橙色。
人对长期报警的反应是无视它，真故障就被淹没了。
现在新闻、评论、社区类的源标了 `allow_empty=True`，空结果算正常。

## 配置

改 `config/competitors.yaml`，不用动代码。

```yaml
- id: alta
  name: Alta
  tier: 1                      # 1=每周全量 2=功能+融资 3=只看大动作
  apple_id: "6481705400"       # 留空则按 app_name 自动查
  app_name: Alta Daily
  android_id: ""
  website: https://www.altadaily.com/
  news_query: '"Alta" AI stylist app'
  founder: Jenny Wang          # 社媒的降级方案：搜创始人名字的新闻
  reddit_query: Alta app stylist closet
```

加竞品就加一条。`android_id` 需要手动查 Play 商店链接里的 `id=` 参数——
Google Play 没有官方搜索接口，没法自动解析。

## 部署到 GitHub Actions

1. 建一个私有仓库，把这些文件推上去
2. Settings → Actions → General → Workflow permissions 选 **Read and write**
   （不开这个权限，Actions 没法把数据 commit 回仓库）
3. Actions 页面手动触发一次 `竞品雷达周采集`，确认能跑通
4. 之后每周一 UTC 22:00（北京时间周二 06:00）自动跑

改时间在 `.github/workflows/weekly.yml` 的 cron 里。注意那是 UTC。

## 目录结构

```
config/competitors.yaml      竞品名单，改这个就行
collectors/
  app_stores.py              Apple + Google Play
  news.py                    Google News + Bing News 双源
  website.py                 官网变化检测
  community.py               Reddit
core/
  runner.py                  调度与容错，整个系统的抗压核心
  differ.py                  本周 vs 上周的机械对比
main.py                      入口
data/                        周度快照，git 跟踪
```

## 一条设计原则

**脚本层只搬运事实，不做判断。**

`differ.py` 只算「版本从 2.2.3 变成 2.2.4」「评分从 4.7 掉到 4.5」，
不判断这重不重要。重要性判断在分析层由 Claude 做。

一旦这条界线破了，脚本里会开始长出「重要性评分」之类的 if-else，
越长越复杂，最后没人敢改，判断质量还不如直接交给模型。
