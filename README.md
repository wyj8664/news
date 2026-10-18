# NEWSHOT / TrendRadar

更新时间：2026-07-10

这是基于 TrendRadar 改造的 NEWSHOT 来源看板。它现在的核心定位不是最终编辑供稿页，而是统一收集、展示各来源热榜和快讯，并为 `/briefing/latest.md` 供稿系统提供来源池。四源统一供稿的第一版 dry-run 已经可以在本机生成预览。

## 线上地址

```text
https://newshot-5jq.pages.dev/
https://newshot-5jq.pages.dev/hotlists/
https://newshot-5jq.pages.dev/hotlists/telegraph/
```

当前关键页面：

- `/`：全部关注来源的近24小时内容，经原文时间核验、事件去重聚类后按领域展示；筛选顺序为“全部、硬科技、大宗、新消费、汽车、新能源、金融、物流、社会”。不要求上过热榜或被标为重要。财联社电报、金十普通流、关注微博和RSS都能进入，不仅是热榜条目。
- `/hotlists/`：来源热榜看板，微博、今日头条、百度热搜、抖音、知乎、Bilibili、雪球、财联社热门、金十数据等在这里按来源展示。
- `/hotlists/telegraph/`：财联社电报时间流，支持前端实时刷新，放在来源热榜体系里。
- `/daily/`：七赛道日报，原版左侧日期归档＋右侧连续排名列表。可切换今天及前6天，按原文北京时间自然日归档，每天按综合分展示最多50条，不足不补；复用领域分类的分类与稳定事件／进展／文章ID，不包含社会。历史日报不会随24小时到期而消失。热点脉络入口已移除，旧 `/timeline/` 跳转日报。发布目标仍是 `newshot-5jq.pages.dev`，部署流程见 Cloudflare 说明。
- `/briefing/latest.md`：四源统一召回、聚类后的 7 赛道 Markdown 选题池；工作日四批生成后通过旧早报机器人发送飞书。
- `/briefing/latest.html`：同批结果的本地调试预览，包含来源状态、聚类明细和可解释排序分。

## 当前刷新状态

首页已增加[旧闻过滤与全来源事件聚类机制](docs/news-freshness.md)：按北京时间滚动 24 小时的原文发布时间筛选，未知时间不进入首页；先过滤再聚类排序。同一事件的跨媒体报道合并为一张主卡，展开看来源链接；不要求仍在热榜。来源热榜保留旧记录，采集时间与发布时间分开显示，浏览器也会自动隐藏过期内容。

“社会”只收国内社会民生，不再兜底国际、外交、军事新闻。“全部”是八个关注领域的合集，未归入领域的综合、军事、外交内容留在“来源热榜”；海外产业新闻仍可进入硬科技、大宗、金融等对应领域。

### 金十数据

“来源热榜”的“金十数据”来源标签固定放在“全部”后面；“刷新快讯”是该来源的操作按钮，位于金十内容区标题旁，不与来源标签混排，刷新状态也在该内容区展示。选择其他来源时不显示金十刷新操作；空缓存时按钮仍保留。不再用其他平台的快照日期筛掉金十新内容。首页公开市场快讯流中的普通、重要、热门快讯均进入候选，不按重要性或领域预筛。仅收公开可读内容，VIP、PLUS、登录/付费受限标记及解锁占位提示直接跳过，权限不明也跳过，不登录、不绕过限制。本范围不包含需要解锁的头条文章或会员栏目。

构建前运行 `python3 tools/home_source_pool.py --output-root output`，来源页与领域分类读取同一 `source-pool/jin10-all.json` 公开来源池；旧的未核验权限缓存不再复用。按原生发布时间保留最近24小时，部分抓取明确标注“尚未确认抓全”，不以数量宣称抓齐首页。失败保留已经核验的公开缓存及其原采集时间，没有缓存时显示空状态，入口不消失。

金十数据已经有前端实时刷新能力：

```text
/api/jin10/flash?refresh=1&limit=2000&pages=24
```

前端按钮通过 Cloudflare Pages `_worker.js` 在服务端读取公开普通流，普通快讯不因缺少 `important` 或 `hot` 被排除。最多24页、2000条、每轮30秒预算；不足24小时会返回 `window_complete=false`，不请求登录或受限历史。结果按时间倒序，重要/热门只作为显示标签，不作为准入条件。按钮更新当前来源页面；领域分类由下一轮来源池采集及页面构建同步更新。

实时来源页可显示金十官方 `/classify` 标签（该接口失败不影响快讯）；领域分类按本站内容规则聚类、归类，不以金十标签直接决定领域。纯军事/外交等未匹配关注领域的内容只在来源页保留。

### 其他热榜

“来源热榜”已增加固定的“界面新闻·快报”标签，读取[界面24小时滚动快报](https://www.jiemian.com/lists/4.html)及该页公开“加载更多”接口。只采公开标题、列表摘要、原文链接与原生 `data-time`/`publishtime`；不读取文章会员正文，不登录、不访问后台或编辑接口。会员、付费、登录受限标记和权限不明条目直接跳过。

来源池位于 `output/source-pool/jiemian-flash.json`，随现有后台采集/构建更新，也可用 `python3 tools/home_source_pool.py --output-root output --only jiemian-flash --force` 单独采集后重建页面。来源页按发布时间倒序，入口在没有数据时也保留；错误时沿用原采集时间的公开缓存，未抓全24小时明确提示。符合关注领域的快报进入同一分类候选池，与财联社、金十、微博等按事件合并，而不是重复占卡。

微博、今日头条、百度热搜、抖音、知乎、Bilibili、雪球等来源仍然是静态生成结果：

- 点击来源 tab 只是切换当前页面已有数据。
- 浏览器刷新只是重新请求已经部署的页面。
- 它们不会因为前端点击而重新抓源站。
- 更新依赖 GitHub Actions 跑 TrendRadar 并重新部署 Cloudflare Pages。

常规 workflow 仍是约 2 小时一次：

```yaml
schedule:
  - cron: "17 */2 * * *"
```

本地网站已经增加本机定时刷新：

```text
tools/refresh_local_hotlists.sh
tools/com.newshot.hotlists-refresh.plist.example
~/Library/LaunchAgents/com.newshot.hotlists-refresh.plist
```

本机 `launchd` 在周一至周五的北京时间 `06:45`、`09:45`、`12:45`、`15:45` 提交更新任务。周六、周日自动任务仍跳过；领域分类页“更新领域分类”按钮可手动提交同一持久队列。公共来源先更新并发布第一版分类与七赛道日报，随后复用微博后台检查到期账号，再发布第二版；未到期或失败的来源保留缓存。队列与分来源限频独立于网页运行，重复点击不重复采集。重建当前分类首页、七赛道日报与来源页，不运行旧长内容日报、完整脉络或飞书发送；采集容器仍关闭原始HTML报告输出，避免覆盖分类首页。详见[本地领域分类更新说明](docs/local-home-refresh.md)。

这条本机任务只影响 `http://127.0.0.1:8799/`，不会部署 Cloudflare；线上站点仍按原来的 GitHub Actions 频率更新。

本地 `8799` 预览由 `com.newshot.preview` 常驻服务托管，配置示例为 `tools/com.newshot.preview.plist.example`。服务在登录后自动启动，异常退出时由 `launchd` 重启。

### 财联社电报

财联社电报现在和金十一样支持前端实时刷新：

```text
GET /api/cls/telegraph?refresh=1&limit=120&pages=8&page_size=20
```

页面上的“刷新财联社”按钮通过 Cloudflare Pages `_worker.js` 在服务端实时抓取财联社，返回按财联社发布时间倒序排列的结构化数据。这个接口也可以直接供后续 `editor-briefing` 在四个批次生成前调用。

页面继续使用最新 120 条。供稿任务不要固定抓 120 条，应按时间窗口抓全：

```text
# 07:00 批次，覆盖过去 12 小时
GET /api/cls/telegraph?lookback_hours=12&limit=300&pages=8&page_size=50

# 10:00、13:00、16:00 批次，覆盖过去 6 小时
GET /api/cls/telegraph?lookback_hours=6&limit=300&pages=8&page_size=50
```

响应中的 `window_complete` 和 `truncated` 用于判断窗口是否抓全，不能在窗口不完整时静默进入排序。

静态生成链路继续保留作为首次加载和接口异常时的降级结果，由：

```text
tools/cls_telegraph.py
```

生成静态页面：

```text
output/hotlists/telegraph/index.html
```

它按财联社电报时间流抓取并按时间排序。用户点击刷新后，页面会用实时接口结果替换静态内容，不需要等待下一次 GitHub Actions 部署。

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

第一版 dry-run 已经实现：

```text
tools/editor_briefing.py
tools/briefing_config.py
tools/test_editor_briefing.py
```

本机手动生成：

```bash
cd /Users/wangyajing/skills/新闻/TrendRadar
python3 tools/editor_briefing.py
```

指定批次或回放时间：

```bash
python3 tools/editor_briefing.py --batch 1600
python3 tools/editor_briefing.py --batch 0700 --now 2026-07-10T06:55:00+08:00
```

本地预览：

```text
http://127.0.0.1:8799/briefing/
http://127.0.0.1:8799/briefing/latest
```

调试产物：

```text
output/briefing/debug/raw_docs.json
output/briefing/debug/atomic_items.json
output/briefing/debug/rejected_candidates.json
output/briefing/debug/events.json
output/briefing/debug/report.json
```

`editor_briefing.py` 仍只负责无副作用生成；正式发送由下面两个文件负责：

```text
tools/send_editor_briefing.py
tools/run_editor_briefing.sh
```

本机已经安装 `com.newshot.editor-briefing`，工作日北京时间 `06:55`、`09:55`、`12:55`、`15:55` 生成并发送对应的 `07:00`、`10:00`、`13:00`、`16:00` 批次。发送复用 `wechat-news-site/.env` 中旧早报机器人的 Webhook；飞书成功后写入 `output/briefing/sent_history.json`，按 `batch_id` 和已发 progress/标题防重。旧的 `com.newshot.daily-report` 已停用，避免继续发送单一公众号榜。

如果电脑睡眠导致日历任务延迟交付，目标整点后的 15 分钟内仍会补跑对应批次：`07:00-07:15`、`10:00-10:15`、`13:00-13:15`、`16:00-16:15`。已完成的 `batch_id` 会被幂等检查拦截，不会重复发送；超过窗口不自动补发。

`editor_briefing.py` 默认同样只在周一至周五运行。周末手动排查时可以显式执行：

```bash
python3 tools/editor_briefing.py --batch 1600 --force-weekend
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
tools/site_daily_archive.py
tools/refresh_local_hotlists.sh
tools/com.newshot.hotlists-refresh.plist.example
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
