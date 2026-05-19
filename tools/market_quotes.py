#!/usr/bin/env python3
"""Fetch a small watchlist of market quotes into a local JSON file."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_WATCHLIST = [
    {
        "id": "pingan_bank",
        "name": "平安银行",
        "symbol": "sz000001",
        "type": "tencent_stock",
        "market": "深交所",
        "unit": "元",
        "url": "https://gu.qq.com/sz000001/gp",
    },
    {
        "id": "gold_spot",
        "name": "现货黄金",
        "symbol": "hf_XAU",
        "type": "tencent_global",
        "market": "贵金属",
        "unit": "美元/盎司",
        "url": "https://gu.qq.com/hf/hf_XAU",
    },
]

CHINA_TZ = timezone(timedelta(hours=8))
TENCENT_URL = "https://qt.gtimg.cn/q={symbol}"


def parse_float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def format_quote_time(raw: str | None) -> str:
    if not raw:
        return datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    value = raw.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return value


def fetch_tencent(symbol: str) -> str:
    request = Request(
        TENCENT_URL.format(symbol=symbol),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.qq.com/",
        },
    )
    with urlopen(request, timeout=10) as response:
        raw = response.read()
    text = raw.decode("gbk", errors="replace").strip()
    match = re.search(r'v_[^=]+="(.*)";?$', text)
    if not match or "pv_none_match" in text:
        raise RuntimeError(f"empty quote for {symbol}")
    return match.group(1)


def parse_stock(entry: dict[str, str]) -> dict[str, object]:
    fields = fetch_tencent(entry["symbol"]).split("~")
    if len(fields) < 35:
        raise RuntimeError(f"unexpected stock quote format: {entry['symbol']}")
    price = parse_float(fields[3])
    prev_close = parse_float(fields[4])
    change = parse_float(fields[31])
    percent = parse_float(fields[32])
    if change is None and price is not None and prev_close is not None:
        change = price - prev_close
    if percent is None and change is not None and prev_close:
        percent = change / prev_close * 100
    return {
        "id": entry["id"],
        "name": entry.get("name") or fields[1],
        "symbol": entry["symbol"],
        "market": entry.get("market", ""),
        "type": "stock",
        "unit": entry.get("unit", ""),
        "url": entry.get("url", ""),
        "price": price,
        "change": change,
        "percent": percent,
        "open": parse_float(fields[5]),
        "prev_close": prev_close,
        "high": parse_float(fields[33]),
        "low": parse_float(fields[34]),
        "volume": parse_float(fields[36]) if len(fields) > 36 else None,
        "turnover": parse_float(fields[37]) if len(fields) > 37 else None,
        "time": format_quote_time(fields[30] if len(fields) > 30 else None),
        "source": "腾讯行情",
    }


def parse_global(entry: dict[str, str]) -> dict[str, object]:
    fields = fetch_tencent(entry["symbol"]).split(",")
    if len(fields) < 14:
        raise RuntimeError(f"unexpected global quote format: {entry['symbol']}")
    price = parse_float(fields[0])
    prev_close = parse_float(fields[7])
    percent = parse_float(fields[1])
    change = None
    if price is not None and prev_close is not None:
        change = price - prev_close
    quote_date = fields[12].strip()
    quote_time = fields[6].strip()
    return {
        "id": entry["id"],
        "name": entry.get("name") or fields[13].strip(),
        "symbol": entry["symbol"],
        "market": entry.get("market", ""),
        "type": "commodity",
        "unit": entry.get("unit", ""),
        "url": entry.get("url", ""),
        "price": price,
        "change": change,
        "percent": percent,
        "open": parse_float(fields[8]),
        "prev_close": prev_close,
        "high": parse_float(fields[4]),
        "low": parse_float(fields[5]),
        "time": format_quote_time(f"{quote_date} {quote_time}"),
        "source": "腾讯行情",
    }


def load_watchlist(path: Path | None) -> list[dict[str, str]]:
    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return data
    return DEFAULT_WATCHLIST


def load_previous(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return {}
    return {str(item.get("id")): item for item in items if isinstance(item, dict)}


def attach_history(item: dict[str, object], previous: dict[str, dict[str, object]]) -> dict[str, object]:
    old = previous.get(str(item["id"]), {})
    history = old.get("history", [])
    if not isinstance(history, list):
        history = []
    price = parse_float(item.get("price"))
    timestamp = str(item.get("time") or datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"))
    if price is not None:
        point = {"time": timestamp, "price": price}
        if not history or history[-1] != point:
            history.append(point)
    item["history"] = history[-80:]
    return item


def fetch_item(entry: dict[str, str]) -> dict[str, object]:
    quote_type = entry.get("type", "")
    if quote_type == "tencent_stock":
        return parse_stock(entry)
    if quote_type == "tencent_global":
        return parse_global(entry)
    raise RuntimeError(f"unsupported quote type: {quote_type}")


def generate(output_root: Path, watchlist_path: Path | None) -> Path:
    output_path = output_root / "markets" / "quotes.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist = load_watchlist(watchlist_path)
    previous = load_previous(output_path)
    items: list[dict[str, object]] = []
    errors: list[str] = []
    for entry in watchlist:
        try:
            items.append(attach_history(fetch_item(entry), previous))
        except Exception as exc:
            stale = previous.get(str(entry.get("id")))
            if stale:
                stale["stale"] = True
                items.append(stale)
            errors.append(f"{entry.get('name') or entry.get('symbol')}: {exc}")
    payload = {
        "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        "refresh_seconds": 60,
        "source": "腾讯行情公开接口，仅作行情观察",
        "items": items,
        "errors": errors,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--watchlist", default="config/market_watchlist.json")
    args = parser.parse_args()
    watchlist_path = Path(args.watchlist)
    if not watchlist_path.exists():
        watchlist_path = None
    path = generate(Path(args.output_root), watchlist_path)
    print(f"[markets] generated {path}")


if __name__ == "__main__":
    main()
