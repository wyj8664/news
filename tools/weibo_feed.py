#!/usr/bin/env python3
"""Export only followed public Weibo posts from WeiboX's cached HTTP API."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
from zoneinfo import ZoneInfo

CHINA = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT.parent / "weiboX-server" / "server" / ".env"
PUBLIC_URL = "https://newshot-5jq.pages.dev/api/weibo/followed"
KV_KEY = "weibo:followed:v1"
WRANGLER = "wrangler@4.151.1"


def plain(value: object) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def headline(text: str) -> str:
    text = re.sub(r"#([^#\n]+)#", r"\1", plain(text))
    bracket = re.match(r"\s*【([^】]+)】", text)
    if bracket:
        return bracket.group(1).strip()[:160]
    first = text.split("\n", 1)[0].strip()
    if len(first) > 100:
        first = re.split(r"[。！？]", first, maxsplit=1)[0]
    return first[:100] + ("…" if len(first) > 100 else "")


def number(value: object) -> int:
    try:
        parsed = float(value)
        return max(0, int(parsed)) if math.isfinite(parsed) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def original_timestamp(original: object) -> int:
    """Use the original post's time, not the time an old post was reshared."""
    for _ in range(5):
        if not isinstance(original, dict):
            return 0
        nested = original.get("retweet")
        if original.get("is_retweet") or isinstance(nested, dict):
            original = nested
            continue
        return number(original.get("created_at_ts"))
    return 0


def normalize_payload(posts: list[dict], users: list[dict], status: dict, *, now: datetime | None = None,
                      max_age_hours: int = 24, truncated: bool = False) -> dict:
    from curated_events import primary_category_for_text, category_label

    now = (now or datetime.now(CHINA)).astimezone(CHINA)
    now_ms = int(now.timestamp() * 1000)
    cutoff = now_ms - max_age_hours * 3600 * 1000
    user_map = {str(u.get("id", "")): u for u in users if re.fullmatch(r"\d{5,20}", str(u.get("id", "")))}
    items, seen = [], set()
    for post in posts:
        uid, mid = str(post.get("user_id", "")), str(post.get("id", ""))
        ts = number(post.get("created_at_ts"))
        if uid not in user_map or not re.fullmatch(r"\d{5,24}", mid) or mid in seen:
            continue
        if ts < cutoff or ts > now_ms + 5 * 60 * 1000:
            continue
        text = plain(post.get("text"))
        original = post.get("retweet")
        is_retweet = bool(post.get("is_retweet") or isinstance(original, dict))
        origin_ts = original_timestamp(original) if is_retweet else 0
        if origin_ts > ts + 5 * 60 * 1000:
            origin_ts = 0
        if isinstance(original, dict) and original.get("text"):
            original_text = plain(original["text"])
            if not text or text in ("转发微博", "转发"):
                text = original_text
            else:
                text += "\n转发原文：" + original_text
        title = headline(text)
        if not title:
            continue
        category = primary_category_for_text(title)
        if category == "general":
            category = primary_category_for_text(text[:1000])
        seen.add(mid)
        items.append({
            "id": f"weibo:{mid}", "post_id": mid,
            "source_id": f"weibo-account:{uid}", "source_name": "微博·" + plain(post.get("user_name") or user_map[uid].get("screen_name") or uid),
            "account_id": uid, "account_name": plain(post.get("user_name") or user_map[uid].get("screen_name") or uid),
            "title": title, "summary": text[:6000], "url": f"https://m.weibo.cn/detail/{mid}",
            "timestamp": ts, "published_at": datetime.fromtimestamp(ts / 1000, CHINA).isoformat(),
            "likes": number(post.get("likes_count")), "comments": number(post.get("comments_count")),
            "reposts": number(post.get("reposts_count")), "is_retweet": is_retweet,
            "original_timestamp": origin_ts or None,
            "category": category, "category_name": category_label(category),
        })
    items.sort(key=lambda item: (item["timestamp"], item["post_id"]), reverse=True)
    counts = Counter(item["account_id"] for item in items)
    accounts = [{
        "id": uid, "name": plain(user.get("screen_name") or uid),
        "url": f"https://weibo.com/u/{uid}", "count": counts[uid],
        "last_fetched_at": number(user.get("last_fetched_at")),
    } for uid, user in user_map.items()]
    fetched_ms = max((account["last_fetched_at"] for account in accounts), default=0)
    return {
        "ok": True, "schema_version": 1, "source": "关注微博", "source_id": "weibo-followed",
        "generated_at": now.isoformat(),
        "fetched_at": datetime.fromtimestamp(fetched_ms / 1000, CHINA).isoformat() if fetched_ms else None,
        "window_hours": max_age_hours, "sort": "published_at_desc", "count": len(items),
        "accounts": accounts, "items": items, "truncated": truncated,
        "stale": not fetched_ms or now_ms - fetched_ms > 2 * 3600 * 1000,
        "collector_error": "本机采集出现异常，当前保留已缓存内容。" if status.get("last_error") else "",
    }


def read_json(url: str, token: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "NEWSHOT-Weibo/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    # Bypass system proxies only for the local cache; public requests use normal settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if url.startswith("http://127.0.0.1:") else urllib.request.build_opener()
    with opener.open(request, timeout=25) as response:
        return json.load(response)


def collect_local(base_url: str, env_file: Path, max_age_hours: int) -> dict:
    token = os.environ.get("WEIBOX_API_TOKEN", "")
    if not token:
        values = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                      if line and not line.startswith("#") and "=" in line)
        token = values.get("WEIBOX_API_TOKEN", "")
    if not token:
        raise RuntimeError("Local WeiboX API token is not configured")
    users = read_json(base_url + "/api/v1/users", token).get("users", [])
    status = read_json(base_url + "/api/v1/status", token)
    posts, truncated = [], False
    cutoff = (datetime.now(CHINA) - timedelta(hours=max_age_hours)).timestamp() * 1000
    for page in range(10):
        batch = read_json(f"{base_url}/api/v1/timeline?limit=200&offset={page * 200}", token).get("posts", [])
        posts.extend(batch)
        if len(batch) < 200 or any(0 < number(post.get("created_at_ts")) < cutoff for post in batch):
            break
    else:
        truncated = True
    return normalize_payload(posts, users, status, max_age_hours=max_age_hours, truncated=truncated)


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(handle.name)
    temporary.replace(path)


def publish(path: Path, payload: dict, config_path: Path) -> None:
    config = json.loads(config_path.read_text())
    namespace = str(config["namespace_id"])
    stable = {key: value for key, value in payload.items() if key != "generated_at"}
    digest = hashlib.sha256((namespace + json.dumps(stable, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    state_path = ROOT / ".weibo-published-hash"
    if state_path.exists() and state_path.read_text().strip() == digest:
        print("[weibo] unchanged cache; remote write skipped")
        return
    environment = dict(os.environ)
    if config.get("account_id"):
        environment["CLOUDFLARE_ACCOUNT_ID"] = str(config["account_id"])
    subprocess.run(["npx", "--yes", WRANGLER, "kv", "key", "put", KV_KEY,
                    "--namespace-id", namespace, "--remote", "--path", str(path.resolve())], check=True, cwd=ROOT, env=environment)
    state_path.write_text(digest + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8789")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--from-public", nargs="?", const=PUBLIC_URL)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-config", type=Path, default=ROOT / "config" / "weibo_publication.json")
    args = parser.parse_args()
    path = args.output_root / "weibo" / "latest.json"
    if args.seed and args.seed.exists() and not path.exists():
        save_json(path, json.loads(args.seed.read_text(encoding="utf-8")))
    try:
        if args.from_public:
            payload = read_json(args.from_public)
            if not payload.get("ok") or not isinstance(payload.get("items"), list):
                raise RuntimeError("Public Weibo snapshot is unavailable")
        else:
            payload = collect_local(args.base_url.rstrip("/"), args.env_file, args.max_age_hours)
        save_json(path, payload)
        if args.publish:
            publish(path, payload, args.publish_config)
        print(f"[weibo] exported {payload['count']} posts from {len(payload['accounts'])} followed accounts; fetched_at={payload.get('fetched_at')}")
    except Exception as error:
        if args.allow_stale:
            print(f"[weibo] source unavailable; existing snapshot preserved: {error}")
            return
        raise SystemExit(f"[weibo] export failed: {error}") from None


if __name__ == "__main__":
    main()
