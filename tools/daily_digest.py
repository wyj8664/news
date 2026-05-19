#!/usr/bin/env python3
"""Generate a daily top-news digest from TrendRadar's local SQLite data."""

from __future__ import annotations

import argparse
import html
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


SOURCE_WEIGHTS = {
    "thepaper": 16,
    "wallstreetcn-hot": 15,
    "cls-hot": 15,
    "sina-finance": 13,
    "ifeng": 12,
    "baidu": 12,
    "toutiao": 11,
    "weibo": 11,
    "zhihu": 10,
    "xueqiu-hotstock": 9,
    "bilibili-hot-search": 8,
    "douyin": 8,
}

IMPORTANT_TERMS = (
    "国务院", "外交部", "央行", "证监会", "最高法", "最高检", "国家统计局",
    "美国", "俄罗斯", "乌克兰", "以色列", "中东", "欧盟", "日本", "韩国",
    "关税", "利率", "通胀", "股市", "A股", "港股", "美股", "人民币",
    "AI", "芯片", "新能源", "机器人", "事故", "通报", "调查", "地震",
)


@dataclass
class Item:
    title: str
    url: str
    kind: str
    source_id: str
    source_name: str
    score: float
    first_time: str = ""
    last_time: str = ""
    count: int = 1
    best_rank: int | None = None
    worst_rank: int | None = None
    summary: str = ""
    published_at: str = ""


@dataclass
class Cluster:
    title: str
    url: str
    kind: str
    score: float
    items: list[Item] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    first_time: str = ""
    last_time: str = ""
    count: int = 0
    best_rank: int | None = None
    worst_rank: int | None = None
    summary: str = ""
    published_at: str = ""

    def add(self, item: Item) -> None:
        self.items.append(item)
        self.sources[item.source_id] = item.source_name
        self.count += item.count
        self.score = max(self.score, item.score)
        if item.score >= max((i.score for i in self.items), default=item.score):
            self.title = item.title
            self.url = item.url or self.url
            self.summary = item.summary or self.summary
            self.published_at = item.published_at or self.published_at
        self.first_time = min_time(self.first_time, item.first_time)
        self.last_time = max_time(self.last_time, item.last_time)
        self.best_rank = min_rank(self.best_rank, item.best_rank)
        self.worst_rank = max_rank(self.worst_rank, item.worst_rank)

    @property
    def final_score(self) -> float:
        source_bonus = 16 * max(0, len(self.sources) - 1)
        persistence_bonus = min(24, math.log1p(self.count) * 7)
        term_bonus = term_score(self.title)
        return self.score + source_bonus + persistence_bonus + term_bonus


@dataclass
class ArchiveEntry:
    date: str
    title: str
    count: int


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[#【】\[\]（）()《》“”\"'：:，,。.!！?？、\s\-_/|]+", "", title)
    title = re.sub(r"(热搜|最新|突发|刚刚|详情|全文)$", "", title)
    return title


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) >= 8 and short in long:
        return True
    if min(len(a), len(b)) < 10:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.78


def term_score(text: str) -> float:
    return min(20, sum(3 for term in IMPORTANT_TERMS if term.lower() in text.lower()))


def parse_rank(value: object) -> int | None:
    try:
        rank = int(value)
        return rank if rank > 0 else None
    except (TypeError, ValueError):
        return None


def min_rank(a: int | None, b: int | None) -> int | None:
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else None


def max_rank(a: int | None, b: int | None) -> int | None:
    values = [v for v in (a, b) if v is not None]
    return max(values) if values else None


def min_time(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return a if normalize_time(a) <= normalize_time(b) else b


def max_time(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return a if normalize_time(a) >= normalize_time(b) else b


def normalize_time(value: str) -> str:
    return value.replace("-", ":") if value else ""


def display_time(value: str) -> str:
    return normalize_time(value) or "-"


def strip_summary(summary: str, limit: int = 120) -> str:
    summary = re.sub(r"\s+", " ", summary or "").strip()
    summary = re.sub(r"（来源：.*?）", "", summary).strip()
    if len(summary) > limit:
        return summary[:limit].rstrip() + "..."
    return summary


def connect_if_exists(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def load_hotlist_items(db_path: Path) -> list[Item]:
    conn = connect_if_exists(db_path)
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT
            n.id,
            n.title,
            n.platform_id,
            COALESCE(p.name, n.platform_id) AS source_name,
            n.rank,
            n.url,
            n.first_crawl_time,
            n.last_crawl_time,
            n.crawl_count,
            MIN(r.rank) AS best_rank,
            MAX(r.rank) AS worst_rank,
            COUNT(r.id) AS rank_points
        FROM news_items n
        LEFT JOIN platforms p ON p.id = n.platform_id
        LEFT JOIN rank_history r ON r.news_item_id = n.id
        GROUP BY n.id
        """
    ).fetchall()
    conn.close()

    items: list[Item] = []
    for row in rows:
        best = parse_rank(row["best_rank"]) or parse_rank(row["rank"])
        worst = parse_rank(row["worst_rank"]) or parse_rank(row["rank"])
        count = int(row["crawl_count"] or row["rank_points"] or 1)
        rank_score = 0.0 if best is None else 68 / ((best + 2) ** 0.72)
        source_weight = SOURCE_WEIGHTS.get(row["platform_id"], 8)
        score = rank_score + source_weight + min(28, math.log1p(count) * 8)
        items.append(
            Item(
                title=row["title"],
                url=row["url"] or "",
                kind="hotlist",
                source_id=row["platform_id"],
                source_name=row["source_name"],
                score=score,
                first_time=row["first_crawl_time"] or "",
                last_time=row["last_crawl_time"] or "",
                count=count,
                best_rank=best,
                worst_rank=worst,
            )
        )
    return items


def load_rss_items(db_path: Path, target_date: str) -> list[Item]:
    conn = connect_if_exists(db_path)
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT
            r.title,
            r.feed_id,
            COALESCE(f.name, r.feed_id) AS source_name,
            r.url,
            r.published_at,
            r.summary,
            r.author,
            r.first_crawl_time,
            r.last_crawl_time,
            r.crawl_count
        FROM rss_items r
        LEFT JOIN rss_feeds f ON f.id = r.feed_id
        """
    ).fetchall()
    conn.close()

    items: list[Item] = []
    for row in rows:
        published = row["published_at"] or ""
        same_day_bonus = 8 if target_date in published else 0
        count = int(row["crawl_count"] or 1)
        score = 20 + SOURCE_WEIGHTS.get(row["feed_id"], 8) + same_day_bonus + term_score(row["title"])
        items.append(
            Item(
                title=row["title"],
                url=row["url"] or "",
                kind="rss",
                source_id=row["feed_id"],
                source_name=row["source_name"],
                score=score,
                first_time=row["first_crawl_time"] or "",
                last_time=row["last_crawl_time"] or "",
                count=count,
                summary=strip_summary(row["summary"] or ""),
                published_at=published,
            )
        )
    return items


def cluster_items(items: Iterable[Item]) -> list[Cluster]:
    clusters: list[Cluster] = []
    keys: list[str] = []
    for item in sorted(items, key=lambda it: it.score, reverse=True):
        key = normalize_title(item.title)
        chosen = None
        for idx, existing in enumerate(keys):
            if similar(key, existing):
                chosen = idx
                break
        if chosen is None:
            cluster = Cluster(
                title=item.title,
                url=item.url,
                kind=item.kind,
                score=item.score,
            )
            cluster.add(item)
            clusters.append(cluster)
            keys.append(key)
        else:
            clusters[chosen].add(item)
            keys[chosen] = max((keys[chosen], key), key=len)
    return clusters


def rank_label(cluster: Cluster) -> str:
    if cluster.best_rank is None:
        return "RSS"
    if cluster.worst_rank and cluster.worst_rank != cluster.best_rank:
        return f"{cluster.best_rank}-{cluster.worst_rank}"
    return str(cluster.best_rank)


def source_label(cluster: Cluster) -> str:
    return " / ".join(cluster.sources.values())


def month_label(date: str) -> str:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return f"{dt.year} 年 {dt.month} 月"
    except ValueError:
        return date[:7]


def day_label(date: str) -> str:
    try:
        return str(datetime.strptime(date, "%Y-%m-%d").day)
    except ValueError:
        return date[-2:]


def render_archive(entries: list[ArchiveEntry], active_date: str) -> str:
    if not entries:
        return ""

    latest = entries[0]
    groups: list[str] = []
    current_month = ""
    for entry in entries:
        month = month_label(entry.date)
        if month != current_month:
            if current_month:
                groups.append("</div>")
            current_month = month
            month_count = sum(1 for e in entries if month_label(e.date) == month)
            groups.append(
                f'<div class="month"><div class="month-head"><span>{html.escape(month)}</span>'
                f'<span>{month_count}</span></div>'
            )
        active = " active" if entry.date == active_date else ""
        title = html.escape(entry.title or "日报生成中")
        groups.append(
            f'<a class="archive-row{active}" href="/daily/{html.escape(entry.date)}.html">'
            f'<span class="day">{html.escape(day_label(entry.date))} 日</span>'
            f'<span class="archive-title">{title}</span>'
            f'</a>'
        )
    if current_month:
        groups.append("</div>")

    latest_active = " active" if latest.date == active_date else ""
    return f"""
    <aside class="archive">
      <div class="brand">NEWS<span>HOT</span></div>
      <a class="latest{latest_active}" href="/daily/">
        <strong>最新一期</strong>
        <span>{html.escape(latest.date)}</span>
      </a>
      {''.join(groups)}
    </aside>
    """


def render_html(
    clusters: list[Cluster],
    date: str,
    generated_at: str,
    top: int,
    archive_entries: list[ArchiveEntry],
) -> str:
    rows = []
    for idx, cluster in enumerate(clusters[:top], 1):
        title = html.escape(cluster.title)
        url = html.escape(cluster.url)
        title_html = f'<a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
        summary = html.escape(cluster.summary)
        summary_html = f"<p>{summary}</p>" if summary else ""
        rank = html.escape(rank_label(cluster))
        sources = html.escape(source_label(cluster))
        time_span = f"{display_time(cluster.first_time)}~{display_time(cluster.last_time)}"
        meta = html.escape(f"{sources} · {time_span} · {cluster.count}次")
        rows.append(
            f"""
            <article class="item">
              <div class="rank">{idx}</div>
              <div class="body">
                <div class="topline"><span class="badge">{rank}</span><span>{meta}</span></div>
                <h2>{title_html}</h2>
                {summary_html}
              </div>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(date)} 新闻日报 Top {top}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #637083;
      --line: #e7ebf1;
      --accent: #d83232;
      --blue: #185abc;
    }}
    * {{ box-sizing: border-box; }}
	    body {{
	      margin: 0;
	      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
	      background: var(--bg);
	      color: var(--text);
	    }}
	    a {{ text-decoration: none; }}
	    .global-topbar {{
	      position: sticky;
	      top: 0;
	      z-index: 30;
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      gap: 18px;
	      max-width: 1180px;
	      margin: 0 auto;
	      padding: 14px 18px 16px;
	      border-bottom: 1px solid var(--line);
	      background: rgba(247, 248, 251, .96);
	      backdrop-filter: blur(12px);
	    }}
	    .global-left {{
	      display: flex;
	      align-items: center;
	      min-width: 0;
	    }}
	    .global-brand {{
	      color: #20293a;
	      font-size: 22px;
	      font-weight: 850;
	      letter-spacing: 0;
	      white-space: nowrap;
	    }}
	    .global-brand span {{ color: #0d8fc8; }}
	    .back-link,
	    .global-nav a {{
	      display: inline-flex;
	      align-items: center;
	      justify-content: center;
	      min-height: 34px;
	      padding: 7px 10px;
	      border: 1px solid var(--line);
	      border-radius: 6px;
	      background: #fff;
	      color: #435066;
	      font-size: 13px;
	      font-weight: 750;
	      white-space: nowrap;
	    }}
	    .global-nav {{
	      display: flex;
	      gap: 8px;
	      flex-wrap: wrap;
	      justify-content: flex-end;
	    }}
	    .global-nav a.active {{
	      color: #fff;
	      background: #20293a;
	      border-color: #20293a;
	    }}
	    .shell {{
	      max-width: 1180px;
	      margin: 0 auto;
	      padding: 0 18px 56px;
	      display: grid;
	      grid-template-columns: 250px minmax(0, 1fr);
	      gap: 48px;
	      align-items: start;
	    }}
	    .archive {{
	      position: sticky;
	      top: 78px;
	      max-height: calc(100vh - 96px);
	      overflow-y: auto;
	      padding: 24px 0 0;
	      background: transparent;
	    }}
	    .brand {{
	      height: 54px;
	      display: flex;
	      align-items: center;
	      justify-content: center;
	      gap: 7px;
	      margin-bottom: 18px;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      color: #39445a;
	      font-size: 20px;
	      font-weight: 800;
	      letter-spacing: 1px;
	    }}
	    .brand span {{
	      color: #0d8fc8;
	    }}
	    .latest {{
	      display: block;
	      padding: 14px 16px;
	      margin-bottom: 18px;
	      border: 1px solid #10a477;
	      border-radius: 6px;
	      color: #10815f;
	      text-decoration: none;
	      background: #ffffff;
	    }}
	    .latest span {{
	      display: block;
	      margin-top: 8px;
	      font-size: 13px;
	      color: #62b69c;
	    }}
	    .month {{
	      margin-bottom: 16px;
	      padding: 12px;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      background: var(--panel);
	    }}
	    .month-head {{
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      padding: 0 2px 8px;
	      color: #263144;
	      font-weight: 700;
	    }}
	    .month-head span:last-child {{
	      color: #8ba0b8;
	      font-size: 12px;
	      font-weight: 600;
	    }}
	    .archive-row {{
	      display: grid;
	      grid-template-columns: 44px 1fr;
	      gap: 8px;
	      padding: 9px 6px;
	      border-radius: 6px;
	      color: #6b778b;
	      text-decoration: none;
	    }}
	    .archive-row:hover,
	    .archive-row.active,
	    .latest.active {{
	      background: #e9fbf3;
	    }}
	    .day {{
	      color: #7ea2c0;
	      font-size: 12px;
	      white-space: nowrap;
	    }}
	    .archive-title {{
	      overflow: hidden;
	      display: -webkit-box;
	      -webkit-box-orient: vertical;
	      -webkit-line-clamp: 2;
	      font-size: 12px;
	      line-height: 1.45;
	    }}
	    main {{
	      max-width: none;
	      margin: 0;
	      padding: 28px 0 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-end;
      padding: 8px 0 18px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    .nav a {{
      color: var(--blue);
      text-decoration: none;
      font-size: 14px;
      white-space: nowrap;
    }}
    .item {{
      display: grid;
      grid-template-columns: 44px 1fr;
      gap: 14px;
      padding: 18px 0;
      border-bottom: 1px solid var(--line);
    }}
    .rank {{
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: var(--text);
      color: white;
      display: grid;
      place-items: center;
      font-weight: 700;
      font-size: 15px;
    }}
    .topline {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .badge {{
      display: inline-flex;
      min-width: 28px;
      height: 22px;
      align-items: center;
      justify-content: center;
      padding: 0 7px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
      font-size: 12px;
    }}
    h2 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.45;
      letter-spacing: 0;
    }}
    h2 a {{
      color: var(--blue);
      text-decoration: none;
    }}
    p {{
      margin: 8px 0 0;
      color: #4c596b;
      font-size: 14px;
      line-height: 1.65;
    }}
    footer {{
      color: var(--muted);
      font-size: 13px;
      padding-top: 20px;
    }}
	    @media (max-width: 640px) {{
	      .shell {{
	        display: block;
	        padding: 0 12px 44px;
	      }}
	      .archive {{
	        position: static;
	        max-height: 310px;
	        border-bottom: 1px solid var(--line);
	        padding: 16px 0;
	      }}
	      main {{ padding: 22px 0 44px; }}
	      header {{ display: block; }}
	      .nav {{ margin-top: 12px; }}
	      .global-topbar {{ position: static; align-items: flex-start; flex-direction: column; }}
	      .global-nav {{ width: 100%; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px; }}
	      h1 {{ font-size: 24px; }}
      .item {{ grid-template-columns: 36px 1fr; gap: 10px; }}
      .rank {{ width: 30px; height: 30px; font-size: 14px; }}
    }}
  </style>
</head>
<body>
  <div class="global-topbar">
    <div class="global-left">
      <a class="global-brand" href="/">NEWS<span>HOT</span></a>
    </div>
    <nav class="global-nav">
      <a class="back-link" href="/" onclick="history.back(); return false;">返回</a>
      <a href="/">领域分类</a>
      <a class="active" href="/daily/">日报</a>
      <a href="/timeline/">热点脉络</a>
      <a href="/hotlists/">来源热榜</a>
    </nav>
  </div>
  <div class="shell">
    {render_archive(archive_entries, date)}
    <main>
      <header>
        <div>
          <h1>{html.escape(date)} 新闻日报 Top {top}</h1>
          <div class="sub">基于热榜排名、在榜次数、跨平台覆盖和 RSS 正式新闻源自动排序。生成时间：{html.escape(generated_at)}</div>
        </div>
      </header>
      {''.join(rows)}
      <footer>日报为自动聚合排序结果，用于快速浏览；重要事实请以原始媒体和官方来源为准。</footer>
    </main>
  </div>
</body>
</html>
"""


def render_markdown(clusters: list[Cluster], date: str, top: int) -> str:
    lines = [f"# {date} 新闻日报 Top {top}", ""]
    for idx, cluster in enumerate(clusters[:top], 1):
        sources = source_label(cluster)
        span = f"{display_time(cluster.first_time)}~{display_time(cluster.last_time)}"
        lines.append(f"{idx}. {cluster.title}")
        lines.append(f"   来源：{sources}；排名：{rank_label(cluster)}；时间：{span}；出现：{cluster.count}次")
        if cluster.url:
            lines.append(f"   链接：{cluster.url}")
        if cluster.summary:
            lines.append(f"   摘要：{cluster.summary}")
        lines.append("")
    return "\n".join(lines)


def rank_clusters(output_root: Path, date: str) -> list[Cluster]:
    news_db = output_root / "news" / f"{date}.db"
    rss_db = output_root / "rss" / f"{date}.db"
    items = load_hotlist_items(news_db) + load_rss_items(rss_db, date)
    return sorted(cluster_items(items), key=lambda cl: cl.final_score, reverse=True)


def available_dates(output_root: Path) -> list[str]:
    dates = {path.stem for path in (output_root / "news").glob("*.db")}
    dates.update(path.stem for path in (output_root / "rss").glob("*.db"))
    return sorted(dates, reverse=True)


def build_archive(output_root: Path, top: int) -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    for date in available_dates(output_root):
        clusters = rank_clusters(output_root, date)
        title = clusters[0].title if clusters else ""
        entries.append(ArchiveEntry(date=date, title=title, count=min(top, len(clusters))))
    return entries


def generate(
    output_root: Path,
    date: str,
    top: int,
    archive_entries: list[ArchiveEntry] | None = None,
) -> tuple[Path, Path, int]:
    clusters = rank_clusters(output_root, date)

    daily_dir = output_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if archive_entries is None:
        archive_entries = build_archive(output_root, top)
    html_content = render_html(clusters, date, generated_at, top, archive_entries)
    md_content = render_markdown(clusters, date, top)

    date_html = daily_dir / f"{date}.html"
    latest_html = daily_dir / "index.html"
    date_md = daily_dir / f"{date}.md"
    latest_md = daily_dir / "latest.md"

    date_html.write_text(html_content, encoding="utf-8")
    if archive_entries and date == archive_entries[0].date:
        latest_html.write_text(html_content, encoding="utf-8")
    date_md.write_text(md_content, encoding="utf-8")
    if archive_entries and date == archive_entries[0].date:
        latest_md.write_text(md_content, encoding="utf-8")
    return latest_html, latest_md, len(clusters)


def generate_all(output_root: Path, top: int) -> tuple[Path, Path, int]:
    archive_entries = build_archive(output_root, top)
    total = 0
    latest_html = output_root / "daily" / "index.html"
    latest_md = output_root / "daily" / "latest.md"
    for entry in reversed(archive_entries):
        latest_html, latest_md, total = generate(output_root, entry.date, top, archive_entries)
    return latest_html, latest_md, total


def today(tz_name: str) -> str:
    if ZoneInfo is None:
        return datetime.now().strftime("%Y-%m-%d")
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--date", default="")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--all", action="store_true", help="Generate pages for every local daily database")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    if args.all:
        latest_html, latest_md, total = generate_all(output_root, args.top)
    else:
        date = args.date or today(args.timezone)
        latest_html, latest_md, total = generate(output_root, date, args.top)
    print(f"[daily] generated {latest_html} and {latest_md} from {total} clustered items")


if __name__ == "__main__":
    main()
