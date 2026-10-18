"""Read the public Jiemian rolling-flash page and its own load-more endpoint.

No article-body, member, login, whitelist, or editorial endpoints are requested.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html.parser import HTMLParser
import html
import json
import re
import time
import urllib.parse
import urllib.request

from jin10_public import PLACEHOLDER, RESTRICTED_KEYS, restricted_value
from news_freshness import WINDOW_HOURS, parse_publication_time

SOURCE_ID = "jiemian-flash"
SOURCE_NAME = "界面新闻·快报"
LIST_URL = "https://www.jiemian.com/lists/4.html"
MORE_URL = "https://papi.jiemian.com/page/api/kuaixun/getlistmore"
ITEM_CLASS = "columns-right-center__newsflash-item"
SUMMARY_CLASS = "columns-right-center__newsflash-content__summary"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()


def public_jiemian_record(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if any(restricted_value(raw.get(key)) for key in RESTRICTED_KEYS):
        return False
    for key in ("access", "access_level", "visibility"):
        if str(raw.get(key) or "").strip().lower() not in {"", "0", "public", "open", "free", "anonymous"}:
            return False
    if raw.get("is_free") is not None and str(raw["is_free"]).lower() not in {"1", "true"}:
        return False
    return not any(PLACEHOLDER.search(clean(raw.get(key))) for key in ("title", "summary"))


class FlashParser(HTMLParser):
    """Only capture visible flash cards, never page scripts or unrelated links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.records = []
        self.current = None
        self.item_depth = self.title_depth = self.summary_depth = None

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag in VOID_TAGS:
            return
        self.stack.append(tag)
        depth = len(self.stack)
        classes = (attrs.get("class") or "").split()
        if ITEM_CLASS in classes:
            self.current = {"id": attrs.get("data-id"), "publishtime": attrs.get("data-time"),
                            "title": "", "summary": "", "url": ""}
            self.item_depth = depth
        if self.current is None:
            return
        if any(re.search(r"(?:^|[-_])(?:vip|plus|paywall|locked|restricted)(?:$|[-_])", value, re.I) for value in classes):
            self.current["restricted"] = True
        for key in RESTRICTED_KEYS:
            if key in attrs or "data-" + key.replace("_", "-") in attrs:
                value = attrs.get(key, attrs.get("data-" + key.replace("_", "-")))
                self.current[key] = True if value is None or value == "" else value
        for key in ("access", "access_level", "visibility"):
            if "data-" + key.replace("_", "-") in attrs:
                self.current[key] = attrs["data-" + key.replace("_", "-")]
        if tag == "h4":
            self.title_depth = depth
        if tag == "a" and self.title_depth is not None:
            self.current["url"] = attrs.get("href", "")
        if SUMMARY_CLASS in classes:
            self.summary_depth = depth

    def handle_data(self, value):
        if self.current is None or any(tag in {"script", "style"} for tag in self.stack):
            return
        if self.title_depth is not None:
            self.current["title"] += value
        if self.summary_depth is not None:
            self.current["summary"] += value

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.current is not None:
            if self.title_depth is not None and depth <= self.title_depth:
                self.title_depth = None
            if self.summary_depth is not None and depth <= self.summary_depth:
                self.summary_depth = None
            if depth <= self.item_depth:
                self.records.append(self.current)
                self.current = None
                self.item_depth = None
        del self.stack[depth - 1:]


def allowed_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and (parsed.hostname, parsed.path) in {
                    ("www.jiemian.com", "/lists/4.html"),
                    ("papi.jiemian.com", "/page/api/kuaixun/getlistmore")})
    except ValueError:
        return False


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError("public flash redirected outside its allowed endpoints")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def read_public(url: str, timeout: float) -> str:
    if not allowed_url(url):
        raise ValueError("unsupported public flash endpoint")
    opener = urllib.request.build_opener(PublicRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "NEWSHOT-PublicFlash/1.0",
                                                   "Accept": "text/html, application/json", "Referer": LIST_URL})
    with opener.open(request, timeout=timeout) as response:
        if not allowed_url(response.url):
            raise ValueError("unexpected public response URL")
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("public response too large")
        return body.decode("utf-8")


def normalize_record(raw: dict) -> dict | None:
    if not public_jiemian_record(raw):
        return None
    key = str(raw.get("id") or "")
    title = clean(raw.get("title"))
    published = parse_publication_time(raw.get("publishtime"))
    if not re.fullmatch(r"\d+", key) or not title or published is None:
        return None
    canonical = f"https://www.jiemian.com/article/{key}.html"
    if raw.get("url") and raw["url"] != canonical:
        return None
    return {"id": key, "title": title, "summary": clean(raw.get("summary"))[:2000],
            "url": canonical, "published_at": published.isoformat(), "public_access": True}


def fetch_jiemian(now: datetime, max_pages: int) -> dict:
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    deadline = time.monotonic() + 30
    records, seen, errors = [], set(), []
    pages = skipped = 0
    complete = False
    cursor = None
    cid = tagid = None
    for page in range(1, min(24, max(1, max_pages)) + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("界面快报达到本轮请求时限，尚未确认抓全24小时。")
            break
        try:
            if page == 1:
                document = read_public(LIST_URL, min(8, remaining))
                parser = FlashParser()
                parser.feed(document)
                batch = parser.records
                cid_match = re.search(r"\bvar\s+cid\s*=\s*['\"]([\w-]+)['\"]", document)
                tag_match = re.search(r"\bvar\s+tagid\s*=\s*['\"]([\w-]*)['\"]", document)
                cid = cid_match[1] if cid_match else None
                tagid = tag_match[1] if tag_match else None
            else:
                query = urllib.parse.urlencode({"cid": cid, "start_time": cursor, "page": page, "tagid": tagid})
                payload = json.loads(read_public(MORE_URL + "?" + query, min(8, remaining)))
                if str(payload.get("code")) != "0":
                    raise ValueError("public feed unavailable")
                batch = payload.get("result", {}).get("list")
                if not isinstance(batch, list):
                    raise ValueError("missing public feed list")
        except Exception:
            errors.append("界面公开快报请求失败，保留已有缓存；不尝试登录或访问受限接口。")
            break
        pages += 1
        if not batch:
            errors.append("公开快报没有更多可读条目，尚未确认抓全24小时。")
            break
        oldest = None
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            published = parse_publication_time(raw.get("publishtime"))
            if published:
                oldest = min(oldest or published, published)
            if not public_jiemian_record(raw):
                skipped += 1
                continue
            item = normalize_record(raw)
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            if cutoff <= published <= now + timedelta(minutes=5):
                records.append(item)
        if oldest is not None and oldest <= cutoff:
            complete = True
            break
        next_cursor = int(oldest.timestamp()) if oldest else None
        if next_cursor is None or (cursor is not None and next_cursor >= cursor):
            errors.append("界面快报分页时间未推进，已停止，尚未确认抓全24小时。")
            break
        if not cid or tagid is None:
            errors.append("未找到公开分页参数，仅保留当前页，不猜测或请求受限接口。")
            break
        cursor = next_cursor
        time.sleep(0.15)
    return {"schema_version": 1, "source_id": SOURCE_ID, "source_name": SOURCE_NAME, "source_url": LIST_URL,
            "fetched_at": now.isoformat(), "window_start": cutoff.isoformat(), "window_hours": WINDOW_HOURS,
            "public_only": True, "window_complete": complete, "truncated": not complete,
            "pages_fetched": pages, "skipped_restricted": skipped, "errors": errors,
            "filter": "24小时公开滚动快报，不要求热门或重要；无权限内容不采集",
            "items": sorted(records, key=lambda item: item["published_at"], reverse=True),
            "ok": bool(records)}
