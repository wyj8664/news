#!/usr/bin/env python3
"""Write a lightweight placeholder while long-form daily reports are paused."""

from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def render_page(now: datetime) -> str:
    timestamp = now.strftime("%Y-%m-%d %H:%M")
    escaped_timestamp = html.escape(timestamp)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>新闻日报暂时停更</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #fff;
      --text: #172033;
      --muted: #627086;
      --line: #e3e8f0;
      --blue: #185abc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.65;
    }}
    .wrap {{ max-width: 920px; margin: 0 auto; padding: 36px 18px; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 26px; }}
    nav a {{
      padding: 8px 11px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: #42506a;
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }}
    nav a.active {{ background: #20293a; color: #fff; border-color: #20293a; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 24px; }}
    h1 {{ margin: 0 0 10px; font-size: 28px; line-height: 1.25; }}
    p {{ margin: 10px 0; color: var(--muted); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }}
    .actions a {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid var(--line);
      color: var(--blue);
      background: #fff;
      text-decoration: none;
      font-weight: 750;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <nav>
      <a href="/curated/">领域分类</a>
      <a href="/hotlists/">来源热榜</a>
      <a href="/timeline/">热点脉络</a>
      <a class="active" href="/daily/">日报</a>
    </nav>
    <section class="panel">
      <h1>长内容日报暂时停更</h1>
      <p>长内容日报生成已暂停，不再随部署更新。</p>
      <p>当前优先维护来源热榜、实时快讯和后续的编辑供稿页。更新时间：{escaped_timestamp}</p>
      <div class="actions">
        <a href="/hotlists/">查看来源热榜</a>
        <a href="/curated/">查看领域分类</a>
      </div>
    </section>
  </div>
</body>
</html>
"""


def write_daily_pause(output_root: Path) -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    daily_dir = output_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "index.html").write_text(render_page(now), encoding="utf-8")
    (daily_dir / "latest.md").write_text(
        f"# 长内容日报暂时停更\n\n长内容日报生成已暂停。更新时间：{now:%Y-%m-%d %H:%M}\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    args = parser.parse_args()
    write_daily_pause(Path(args.output_root))


if __name__ == "__main__":
    main()
