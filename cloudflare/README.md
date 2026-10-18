# Cloudflare 部署说明

更新时间：2026-10-18

线上站点：

```text
https://newshot-5jq.pages.dev/
```

当前使用 Cloudflare Pages 承载经过白名单打包的 `.site-deploy-dist/` 静态目录，同时通过 `_worker.js` 提供少量公开实时接口。禁止直接上传 `output/`，其中可能包含本地数据库、调试日志和飞书发送历史。

## 1. Pages 项目

GitHub Actions workflow：

```text
.github/workflows/cloudflare-pages.yml
```

需要的 GitHub Secrets：

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

部署命令：

```bash
python3 tools/public_site_bundle.py pack --output-root output --destination .site-deploy-dist
npx --yes wrangler@4.151.1 pages deploy .site-deploy-dist --project-name newshot --branch main
```

当前 workflow 主要步骤：

```text
安装 Python / Node 依赖
-> 运行 TrendRadar 抓热榜
-> 生成行情数据
-> 生成财联社电报页
-> 生成 curated / hotlists 页面及七赛道 /daily/ 日报
-> 复制 _headers 和 _worker.js
-> 部署到 Cloudflare Pages
```

七赛道日报生成入口：

```bash
python3 tools/curated_events.py --output-root output --lookback-days 7 --daily-top 50
```

不再调用 `daily_pause.py` 或旧 `daily_digest.py` 采集/排序入口；日报仅复用旧版 `daily_digest.render_html` 的日期归档与列表模板。数据沿用共享七赛道分类、稳定事件／进展模型，按北京时间原文自然日累计、每天综合排序最多50条，支持最近7天。私有候选账本 `.local-refresh/site-daily-output` 随工作流缓存保存，不作为公开资产；历史日报不执行24小时到期隐藏。热点脉络不再生成，旧入口跳转日报。

## 2. 当前自动更新频率

常规 schedule 仍是约 2 小时一次：

```yaml
schedule:
  - cron: "17 */2 * * *"
```

这会更新领域分类、七日七赛道日报、来源热榜、公开快讯页和行情。微博只读取已有公开缓存，不在 GitHub 登录微博或调用本机采集器。

手动首次发布可选择 `deploy_snapshot=true`，使用 `data/site-public-seed.tar.gz` 中已验收的网页快照，不额外采集。打包器仅允许明确的 HTML/JS/JSON/Markdown 路径，校验哈希，拒绝数据库、凭证字段、路径穿越和符号链接。部署前用现有密钥只读核验原 Pages 项目及 `newshot-5jq.pages.dev` 域名，不再自动创建项目。

云端首次从已公开的首页与日报 JSON 导入稳定ID，不上传本机事件数据库；之后由 Actions 保存云端自己生成的身份与日归档缓存。冷启动时保留已发布的历史日报原分数与ID，不用不完整回填替换既有日期。公开微博接口优先采用较新且账号完整的快照，旧KV不会覆盖新增加的关注账号。

当前四个供稿批次前的额外刷新只在本机执行：

```text
tools/refresh_local_hotlists.sh
~/Library/LaunchAgents/com.newshot.hotlists-refresh.plist
```

北京时间运行时间：

```text
06:45、09:45、12:45、15:45
```

它更新 `http://127.0.0.1:8799/` 的本地数据，不会部署 Cloudflare。线上仍只有 `cloudflare-pages.yml` 的约 2 小时一次常规部署。

注意：

- 微博、今日头条、抖音、知乎、Bilibili、雪球等来源仍依赖这个静态部署更新。
- 页面上点击 tab 不会抓源站，只会切换当前页面已有数据。
- 浏览器刷新不会触发 GitHub Actions。
- 金十和财联社电报有前端实时刷新接口；其他平台热榜依赖部署更新。

如果要把普通热榜缩短到 30 分钟，需要改 workflow schedule，例如：

```yaml
schedule:
  - cron: "*/30 * * * *"
```

当前分工：

- `cloudflare-pages.yml`：保留约 2 小时一次的常规部署。
- 本机 `com.newshot.hotlists-refresh`：四个批次前 15 分钟刷新本地 TrendRadar 热榜，只生成来源热榜、行情和财联社静态降级页，不运行 `daily_digest.py` 或 AI 分析。
- `editor-briefing`：下一阶段在本机四个批次前读取公众号和热榜、补抓实时源并生成飞书供稿。

## 3. Pages `_worker.js`

当前实时接口文件：

```text
cloudflare/pages/_worker.js
```

部署时会复制到：

```text
output/_worker.js
```

当前已支持：

```text
GET /api/jin10/flash?refresh=1&limit=2000&pages=24
GET /api/cls/telegraph?refresh=1&limit=120&pages=8&page_size=20
GET /api/cls/telegraph?lookback_hours=6&limit=300&pages=8&page_size=50
```

金十接口用途：

- 服务端读取金十首页公开市场快讯流，普通、重要、热门均收录，不要求 `important` 或 `hot`。
- VIP、PLUS、登录/付费受限内容及解锁提示直接跳过；权限未核验也不收录，不尝试登录或绕过限制。
- 实时来源页可使用金十官方 `/classify` 标签，其失败不阻断快讯；领域首页使用本站聚类和分类规则。
- 对明显地缘冲突新闻做分类修正，避免错误显示成美股、A股等市场类标签。
- 返回结果按发布时间倒序。
- 最多24页、2000条、每轮30秒。返回 `public_only`、`window_complete`、`truncated`、`skipped_restricted`；空页或部分请求失败不宣称抓全24小时。
- 构建读取 `source-pool/jin10-all.json`，来源页与领域分类共用公开候选池。页面刷新按钮只更新当前来源页，分类首页由下一轮采集/构建同步。

财联社接口用途：

- 服务端实时抓取财联社电报时间流。
- 支持 `limit`、`pages` 和 `page_size` 控制抓取范围，最大返回 400 条。
- 支持 `lookback_hours` 或 `since` 按时间窗口向前翻页；`lookback_hours` 最长 48 小时。
- 返回统一的标题、正文、链接、发布时间、级别、主题和股票标签。
- 返回结果按财联社发布时间倒序。
- 返回 `window_complete`、`truncated`、`pages_fetched` 等完整性字段。
- `/hotlists/telegraph/` 的刷新按钮会用实时结果替换页面现有静态内容。

两个接口都禁用响应缓存；点击按钮或供稿任务调用时会重新访问对应源站。

## 4. 财联社电报

当前由静态生成脚本负责：

```bash
python3 tools/cls_telegraph.py --output-root output --limit 120 --pages 8
```

输出：

```text
output/hotlists/telegraph/index.html
output/hotlists/telegraph/latest.json
```

线上地址：

```text
https://newshot-5jq.pages.dev/hotlists/telegraph/
```

静态结果用于页面首次加载和实时接口异常时降级。当前已经支持前端实时刷新：

```text
GET /api/cls/telegraph?refresh=1&limit=120&pages=8&page_size=20
```

`editor-briefing` 也应在 `07:00`、`10:00`、`13:00`、`16:00` 四个批次生成前直接调用该接口或复用同一抓取模块，不再依赖两小时一次的静态结果。

推荐调用：

```text
07:00：/api/cls/telegraph?lookback_hours=12&limit=300&pages=8&page_size=50
其他批次：/api/cls/telegraph?lookback_hours=6&limit=300&pages=8&page_size=50
```

前端页面仍只请求最新 120 条；300 条上限用于供稿召回，不代表页面必须展示 300 条。

## 5. 旧 Worker + KV

目录：

```text
cloudflare/worker/
```

这是此前用于高频行情 JSON 的独立 Worker + KV 方案。它仍可作为以后实时行情或缓存层的参考，但当前金十刷新走的是 Pages `_worker.js`，不是这个独立 Worker。

如果后续重新启用独立 Worker，需要：

```bash
cd cloudflare/worker
npx wrangler kv namespace create NEWSHOT_KV
cp wrangler.toml.example wrangler.toml
npx wrangler deploy
```

## 6. 编辑供稿后续

`/hotlists/` 只是来源看板。编辑群要用的目标产物是：

```text
output/briefing/latest.md
output/briefing/latest.html
```

完整设计见：

```text
docs/editor-briefing-system.md
```
