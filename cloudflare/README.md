# Cloudflare 部署说明

更新时间：2026-07-10

线上站点：

```text
https://newshot-5jq.pages.dev/
```

当前使用 Cloudflare Pages 承载 `output/` 静态目录，同时通过 Pages Functions 形式的 `_worker.js` 提供少量实时接口。

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
npx wrangler pages deploy output --project-name newshot
```

当前 workflow 主要步骤：

```text
安装 Python / Node 依赖
-> 运行 TrendRadar 抓热榜
-> 生成行情数据
-> 生成财联社电报页
-> 生成 curated / hotlists 页面
-> 生成 /daily/ 停更占位页
-> 复制 _headers 和 _worker.js
-> 部署到 Cloudflare Pages
```

长内容日报已经暂时停更，当前生成的是：

```bash
python3 tools/daily_pause.py --output-root output
```

不是旧的 `daily_digest.py`。

## 2. 当前自动更新频率

当前 schedule 仍是约 2 小时一次：

```yaml
schedule:
  - cron: "17 */2 * * *"
```

这会刷新 `/hotlists/`、财联社电报静态页、行情数据和停更占位页。

注意：

- 微博、今日头条、抖音、知乎、Bilibili、雪球等来源仍依赖这个静态部署更新。
- 页面上点击 tab 不会抓源站，只会切换当前页面已有数据。
- 浏览器刷新不会触发 GitHub Actions。
- 只有金十数据有前端实时刷新接口。

如果要把普通热榜缩短到 30 分钟，需要改 workflow schedule，例如：

```yaml
schedule:
  - cron: "*/30 * * * *"
```

更推荐后续拆成两个 workflow：

- `hotlists-only`：每 15 到 30 分钟跑，只更新来源池和 `/hotlists/`。
- `editor-briefing`：每天北京时间 07:00、10:00、13:00、16:00 生成 `/briefing/latest.md`。

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
GET /api/jin10/flash?refresh=1&limit=80
```

用途：

- 服务端实时抓金十数据。
- 召回 `important=1` 或 `爆 / 沸 / 热 / 火` 的快讯。
- 使用金十官方 `/classify` 分类。
- 对明显地缘冲突新闻做分类修正，避免错误显示成美股、A股等市场类标签。
- 返回结果按发布时间倒序。

这个接口只刷新金十模块，不刷新其他来源。

## 4. 财联社电报

当前由静态生成脚本负责：

```bash
python3 tools/cls_telegraph.py --output-root output --limit 120 --pages 8
```

输出：

```text
output/hotlists/telegraph/index.html
output/hotlists/telegraph/data.json
```

线上地址：

```text
https://newshot-5jq.pages.dev/hotlists/telegraph/
```

当前没有前端实时刷新。后续可参考金十接口新增：

```text
GET /api/cls/telegraph
```

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
