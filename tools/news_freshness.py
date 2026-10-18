"""Fail-closed publication-time checks for the NEWSHOT current-news homepage.

Publisher metadata is cached separately from upstream crawl databases. A crawl,
cache refresh, updated-time field, or newly observed hotlist rank is never a
substitute for an article's publication time.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

CHINA = ZoneInfo("Asia/Shanghai")
WINDOW_HOURS = 24
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_FETCHES = 12
MAX_DOCUMENT_BYTES = 2_000_000
PUBLISH_META = {"article:published_time", "og:published_time", "datepublished", "pubdate",
                "publishdate", "publish_time", "publishtime", "parsely-pub-date"}
ARTICLE_TYPES = {"article", "newsarticle", "blogposting", "reportagenewsarticle"}
ARTICLE_PATHS = {
    "wallstreetcn.com": r"/articles/\d+/?$",
    "www.thepaper.cn": r"/newsDetail_forward_\d+/?$",
    "m.thepaper.cn": r"/newsDetail_forward_\d+/?$",
    "www.cls.cn": r"/detail/\d+/?$",
    "news.ifeng.com": r"/c/[A-Za-z0-9]+/?$",
    "finance.ifeng.com": r"/c/[A-Za-z0-9]+/?$",
    "finance.sina.com.cn": r"/.*\.s?html$",
    "news.sina.com.cn": r"/.*\.s?html$",
}


def china_time(value: datetime) -> datetime:
    return value.replace(tzinfo=CHINA) if value.tzinfo is None else value.astimezone(CHINA)


def parse_publication_time(value: object) -> datetime | None:
    """Accept explicit full dates/epochs only; never infer a year or use 'now'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return china_time(value)
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}", text):
            number = float(text)
            result = datetime.fromtimestamp(number / 1000 if number > 1e12 else number, CHINA)
        elif re.match(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text):
            text = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
            if not re.search(r"\d{1,2}:\d{2}", text):
                return None
            result = china_time(datetime.fromisoformat(text.replace("Z", "+00:00")))
        else:
            # RFC 2822 dates appear in RSS, but partial display dates do not qualify.
            if not re.search(r"\b\d{4}\b", text) or not re.search(r"\d{1,2}:\d{2}", text):
                return None
            result = china_time(parsedate_to_datetime(text))
        return result if 2000 <= result.year <= 2100 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def safe_article_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        pattern = ARTICLE_PATHS.get((parsed.hostname or "").lower())
        return bool(parsed.scheme == "https" and not parsed.username and not parsed.password
                    and parsed.port in (None, 443) and pattern and re.fullmatch(pattern, parsed.path))
    except ValueError:
        return False


class PublicationParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: list[tuple[str, str]] = []
        self.ld_scripts: list[str] = []
        self.scripts: list[str] = []
        self._script: list[str] | None = None
        self._is_ld = False
        self._saw_body = False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value or "" for key, value in attrs}
        if tag == "body":
            self._saw_body = True
        if tag == "meta" and not self._saw_body:
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            if key in PUBLISH_META:
                self.meta.append((key, attrs.get("content", "")))
        if tag == "script":
            self._script = []
            self._is_ld = attrs.get("type", "").lower() == "application/ld+json"

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            body = "".join(self._script)
            self.scripts.append(body)
            if self._is_ld:
                self.ld_scripts.append(body)
            self._script = None


def extract_publication(document: str, url: str = "") -> tuple[datetime | None, str]:
    parser = PublicationParser()
    parser.feed(document)
    for key, value in parser.meta:
        if parsed := parse_publication_time(value):
            return parsed, "publisher:meta:" + key
    if urllib.parse.urlsplit(url).hostname == "wallstreetcn.com" and safe_article_url(url):
        expected_id = urllib.parse.urlsplit(url).path.strip("/").split("/")[-1]
        for script in parser.scripts:
            match = re.match(r"\s*(?:window\.)?__SSR__\s*=\s*", script)
            if not match:
                continue
            try:
                # Decode data only; never execute publisher JavaScript.
                state, _ = json.JSONDecoder().raw_decode(script[match.end():])
                data = state["state"]["default"]["children"]["default"]["data"]
                article = data["article"]
                if str(data.get("id")) != expected_id or str(article.get("id", expected_id)) != expected_id:
                    continue
                if parsed := parse_publication_time(article.get("display_time")):
                    return parsed, "publisher:wallstreetcn:article.display_time"
            except (KeyError, TypeError, ValueError, RecursionError):
                continue
    candidates = []

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and ARTICLE_TYPES.intersection(str(value).lower() for value in types):
                if parsed := parse_publication_time(node.get("datePublished")):
                    candidates.append(parsed)
            if "@graph" in node:
                visit(node["@graph"])

    for script in parser.ld_scripts:
        try:
            visit(json.loads(script))
        except (ValueError, TypeError, RecursionError):
            continue
    # Multiple article entities with different dates cannot establish the page's own time.
    if candidates and len(set(candidates)) == 1:
        return candidates[0], "publisher:jsonld:datePublished"
    return None, ""


class PublisherRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not safe_article_url(newurl):
            raise ValueError("redirect outside supported article sources")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_publication(url: str) -> tuple[datetime | None, str]:
    if not safe_article_url(url):
        return None, ""
    opener = urllib.request.build_opener(PublisherRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "NEWSHOT-PublicationCheck/1.0",
                                                  "Accept": "text/html,application/xhtml+xml"})
    try:
        with opener.open(request, timeout=5) as response:
            if not safe_article_url(response.url):
                return None, ""
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None, ""
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
            if len(raw) > MAX_DOCUMENT_BYTES:
                return None, ""
            encoding = response.headers.get_content_charset() or "utf-8"
            return extract_publication(raw.decode(encoding, errors="replace"), url)
    except Exception:
        # Captchas, paywalls and network errors are unknown, never a current timestamp.
        return None, ""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)


def load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        items = data.get("items", {}) if data.get("schema_version") == 1 else {}
        return {key: value for key, value in items.items() if isinstance(value, dict)} if isinstance(items, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def observed_at(item) -> datetime | None:
    value = getattr(item, "collected_dt", None)
    if value is None and item.kind in {"hotlist", "rss"}:
        value = item.last_dt
    return china_time(value) if isinstance(value, datetime) else None


def select_home_items(items: list, output_root: Path, *, now: datetime | None = None,
                      window_hours: int = WINDOW_HOURS, max_fetches: int = MAX_FETCHES) -> tuple[list, dict]:
    now = china_time(now or datetime.now(CHINA))
    cutoff = now - timedelta(hours=window_hours)
    cache_path = output_root / "freshness" / "publication-times.json"
    cache = load_cache(cache_path)
    latest_by_source = {}
    for item in items:
        observed = observed_at(item)
        if item.kind == "hotlist" and observed is not None and observed <= now + FUTURE_TOLERANCE:
            latest_by_source[item.source_id] = max(latest_by_source.get(item.source_id, observed), observed)
    pending = []
    for item in sorted(items, key=lambda value: value.score, reverse=True):
        observed = observed_at(item)
        if item.published_dt is not None or not safe_article_url(item.url):
            continue
        if observed is None or observed < cutoff or observed > now + FUTURE_TOLERANCE:
            continue
        record = cache.get(item.url, {})
        if parse_publication_time(record.get("published_at")) and record.get("evidence"):
            continue
        checked = parse_publication_time(record.get("checked_at"))
        if checked is not None and timedelta(0) <= now - checked < timedelta(hours=6):
            continue
        if item.url not in pending:
            pending.append(item.url)
    pending = pending[:max(0, max_fetches)]
    if pending:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for url, (published, evidence) in zip(pending, pool.map(fetch_publication, pending)):
                cache[url] = {"published_at": published.isoformat() if published else None,
                              "evidence": evidence, "checked_at": now.isoformat()}
        write_json(cache_path, {"schema_version": 1, "items": cache})
    selected, excluded, per_source = [], Counter(), {}
    for item in items:
        native = parse_publication_time(item.published_dt)
        record = cache.get(item.url, {})
        original = parse_publication_time(record.get("published_at")) if record.get("evidence") else None
        use_original = original is not None and (native is None or original < native)
        published = original if use_original else native
        evidence = record.get("evidence", "") if use_original else item.publication_source
        observed = observed_at(item)
        reason = ""
        if published is not None and published < cutoff:
            reason = "old_publication"
        elif published is not None and published > now + FUTURE_TOLERANCE:
            reason = "future_publication"
        elif observed is not None and observed > now + FUTURE_TOLERANCE:
            reason = "future_capture"
        elif published is None or not evidence:
            reason = "unknown_publication"
        elif observed is not None and published > observed + FUTURE_TOLERANCE:
            reason = "publication_after_capture"
        source_stats = per_source.setdefault(item.source_id, {"name": item.source_name, "input": 0, "included": 0})
        source_stats["input"] += 1
        if reason:
            excluded[reason] += 1
            continue
        source_stats["included"] += 1
        selected.append(replace(item, published_dt=published.replace(tzinfo=None), publication_source=evidence,
                                collected_dt=observed.replace(tzinfo=None) if observed else None))
    latest_snapshot = max(latest_by_source.values(), default=None)
    report = {"schema_version": 1, "generated_at": now.isoformat(), "cutoff": cutoff.isoformat(),
              "window_hours": window_hours, "input_items": len(items), "included_items": len(selected),
              "excluded_items": len(items) - len(selected), "excluded_by_reason": dict(excluded),
              "publication_checks": len(pending), "by_source": per_source,
              "platform_last_capture": latest_snapshot.isoformat() if latest_snapshot else None,
              "platform_cache_stale": latest_snapshot is None or now - latest_snapshot > timedelta(hours=2)}
    write_json(output_root / "freshness" / "report.json", report)
    return selected, report
