#!/usr/bin/env python3
"""Build a deduped RSS feed for 央视新闻、央广网、新华社."""

from __future__ import annotations

import argparse
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin


FEEDS = [
    ("央视新闻", "https://rsshub.rssforever.com/cctv/news", "rss"),
    ("新华社", "https://www.news.cn/", "xinhua-html"),
    ("央广网", "https://news.cnr.cn/", "cnr-html"),
]
SOURCE_ORDER = [source for source, _, _ in FEEDS]

XINHUA_PAGES = [
    "https://www.news.cn/",
    "https://www.news.cn/politics/",
    "https://www.news.cn/world/",
    "https://www.news.cn/fortune/index.htm",
    "https://www.news.cn/tech/",
]


@dataclass
class Entry:
    title: str
    url: str
    source: str
    published: str = ""
    summary: str = ""


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TrendRadar official feed)",
            "Accept": "application/rss+xml, application/xml, text/html, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def looks_mojibake(value: str) -> bool:
    return len(re.findall(r"[ÃÂâåäæçèéœ‰º¤¥]", value)) >= 4


def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[#【】\[\]（）()《》“”\"'：:，,。.!！?？、\s\-_/|]+", "", title)
    title = re.sub(r"(图文|视频|详情|全文|最新)$", "", title)
    return title


def short_summary(value: str, limit: int = 500) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def date_from_url(url: str) -> date | None:
    match = re.search(r"/(20\d{2})(\d{2})(\d{2})/", url)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def pubdate_from_date(value: date | None) -> str:
    if not value:
        return rss_now()
    stamp = datetime.combine(value, time(12, 0), tzinfo=timezone.utc)
    return format_datetime(stamp)


def is_recent(value: date | None, max_age_days: int = 3) -> bool:
    if not value:
        return False
    return value >= date.today() - timedelta(days=max_age_days)


def parse_rss(source: str, url: str) -> list[Entry]:
    data = fetch_bytes(url)
    root = ET.fromstring(data)
    items: list[Entry] = []
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        pub_date = clean_text(item.findtext("pubDate"))
        summary = clean_text(item.findtext("description"))
        if title and link and not looks_mojibake(title):
            items.append(Entry(title=title, url=link, source=source, published=pub_date, summary=summary))
    return items


def extract_html_links(source: str, url: str, encoding: str, require_recent: bool = True) -> list[Entry]:
    data = fetch_bytes(url)
    text = data.decode(encoding, "ignore")
    items: list[Entry] = []
    for href, raw_title in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", text, re.S | re.I):
        title = clean_text(raw_title)
        if not title or len(title) < 6:
            continue
        if looks_mojibake(title):
            continue
        if "更多" in title or href.startswith("javascript"):
            continue
        full_url = urljoin(url, href)
        if not re.search(r"/20\d{6}/", full_url):
            continue
        article_date = date_from_url(full_url)
        if require_recent and not is_recent(article_date):
            continue
        items.append(Entry(title=title, url=full_url, source=source, published=pubdate_from_date(article_date)))
    return items


def parse_xinhua_html(source: str) -> list[Entry]:
    items: list[Entry] = []
    for url in XINHUA_PAGES:
        try:
            for entry in extract_html_links(source, url, "utf-8"):
                if "news.cn" in entry.url or "xinhuanet.com" in entry.url:
                    items.append(entry)
        except Exception as exc:
            print(f"[official-feed] {source}: {url} failed: {exc}")
    return dedupe(items, 300)


def parse_cnr_html(source: str, url: str) -> list[Entry]:
    items: list[Entry] = []
    for entry in extract_html_links(source, url, "gb18030"):
        if "cnr.cn" in entry.url and re.search(r"/20\d{6}/t20\d{6}_", entry.url):
            items.append(entry)
    return items


def rss_now() -> str:
    return format_datetime(datetime.now(timezone.utc))


def dedupe(entries: Iterable[Entry], limit: int) -> list[Entry]:
    buckets: dict[str, list[Entry]] = {source: [] for source in SOURCE_ORDER}
    for entry in entries:
        buckets.setdefault(entry.source, []).append(entry)

    seen: set[str] = set()
    result: list[Entry] = []

    # Round-robin the three sources so the merged standalone tag is balanced,
    # then dedupe by normalized title across all of them.
    while len(result) < limit and any(buckets.values()):
        for source in SOURCE_ORDER:
            bucket = buckets.get(source) or []
            while bucket:
                entry = bucket.pop(0)
                key = normalize_title(entry.title)
                if key and key not in seen:
                    seen.add(key)
                    result.append(entry)
                    break
            if len(result) >= limit:
                break
    return result


def build_feed(entries: list[Entry]) -> str:
    now = rss_now()
    parts = [
        '<?xml version="1.0" encoding="US-ASCII"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>三大家</title>",
        "<link>http://localhost:8080/feeds/official-major.xml</link>",
        "<description>央视新闻、央广网、新华社合并去重</description>",
        f"<lastBuildDate>{html.escape(now)}</lastBuildDate>",
    ]
    for entry in entries:
        title = html.escape(entry.title)
        link = html.escape(entry.url)
        summary = html.escape(short_summary(entry.summary) or f"来源：{entry.source}")
        pub_date = html.escape(entry.published or now)
        guid = html.escape(entry.url)
        author = html.escape(entry.source)
        parts.extend(
            [
                "<item>",
                f"<title>{title}</title>",
                f"<link>{link}</link>",
                f"<guid isPermaLink=\"true\">{guid}</guid>",
                f"<author>{author}</author>",
                f"<pubDate>{pub_date}</pubDate>",
                f"<description>{summary}</description>",
                "</item>",
            ]
        )
    parts.extend(["</channel>", "</rss>", ""])
    return "\n".join(parts)


def generate(output_root: Path, limit: int) -> Path:
    all_entries: list[Entry] = []
    for source, url, kind in FEEDS:
        try:
            if kind == "rss":
                entries = parse_rss(source, url)
            elif kind == "xinhua-html":
                entries = parse_xinhua_html(source)
            else:
                entries = parse_cnr_html(source, url)
            print(f"[official-feed] {source}: {len(entries)} items")
            all_entries.extend(entries)
        except Exception as exc:
            print(f"[official-feed] {source}: failed: {exc}")
    merged = dedupe(all_entries, limit)
    output_dir = output_root / "feeds"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "official-major.xml"
    output_path.write_bytes(build_feed(merged).encode("ascii", "xmlcharrefreplace"))
    print(f"[official-feed] wrote {output_path} with {len(merged)} items")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()
    generate(Path(args.output_root), args.limit)


if __name__ == "__main__":
    main()
