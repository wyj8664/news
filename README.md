# NEWSHOT / TrendRadar

更新时间：2026-07-10

这是基于 TrendRadar 改造的 NEWSHOT 线上来源看板。它现在的核心定位不是最终编辑供稿页，而是统一收集、展示各来源热榜和快讯，为后续 `/briefing/latest.md` 供稿系统提供来源池。

## 线上地址

```text
https://newshot-5jq.pages.dev/
https://newshot-5jq.pages.dev/hotlists/
https://newshot-5jq.pages.dev/hotlists/telegraph/
```

当前关键页面：

- `/hotlists/`：来源热榜看板，微博、今日头条、百度热搜、抖音、知乎、Bilibili、雪球、财联社热门、金十数据等在这里按来源展示。
- `/hotlists/telegraph/`：财联社电报时间流，放在来源热榜体系里，不和热点脉络、日报平行。
- `/daily/`：长内容日报暂时停更，只生成停更占位页。
- 计划中的 `/briefing/latest.md`：真正给编辑群复制粘贴的 Markdown 选题池，详见 `docs/editor-briefing-system.md`。

## 当前刷新状态

### 金十数据

金十数据已经有前端实时刷新能力：

```text
/api/jin10/flash?refresh=1
```

前端按钮会通过 Cloudflare Pages `_worker.js` 在服务端实时抓取金十，筛选：

```text
important=1 或 爆 / 沸 / 热 / 火
```

筛选后只按时间倒序展示。`important` 和 `hot` 标签只作为召回条件，不参与排序加权。

金十分类使用金十官方 `/classify` 分类；对于明显地缘冲突内容，系统会做一层修正，避免被金十市场分类误展示成美股、A股等。

### 其他热榜

微博、今日头条、百度热搜、抖音、知乎、Bilibili、雪球等来源仍然是静态生成结果：

- 点击来源 tab 只是切换当前页面已有数据。
- 浏览器刷新只是重新请求已经部署的页面。
- 它们不会因为前端点击而重新抓源站。
- 更新依赖 GitHub Actions 跑 TrendRadar 并重新部署 Cloudflare Pages。

当前 workflow 仍是约 2 小时一次：

```yaml
schedule:
  - cron: "17 */2 * * *"
```

因为长内容日报已经停更，这个频率可以后续缩短到 15 到 30 分钟，但这需要改 GitHub Actions，而不是改前端按钮。

### 财联社电报

财联社电报当前由：

```text
tools/cls_telegraph.py
```

生成静态页面：

```text
output/hotlists/telegraph/index.html
```

它按财联社电报时间流抓取并按时间排序。当前还没有像金十一样的前端实时刷新按钮，后续可以补 `/api/cls/telegraph`。

## 编辑供稿方向

讨论后的目标不是继续把 `/hotlists/` 做成最终产品，而是新增一条编辑供稿流水线：

```text
来源抓取
-> 标准化 raw_doc
-> 早报 / 合集 / 长标题拆成 atomic_item
-> 大召回
-> 聚类成 event
-> 识别 progress
-> 赛道分类
-> 时间窗口排序
-> 每赛道 20 条
-> 写入已发历史
-> 生成可复制 Markdown
```

最终产物：

```text
output/briefing/latest.md
output/briefing/latest.html
```

群里复制的格式只保留：

```markdown
## AI / 科技

1. [标题](链接)
2. [标题](链接)
```

不要把调试字段、分数、来源明细放进群版 Markdown。

完整方案见：

```text
docs/editor-briefing-system.md
```

## 本地运行

```bash
docker compose up -d
```

打开：

```text
http://localhost:8080/
```

手动跑一次 TrendRadar：

```bash
docker exec trendradar sh -lc 'cd /app && ./.venv/bin/python -m trendradar'
```

## Cloudflare 部署

主要入口：

```text
.github/workflows/cloudflare-pages.yml
cloudflare/pages/_headers
cloudflare/pages/_worker.js
tools/curated_events.py
tools/cls_telegraph.py
tools/daily_pause.py
```

部署细节见：

```text
cloudflare/README.md
```

## 新开对话建议

如果新开 Codex 对话继续这个项目，先读：

```text
/Users/wangyajing/skills/新闻/TrendRadar/README.md
/Users/wangyajing/skills/新闻/TrendRadar/cloudflare/README.md
/Users/wangyajing/skills/新闻/TrendRadar/docs/editor-briefing-system.md
/Users/wangyajing/skills/新闻/wechat-news-site/README.md
/Users/wangyajing/skills/新闻/wechat-news-site/MONITORING_SITE_HANDOFF.md
```
