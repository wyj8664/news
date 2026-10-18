"""Collect configured news streams without requiring a hot/important flag.

Only built-in public sources are fetched. Existing Weibo snapshots and upstream
RSS/platform databases remain owned by their existing collectors.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import time
import urllib.parse

from news_freshness import CHINA, WINDOW_HOURS, parse_publication_time, write_json
from jin10_public import public_jin10_record
from jiemian_flash import fetch_jiemian, public_jiemian_record

ROOT = Path(__file__).resolve().parents[1]
FEEDS = {"cls-telegraph": "财联社电报", "jin10-all": "金十数据", "jiemian-flash": "界面新闻·快报"}
PUBLIC_ONLY_FEEDS = {"jin10-all", "jiemian-flash"}


def public_record(feed: str, raw: dict) -> bool:
    if feed == "jin10-all":
        return public_jin10_record(raw)
    if feed == "jiemian-flash":
        return public_jiemian_record(raw)
    return True


def load_config(path: Path | None = None) -> dict:
    path = path or ROOT / "config" / "home_sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


def read_snapshot(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("items"), list) else {}
    except (OSError, ValueError):
        return {}


def fetch_cls(now: datetime, max_pages: int) -> dict:
    from cls_telegraph import normalize_item, signed_roll_page
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    cursor = int(now.timestamp()) + 60
    seen, records, errors = set(), [], []
    complete = False
    pages = 0
    for _ in range(max_pages):
        try:
            batch = signed_roll_page(cursor, 50)
        except Exception:
            errors.append("财联社本轮请求失败，未确认完整覆盖24小时。")
            break
        pages += 1
        if not batch:
            complete = True
            break
        oldest = cursor
        for raw in batch:
            entry = normalize_item(raw)
            if entry is None:
                continue
            oldest = min(oldest, entry.ctime)
            if entry.id in seen:
                continue
            seen.add(entry.id)
            if cutoff.timestamp() <= entry.ctime <= now.timestamp() + 300:
                records.append(asdict(entry))
        if oldest <= cutoff.timestamp():
            complete = True
            break
        if oldest >= cursor:
            errors.append("财联社分页游标未推进，已停止。")
            break
        cursor = oldest
        time.sleep(0.15)
    return {"schema_version": 1, "source_id": "cls-telegraph", "source_name": FEEDS["cls-telegraph"],
            "fetched_at": now.isoformat(), "window_start": cutoff.isoformat(), "window_hours": WINDOW_HOURS,
            "window_complete": complete, "truncated": not complete, "pages_fetched": pages,
            "filter": "全部电报，不按等级、热度或上榜筛选", "errors": errors,
            "items": sorted(records, key=lambda value: value["ctime"], reverse=True),
            "ok": bool(pages)}


def fetch_jin10(now: datetime, max_pages: int) -> dict:
    from curated_events import (JIN10_CHANNELS, JIN10_HOT_API, fetch_remote_json, jin10_data_title,
                                jin10_headers, jin10_item_url, parse_jin10_dt)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    cursor = ""
    seen, records, errors = set(), [], []
    complete = False
    pages = 0
    skipped_restricted = 0
    deadline = time.monotonic() + 30
    for _ in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("金十达到本轮请求时限，尚未确认完整覆盖24小时。")
            break
        params = {"channel": list(JIN10_CHANNELS)}
        if cursor:
            params["max_time"] = cursor
        query = urllib.parse.quote(json.dumps(params, ensure_ascii=False, separators=(",", ":")))
        try:
            payload = fetch_remote_json(f"{JIN10_HOT_API}?params={query}", jin10_headers("1.0"), timeout=min(8, remaining))
            batch = payload.get("data")
            if not isinstance(batch, list):
                raise ValueError("missing feed data")
        except Exception:
            errors.append("金十本轮请求失败，未确认完整覆盖24小时。")
            break
        pages += 1
        if not batch:
            errors.append("公开接口暂无更多条目，未确认完整覆盖24小时；不尝试登录或访问受限历史。")
            break
        oldest = None
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            published = parse_jin10_dt(raw.get("time"))
            if published is None:
                continue
            published = published.replace(tzinfo=CHINA)
            oldest = min(oldest or published, published)
            key = str(raw.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if not public_jin10_record(raw):
                skipped_restricted += 1
                continue
            title = jin10_data_title(raw)
            if not title or published < cutoff or published > now + timedelta(minutes=5):
                continue
            content = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            records.append({"id": key, "title": title, "summary": str(content.get("content") or "")[:6000],
                            "url": jin10_item_url(raw), "published_at": published.isoformat(),
                            "important": raw.get("important"), "hot": raw.get("hot"), "public_access": True})
        if oldest is not None and oldest <= cutoff:
            complete = True
            break
        next_cursor = oldest.strftime("%Y-%m-%d %H:%M:%S") if oldest else ""
        if not next_cursor or (cursor and next_cursor >= cursor):
            errors.append("金十分页游标未推进，已停止。")
            break
        cursor = next_cursor
        time.sleep(0.15)
    return {"schema_version": 1, "source_id": "jin10-all", "source_name": FEEDS["jin10-all"],
            "fetched_at": now.isoformat(), "window_start": cutoff.isoformat(), "window_hours": WINDOW_HOURS,
            "window_complete": complete, "truncated": not complete, "pages_fetched": pages,
            "filter": "首页公开市场快讯，普通+重要+热门；不要求important或hot", "errors": errors,
            "public_only": True, "skipped_restricted": skipped_restricted,
            "items": sorted(records, key=lambda value: value["published_at"], reverse=True),
            "ok": bool(pages)}


def refresh(output_root: Path, *, force: bool = False, config: dict | None = None) -> dict:
    config = config or load_config()
    now = datetime.now(CHINA)
    directory = output_root / "source-pool"
    directory.mkdir(parents=True, exist_ok=True)
    statuses = []
    jobs = []
    for feed in config.get("native_feeds", []):
        if feed not in FEEDS:
            continue
        previous = read_snapshot(directory / f"{feed}.json")
        fetched = parse_publication_time(previous.get("fetched_at"))
        verified = feed not in PUBLIC_ONLY_FEEDS or previous.get("public_only") is True
        if not force and verified and fetched and timedelta(0) <= now - fetched < timedelta(minutes=10):
            statuses.append({"source_id": feed, "name": FEEDS[feed], "ok": True, "cached": True,
                             "count": len(previous["items"]), "window_complete": previous.get("window_complete") is True})
        else:
            jobs.append((feed, previous))
    def collect(job):
        feed, previous = job
        try:
            fetch = {"cls-telegraph": fetch_cls, "jin10-all": fetch_jin10, "jiemian-flash": fetch_jiemian}[feed]
            result = fetch(now, min(48, max(1, int(config.get("max_pages_per_feed", 24)))))
        except Exception:
            result = {"ok": False, "items": [], "errors": ["来源抓取失败，保留已有缓存。"]}
        if result.get("ok"):
            observed = {str(entry["id"]): now.isoformat() for entry in result["items"]}
            if not result.get("window_complete") and (feed not in PUBLIC_ONLY_FEEDS or previous.get("public_only") is True):
                current_ids = set(observed)
                for entry in previous.get("items", []):
                    if not public_record(feed, entry):
                        continue
                    key = str(entry.get("id"))
                    published = parse_publication_time(entry.get("ctime") if feed == "cls-telegraph" else entry.get("published_at"))
                    if key not in current_ids and published and now - timedelta(hours=WINDOW_HOURS) <= published <= now:
                        result["items"].append(entry)
                        observed[key] = previous.get("item_observed_at", {}).get(key) or previous.get("fetched_at")
            result["item_observed_at"] = observed
            write_json(directory / f"{feed}.json", result)
            if feed == "cls-telegraph":
                from cls_telegraph import TelegraphItem, write_outputs
                (output_root / "hotlists" / "telegraph").mkdir(parents=True, exist_ok=True)
                entries = sorted(result["items"], key=lambda entry: entry["ctime"], reverse=True)[:120]
                write_outputs(output_root, [TelegraphItem(**entry) for entry in entries], result.get("errors", []))
        effective = result if result.get("ok") else previous
        return {"source_id": feed, "name": FEEDS[feed], "ok": bool(result.get("ok")),
                "cached": not result.get("ok"), "count": len(effective.get("items", [])),
                "window_complete": effective.get("window_complete") is True,
                "errors": result.get("errors", [])}
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses.extend(executor.map(collect, jobs))
    # A targeted refresh must not erase the other feeds' last known status.
    try:
        old_manifest = json.loads((directory / "status.json").read_text())
        unchanged = [entry for entry in old_manifest.get("sources", [])
                     if entry.get("source_id") not in {value["source_id"] for value in statuses}]
    except (OSError, ValueError, AttributeError, TypeError):
        unchanged = []
    manifest = {"generated_at": now.isoformat(), "sources": [*unchanged, *statuses],
                "wechat_collection_enabled": False, "wechat_note": "公众号采集保持停用，未触发刷新。"}
    write_json(directory / "status.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", choices=tuple(FEEDS), help="Refresh one stream without modifying the others")
    args = parser.parse_args()
    config = load_config()
    if args.only:
        config = {**config, "native_feeds": [args.only]}
    result = refresh(args.output_root, force=args.force, config=config)
    for source in result["sources"]:
        print(f"[source-pool] {source['name']}: count={source['count']}, ok={source['ok']}, window_complete={source['window_complete']}", flush=True)


if __name__ == "__main__":
    main()
