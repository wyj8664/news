#!/usr/bin/env python3
"""Generate a Cailianpress telegraph page under the source hotlists section."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CHINA_TZ = timezone(timedelta(hours=8))
BASE_URL = "https://www.cls.cn"
ROLL_ENDPOINT = f"{BASE_URL}/v1/roll/get_roll_list"
APP_PARAMS = {
    "app": "CailianpressWeb",
    "os": "web",
    "sv": "8.7.9",
}


@dataclass
class TelegraphItem:
    id: int
    title: str
    content: str
    ctime: int
    level: str
    author: str
    url: str
    subjects: list[str]
    stocks: list[str]

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.ctime, CHINA_TZ)


def js_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sorted_keys(value: dict[str, Any]) -> list[str]:
    return sorted(value.keys(), key=lambda key: str(key).upper())


def query_string(value: dict[str, Any], prefix: str = "") -> str:
    parts: list[str] = []
    for raw_key in sorted_keys(value):
        key = f"{prefix}[{raw_key}]" if prefix else str(raw_key)
        item = value[raw_key]
        if item is None:
            continue
        if isinstance(item, (str, int, float, bool)):
            parts.append(f"{key}={js_string(item)}")
        elif isinstance(item, list):
            if item:
                for index, child in enumerate(item):
                    child_string = query_string({str(index): child}, key)
                    if child_string:
                        parts.append(child_string)
            else:
                parts.append(f"{key}[]")
        elif isinstance(item, dict):
            child_string = query_string(item, key)
            if child_string:
                parts.append(child_string)
    return "&".join(part for part in parts if part)


def cls_sign(params: dict[str, Any]) -> str:
    payload = query_string(params)
    sha1 = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1.encode("utf-8")).hexdigest()


def fetch_json(url: str, params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; NEWSHOT cls telegraph)",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE_URL}/telegraph",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def signed_roll_page(last_time: int, rn: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        **APP_PARAMS,
        "last_time": last_time,
        "refresh_type": 1,
        "rn": rn,
    }
    params["sign"] = cls_sign(params)
    data = fetch_json(ROLL_ENDPOINT, params)
    if str(data.get("errno")) != "0":
        raise RuntimeError(data.get("msg") or f"errno={data.get('errno')}")
    payload = data.get("data") or {}
    roll_data = payload.get("roll_data") or []
    if not isinstance(roll_data, list):
        return []
    return [item for item in roll_data if isinstance(item, dict)]


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return text.strip()


def split_title_content(raw: dict[str, Any]) -> tuple[str, str]:
    title = clean_text(raw.get("title"))
    content = clean_text(raw.get("content") or raw.get("brief"))
    if not title:
        match = re.match(r"^【([^】]{2,80})】(.*)$", content, flags=re.S)
        if match:
            title = clean_text(match.group(1))
            content = clean_text(match.group(2))
        else:
            title = content[:72]
            content = ""
    prefix = f"【{title}】"
    if content.startswith(prefix):
        content = clean_text(content[len(prefix) :])
    return title, content


def normalize_item(raw: dict[str, Any]) -> TelegraphItem | None:
    try:
        item_id = int(raw.get("id") or 0)
        ctime = int(raw.get("ctime") or 0)
    except (TypeError, ValueError):
        return None
    if item_id <= 0 or ctime <= 0:
        return None
    title, content = split_title_content(raw)
    subjects = []
    for subject in raw.get("subjects") or []:
        if isinstance(subject, dict) and subject.get("subject_name"):
            subjects.append(clean_text(subject["subject_name"]))
    stocks = []
    for stock in raw.get("stock_list") or []:
        if isinstance(stock, dict) and stock.get("name"):
            stocks.append(clean_text(stock["name"]))
    return TelegraphItem(
        id=item_id,
        title=title,
        content=content,
        ctime=ctime,
        level=clean_text(raw.get("level")),
        author=clean_text(raw.get("author")),
        url=f"{BASE_URL}/detail/{item_id}",
        subjects=subjects[:4],
        stocks=stocks[:6],
    )


def fetch_telegraph_items(limit: int, pages: int, page_size: int) -> tuple[list[TelegraphItem], list[str]]:
    seen: set[int] = set()
    items: list[TelegraphItem] = []
    errors: list[str] = []
    last_time = int(time.time()) + 60

    for page in range(max(1, pages)):
        try:
            raw_items = signed_roll_page(last_time, page_size)
        except Exception as exc:
            errors.append(f"page {page + 1}: {exc}")
            break
        if not raw_items:
            break
        next_last_time = last_time
        for raw in raw_items:
            normalized = normalize_item(raw)
            if normalized is None or normalized.id in seen:
                continue
            seen.add(normalized.id)
            items.append(normalized)
            next_last_time = min(next_last_time, normalized.ctime)
        if next_last_time >= last_time:
            break
        last_time = next_last_time
        if len(items) >= limit:
            break
        time.sleep(0.15)

    items = sorted(items, key=lambda item: item.ctime, reverse=True)[:limit]
    return items, errors


def fmt_time(item: TelegraphItem, fmt: str = "%H:%M:%S") -> str:
    return item.dt.strftime(fmt)


def render_tags(item: TelegraphItem) -> str:
    tags = []
    if item.level:
        tags.append(f'<span class="mini-chip level level-{e(item.level).lower()}">{e(item.level)}</span>')
    tags.extend(f'<span class="mini-chip">{e(subject)}</span>' for subject in item.subjects)
    tags.extend(f'<span class="mini-chip stock">{e(stock)}</span>' for stock in item.stocks)
    return "".join(tags)


def e(value: object) -> str:
    return html.escape(str(value or ""))


def item_row(item: TelegraphItem) -> str:
    content = f'<p>{e(item.content)}</p>' if item.content else ""
    author = f" · {e(item.author)}" if item.author else ""
    return f"""
    <article class="telegraph-item">
      <div class="timebox">
        <strong>{e(fmt_time(item, "%H:%M"))}</strong>
        <span>{e(fmt_time(item, "%S"))}</span>
      </div>
      <div class="telegraph-card">
        <div class="telegraph-meta">{e(fmt_time(item, "%Y-%m-%d %H:%M:%S"))}{author}</div>
        <h2><a href="{e(item.url)}" target="_blank" rel="noopener noreferrer">{e(item.title)}</a></h2>
        {content}
        <div class="row-tags">{render_tags(item)}</div>
      </div>
    </article>
    """


def nav_html() -> str:
    nav = [
        ("home", "/", "领域分类"),
        ("daily", "/daily/", "日报"),
        ("timeline", "/timeline/", "热点脉络"),
        ("raw", "/hotlists/", "来源热榜"),
    ]
    return "".join(
        f'<a class="{ "active" if key == "raw" else "" }" href="{href}">{label}</a>'
        for key, href, label in nav
    )


def render_page(items: list[TelegraphItem], generated_at: datetime, errors: list[str]) -> str:
    latest = items[0].dt.strftime("%H:%M:%S") if items else "-"
    earliest = items[-1].dt.strftime("%H:%M:%S") if items else "-"
    latest_date = items[0].dt.strftime("%Y-%m-%d") if items else generated_at.strftime("%Y-%m-%d")
    rows: list[str] = []
    current_date = ""
    for item in items:
        date_label = item.dt.strftime("%Y-%m-%d")
        if date_label != current_date:
            rows.append(f'<div class="date-row">{e(date_label)}</div>')
            current_date = date_label
        rows.append(item_row(item))
    error_html = ""
    if errors:
        error_html = f'<div class="notice">部分翻页请求失败：{e("; ".join(errors[:3]))}</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>财联社电报</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #fff;
      --text: #172033;
      --muted: #627086;
      --line: #e3e8f0;
      --blue: #185abc;
      --red: #d43c33;
      --green: #178260;
      --amber: #9a6400;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    }}
    a {{ color: var(--blue); text-decoration: none; }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 20px 18px 56px; }}
    .topbar {{
      position: sticky; top: 0; z-index: 30;
      display: flex; align-items: center; justify-content: space-between; gap: 18px;
      padding: 14px 0 16px; border-bottom: 1px solid var(--line);
      background: rgba(245, 247, 251, .96); backdrop-filter: blur(12px);
    }}
    .brand {{ font-size: 22px; font-weight: 850; letter-spacing: 0; color: #20293a; }}
    .brand span {{ color: #0d8fc8; }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    nav a, .back-link {{
      padding: 9px 12px; border: 1px solid var(--line); border-radius: 6px;
      background: #fff; color: #435066; font-size: 14px; font-weight: 650;
      white-space: nowrap;
    }}
    nav a.active {{ color: #fff; background: #20293a; border-color: #20293a; }}
    .page-head {{ margin: 24px 0 18px; }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.2; letter-spacing: 0; }}
    .sub {{ margin-top: 8px; color: var(--muted); line-height: 1.7; font-size: 14px; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 20px; }}
    .summary-cell {{ padding: 12px; background: #fff; border: 1px solid var(--line); border-radius: 8px; }}
    .summary-cell strong {{ display: block; font-size: 21px; line-height: 1.1; font-variant-numeric: tabular-nums; }}
    .summary-cell span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .notice {{ margin-bottom: 14px; padding: 10px 12px; border: 1px solid #f0d7ad; border-radius: 8px; background: #fff8ed; color: #7a5200; font-size: 13px; }}
    .date-row {{ margin: 20px 0 8px; color: var(--muted); font-size: 14px; font-weight: 750; }}
    .telegraph-item {{ display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; align-items: start; }}
    .timebox {{ position: sticky; top: 82px; padding-top: 13px; text-align: right; font-variant-numeric: tabular-nums; }}
    .timebox strong {{ display: block; color: #142033; font-size: 17px; line-height: 1.1; }}
    .timebox span {{ display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .telegraph-card {{ margin-bottom: 12px; padding: 14px 16px; background: #fff; border: 1px solid var(--line); border-radius: 8px; }}
    .telegraph-meta {{ color: var(--muted); font-size: 12px; line-height: 1.5; }}
    .telegraph-card h2 {{ margin: 7px 0 0; font-size: 17px; line-height: 1.55; letter-spacing: 0; }}
    .telegraph-card p {{ margin: 8px 0 0; color: #36445a; font-size: 14px; line-height: 1.75; white-space: pre-wrap; }}
    .row-tags {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; }}
    .mini-chip {{ display: inline-flex; align-items: center; min-height: 20px; padding: 2px 7px; border-radius: 999px; background: #f1f4f8; color: #536177; font-size: 12px; font-weight: 650; }}
    .mini-chip.level-a, .mini-chip.level-b {{ background: #fff1ef; color: var(--red); }}
    .mini-chip.stock {{ background: #ecf8f3; color: var(--green); }}
    .empty {{ padding: 18px; background: #fff; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); }}
    @media (max-width: 860px) {{
      .topbar {{ position: static; align-items: flex-start; flex-direction: column; }}
      nav {{ width: 100%; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px; justify-content: flex-start; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .telegraph-item {{ grid-template-columns: 54px minmax(0, 1fr); gap: 8px; }}
      .timebox {{ position: static; padding-top: 12px; }}
      .timebox strong {{ font-size: 14px; }}
      .telegraph-card {{ padding: 12px; }}
      .telegraph-card h2 {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div><a class="brand" href="/">NEWS<span>HOT</span></a></div>
      <nav><a class="back-link" href="/" onclick="history.back(); return false;">返回</a>{nav_html()}</nav>
    </div>
    <div class="page-head">
      <h1>财联社电报</h1>
      <div class="sub">{e(latest_date)} · 来源热榜 / 财联社电报 · 按财联社发布时间倒序展示，不做热度、主题或平台权重排序。</div>
    </div>
    <div class="summary">
      <div class="summary-cell"><strong>{len(items)}</strong><span>电报条目</span></div>
      <div class="summary-cell"><strong>{e(latest)}</strong><span>最新时间</span></div>
      <div class="summary-cell"><strong>{e(earliest)}</strong><span>最早时间</span></div>
      <div class="summary-cell"><strong>{e(generated_at.strftime("%H:%M:%S"))}</strong><span>生成时间</span></div>
    </div>
    {error_html}
    <main>{''.join(rows) if rows else '<div class="empty">暂时没有抓到财联社电报。</div>'}</main>
  </div>
</body>
</html>
"""


def redirect_page(target: str) -> str:
    escaped = e(target)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url={escaped}">
  <link rel="canonical" href="{escaped}">
  <title>跳转到财联社电报</title>
  <script>location.replace({json.dumps(target, ensure_ascii=False)});</script>
</head>
<body>
  <p><a href="{escaped}">财联社电报已移到来源热榜下，点击打开。</a></p>
</body>
</html>
"""


def write_outputs(output_root: Path, items: list[TelegraphItem], errors: list[str]) -> Path:
    generated_at = datetime.now(CHINA_TZ)
    out_dir = output_root / "hotlists" / "telegraph"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source": f"{BASE_URL}/telegraph",
        "sort": "ctime_desc",
        "items": [asdict(item) for item in items],
        "errors": errors,
    }
    (out_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    page_path = out_dir / "index.html"
    page_path.write_text(render_page(items, generated_at, errors), encoding="utf-8")
    legacy_dir = output_root / "telegraph"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "index.html").write_text(redirect_page("/hotlists/telegraph/"), encoding="utf-8")
    (legacy_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return page_path


def generate(output_root: Path, limit: int, pages: int, page_size: int) -> Path:
    items, errors = fetch_telegraph_items(limit=limit, pages=pages, page_size=page_size)
    return write_outputs(output_root, items, errors)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--page-size", type=int, default=20)
    args = parser.parse_args()
    page = generate(Path(args.output_root), args.limit, args.pages, args.page_size)
    print(f"[telegraph] generated {page}")


if __name__ == "__main__":
    main()
