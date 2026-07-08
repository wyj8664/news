#!/usr/bin/env python3
"""Add a stable Daily Digest entry to generated TrendRadar HTML pages."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


STYLE_MARKER = "/* DAILY_DIGEST_ENTRY_STYLE */"
LINK_MARKER = "<!-- DAILY_DIGEST_ENTRY_LINK -->"
GLOBAL_STYLE_MARKER = "/* NEWSHOT_GLOBAL_NAV_STYLE_V2 */"
GLOBAL_NAV_MARKER = "<!-- NEWSHOT_GLOBAL_NAV -->"
RAW_STYLE_MARKER = "/* NEWSHOT_RAW_PAGE_STYLE */"

STYLE = f"""
            {STYLE_MARKER}
            .daily-digest-link {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-height: 38px;
                padding: 10px 14px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.2);
                color: #fff;
                font-size: 13px;
                font-weight: 600;
                text-decoration: none;
                line-height: 1;
                backdrop-filter: blur(10px);
                transition: all 0.2s ease;
                white-space: nowrap;
            }}

            .daily-digest-link:hover {{
                background: rgba(255, 255, 255, 0.3);
                transform: translateY(-1px);
            }}

            body.dark-mode .daily-digest-link {{
                border-color: rgba(255, 255, 255, 0.22);
                background: rgba(255, 255, 255, 0.16);
            }}
"""

LINK = f'{LINK_MARKER}\n                    <a class="daily-digest-link" href="/daily/" title="打开新闻日报归档">日报</a>'

GLOBAL_STYLE = f"""
            {GLOBAL_STYLE_MARKER}
            .newshot-global-topbar {{
                position: sticky;
                top: 0;
                z-index: 1000;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 18px;
                max-width: 1180px;
                margin: 0 auto;
                padding: 12px 18px;
                border-bottom: 1px solid rgba(226, 232, 240, 0.9);
                background: rgba(248, 250, 252, 0.96);
                backdrop-filter: blur(12px);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
            }}
            .newshot-global-left {{
                display: flex;
                align-items: center;
                min-width: 0;
            }}
            .newshot-global-brand {{
                color: #20293a;
                font-size: 22px;
                font-weight: 850;
                text-decoration: none;
                white-space: nowrap;
            }}
            .newshot-global-brand span {{
                color: #0d8fc8;
            }}
            .newshot-back-link,
            .newshot-global-nav a {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-height: 34px;
                padding: 7px 10px;
                border: 1px solid #e3e8f0;
                border-radius: 6px;
                background: #fff;
                color: #435066;
                font-size: 13px;
                font-weight: 750;
                line-height: 1;
                text-decoration: none;
                white-space: nowrap;
            }}
            .newshot-global-nav {{
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
                justify-content: flex-end;
            }}
            .newshot-global-nav a.active {{
                color: #fff;
                background: #20293a;
                border-color: #20293a;
            }}
            body.dark-mode .newshot-global-topbar {{
                border-bottom-color: rgba(255, 255, 255, 0.12);
                background: rgba(17, 24, 39, 0.96);
            }}
            body.dark-mode .newshot-global-brand,
            body.dark-mode .newshot-back-link,
            body.dark-mode .newshot-global-nav a {{
                color: #e5e7eb;
                border-color: rgba(255, 255, 255, 0.16);
                background: rgba(255, 255, 255, 0.08);
            }}
            body.dark-mode .newshot-global-nav a.active {{
                background: #e5e7eb;
                color: #111827;
                border-color: #e5e7eb;
            }}
            @media (max-width: 860px) {{
                .newshot-global-topbar {{
                    position: static;
                    align-items: flex-start;
                    flex-direction: column;
                }}
                .newshot-global-nav {{
                    width: 100%;
                    overflow-x: auto;
                    flex-wrap: nowrap;
                    padding-bottom: 2px;
                }}
            }}
"""

GLOBAL_NAV = f"""{GLOBAL_NAV_MARKER}
        <div class="newshot-global-topbar">
            <div class="newshot-global-left">
                <a class="newshot-global-brand" href="/">NEWS<span>HOT</span></a>
            </div>
            <nav class="newshot-global-nav">
                <a class="newshot-back-link" href="/" onclick="history.back(); return false;">返回</a>
                <a href="/">领域分类</a>
                <a href="/daily/">日报</a>
                <a href="/timeline/">热点脉络</a>
                <a class="active" href="/hotlists/">来源热榜</a>
            </nav>
        </div>"""


def matching_div_end(text: str, start: int) -> int:
    depth = 0
    for match in re.finditer(r"</?div\b[^>]*>", text[start:], flags=re.I):
        tag = match.group(0)
        if tag.lower().startswith("</div"):
            depth -= 1
            if depth == 0:
                return start + match.end()
        else:
            depth += 1
    return -1


def rss_block_urls(block: str) -> set[str]:
    return set(re.findall(r'<div class="rss-title">\s*<a href="([^"]+)"', block, flags=re.S))


def rss_block_authors(block: str) -> list[str]:
    authors = []
    for raw in re.findall(r'<span class="rss-author"(?:\s+[^>]*)?>(.*?)</span>', block, flags=re.S):
        author = re.sub(r"<[^>]+>", "", raw).strip()
        if author and author != "NEW" and author not in authors:
            authors.append(author)
    return authors


def dedupe_rss_sections(text: str) -> tuple[str, bool]:
    starts = [match.start() for match in re.finditer(r'<div class="section-divider rss-section">', text)]
    if not starts:
        return text, False

    sections = []
    for start in starts:
        end = matching_div_end(text, start)
        if end == -1:
            continue
        block = text[start:end]
        title_match = re.search(r'<div class="rss-section-title">(.*?)</div>', block, flags=re.S)
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
        sections.append({"start": start, "end": end, "block": block, "title": title, "urls": rss_block_urls(block)})

    if not sections:
        return text, False

    changed = False
    keep = sections[0]
    remove_ranges: list[tuple[int, int]] = []
    seen_url_sets = [keep["urls"]]
    for section in sections[1:]:
        duplicate = section["urls"] and any(section["urls"] == seen for seen in seen_url_sets)
        if duplicate or section["title"] == "RSS 新增更新":
            remove_ranges.append((section["start"], section["end"]))
            changed = True
        else:
            seen_url_sets.append(section["urls"])

    for start, end in reversed(remove_ranges):
        text = text[:start] + text[end:]

    keep_block = keep["block"]
    authors = rss_block_authors(keep_block)
    if len(authors) == 1 and "（" not in keep["title"]:
        old = '<div class="rss-section-title">RSS 订阅更新</div>'
        new = f'<div class="rss-section-title">RSS 订阅更新（{authors[0]}）</div>'
        if old in text:
            text = text.replace(old, new, 1)
            changed = True

    return text, changed


def upsert_global_nav(text: str) -> tuple[str, bool]:
    start = text.find(GLOBAL_NAV_MARKER)
    if start == -1:
        text, inserted = re.subn(r"(<body[^>]*>)", r"\1\n" + GLOBAL_NAV, text, count=1, flags=re.I)
        return text, bool(inserted)

    div_start = text.find('<div class="newshot-global-topbar"', start)
    if div_start == -1:
        replacement_end = start + len(GLOBAL_NAV_MARKER)
    else:
        div_end = matching_div_end(text, div_start)
        replacement_end = div_end if div_end != -1 else div_start
    old = text[start:replacement_end]
    if old == GLOBAL_NAV:
        return text, False
    return text[:start] + GLOBAL_NAV + text[replacement_end:], True

RAW_STYLE = f"""
            {RAW_STYLE_MARKER}
            body {{
                background: #f5f7fb !important;
                color: #172033 !important;
            }}
            .reading-progress {{
                background: #0d8fc8 !important;
            }}
            .container {{
                max-width: 1180px !important;
                margin: 0 auto !important;
                padding: 20px 18px 56px !important;
                background: transparent !important;
                border-radius: 0 !important;
                box-shadow: none !important;
                overflow: visible !important;
            }}
            .header {{
                margin: 24px 0 18px !important;
                padding: 0 0 18px !important;
                color: #172033 !important;
                text-align: left !important;
                background: transparent !important;
                border-bottom: 1px solid #e3e8f0 !important;
                overflow: visible !important;
            }}
            .header-watermark {{
                display: none !important;
            }}
            .header-title {{
                position: static !important;
                margin: 0 !important;
                color: #172033 !important;
                font-size: 28px !important;
                font-weight: 850 !important;
                line-height: 1.2 !important;
                text-align: left !important;
                text-shadow: none !important;
            }}
            .header-info {{
                display: grid !important;
                grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
                gap: 10px !important;
                margin-top: 18px !important;
            }}
            .info-item {{
                padding: 12px !important;
                background: #fff !important;
                border: 1px solid #e3e8f0 !important;
                border-radius: 8px !important;
                box-shadow: none !important;
            }}
            .info-label {{
                color: #627086 !important;
                font-size: 12px !important;
            }}
            .info-value {{
                margin-top: 6px !important;
                color: #172033 !important;
                font-size: 18px !important;
                font-weight: 850 !important;
            }}
            .save-buttons {{
                top: 0 !important;
                right: 0 !important;
                position: absolute !important;
            }}
            .toggle-wide-btn,
            .toggle-dark-btn,
            .save-btn,
            .save-dropdown-trigger {{
                min-height: 34px !important;
                padding: 7px 10px !important;
                border: 1px solid #e3e8f0 !important;
                border-radius: 6px !important;
                background: #fff !important;
                color: #435066 !important;
                font-size: 13px !important;
                font-weight: 750 !important;
                box-shadow: none !important;
                backdrop-filter: none !important;
            }}
            .save-btn {{
                border-radius: 6px 0 0 6px !important;
                border-right: 0 !important;
            }}
            .save-dropdown-trigger {{
                border-radius: 0 6px 6px 0 !important;
            }}
            .content {{
                padding: 0 !important;
                background: transparent !important;
            }}
            .search-bar {{
                margin: 0 0 18px !important;
            }}
            .search-input {{
                width: 100% !important;
                padding: 11px 13px !important;
                background: #fff !important;
                color: #172033 !important;
                border: 1px solid #e3e8f0 !important;
                border-radius: 8px !important;
                box-shadow: none !important;
            }}
            .standalone-section,
            .rss-section,
            .ai-section,
            .word-section {{
                margin-top: 24px !important;
                padding-top: 0 !important;
            }}
            .standalone-section-header,
            .rss-section-header,
            .ai-section-header {{
                margin-bottom: 14px !important;
            }}
            .standalone-section-title,
            .rss-section-title,
            .ai-section-title {{
                color: #172033 !important;
                font-size: 20px !important;
                font-weight: 850 !important;
            }}
            .standalone-section-count,
            .rss-section-count,
            .ai-section-count {{
                color: #627086 !important;
                font-size: 13px !important;
                font-weight: 700 !important;
            }}
            .tab-bar {{
                display: flex !important;
                gap: 8px !important;
                margin: 0 0 18px !important;
                padding: 0 0 2px !important;
                overflow-x: auto !important;
                border: 0 !important;
            }}
            .tab-btn {{
                flex: 0 0 auto !important;
                min-height: 36px !important;
                padding: 8px 10px !important;
                border: 1px solid #e3e8f0 !important;
                border-radius: 6px !important;
                background: #fff !important;
                color: #42506a !important;
                font-size: 13px !important;
                font-weight: 750 !important;
                box-shadow: none !important;
            }}
            .tab-btn.active {{
                background: #20293a !important;
                border-color: #20293a !important;
                color: #fff !important;
            }}
            .tab-count {{
                margin-left: 7px !important;
                padding: 2px 7px !important;
                border-radius: 999px !important;
                background: #f1f4f8 !important;
                color: #627086 !important;
            }}
            .tab-btn.active .tab-count {{
                background: rgba(255,255,255,.18) !important;
                color: #fff !important;
            }}
            .standalone-groups-grid,
            .rss-groups-grid {{
                display: block !important;
            }}
            .standalone-group,
            .rss-feed-group,
            .word-group {{
                margin-bottom: 22px !important;
                padding: 0 !important;
                background: #fff !important;
                border: 1px solid #e3e8f0 !important;
                border-radius: 8px !important;
                overflow: hidden !important;
                box-shadow: none !important;
            }}
            .standalone-header,
            .feed-header,
            .word-header {{
                margin: 0 !important;
                padding: 13px 14px !important;
                border-bottom: 1px solid #e3e8f0 !important;
                background: #fff !important;
            }}
            .standalone-name,
            .feed-name,
            .word-title {{
                color: #172033 !important;
                font-size: 17px !important;
                font-weight: 850 !important;
            }}
            .standalone-count,
            .feed-count,
            .word-count {{
                color: #627086 !important;
                font-size: 13px !important;
                font-weight: 700 !important;
            }}
            .news-item {{
                margin: 0 !important;
                padding: 12px 14px !important;
                border-bottom: 1px solid #e3e8f0 !important;
                background: #fff !important;
                align-items: flex-start !important;
            }}
            .news-item:last-child {{
                border-bottom: 0 !important;
            }}
            .news-number {{
                width: 34px !important;
                height: 28px !important;
                min-width: 34px !important;
                border-radius: 6px !important;
                background: #eef4ff !important;
                color: #185abc !important;
                font-size: 13px !important;
                font-weight: 850 !important;
            }}
            .news-title {{
                margin-top: 4px !important;
            }}
            .news-link {{
                color: #185abc !important;
                font-size: 16px !important;
                line-height: 1.45 !important;
                font-weight: 750 !important;
            }}
            .news-header {{
                gap: 8px !important;
                color: #627086 !important;
                font-size: 12px !important;
            }}
            .rank-num {{
                display: inline-flex !important;
                align-items: center !important;
                min-height: 22px !important;
                padding: 3px 8px !important;
                border-radius: 999px !important;
                background: #f1f4f8 !important;
                color: #536177 !important;
                font-size: 12px !important;
                font-weight: 750 !important;
            }}
            .rank-num.top,
            .rank-num.high {{
                background: #fff1ef !important;
                color: #d43c33 !important;
            }}
            .time-info,
            .count-info {{
                color: #627086 !important;
                font-size: 12px !important;
            }}
            .count-info {{
                color: #178260 !important;
                font-weight: 750 !important;
            }}
            body.dark-mode {{
                background: #111827 !important;
                color: #e5e7eb !important;
            }}
            body.dark-mode .container,
            body.dark-mode .content,
            body.dark-mode .header {{
                background: transparent !important;
            }}
            body.dark-mode .info-item,
            body.dark-mode .search-input,
            body.dark-mode .tab-btn,
            body.dark-mode .standalone-group,
            body.dark-mode .rss-feed-group,
            body.dark-mode .word-group,
            body.dark-mode .standalone-header,
            body.dark-mode .feed-header,
            body.dark-mode .word-header,
            body.dark-mode .news-item {{
                background: #1f2937 !important;
                border-color: rgba(255,255,255,.12) !important;
                color: #e5e7eb !important;
            }}
            body.dark-mode .header-title,
            body.dark-mode .info-value,
            body.dark-mode .standalone-section-title,
            body.dark-mode .standalone-name,
            body.dark-mode .feed-name,
            body.dark-mode .word-title {{
                color: #e5e7eb !important;
            }}
            @media (max-width: 860px) {{
                .container {{
                    padding: 16px 12px 44px !important;
                }}
                .header-info {{
                    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
                }}
                .save-buttons {{
                    position: static !important;
                    margin-bottom: 14px !important;
                }}
            }}
"""


def inject(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if "NEWSHOT_SOURCE_PAGE" in text:
        return False
    changed = False

    text, removed = re.subn(
        r"\n?\s*<!-- DAILY_DIGEST_ENTRY_LINK -->\s*\n\s*<a class=\"daily-digest-link\"[^>]*>日报</a>",
        "",
        text,
        count=1,
    )
    if removed:
        changed = True

    if STYLE_MARKER not in text:
        text = text.replace("        </style>", f"{STYLE}\n        </style>", 1)
        changed = True

    if GLOBAL_STYLE_MARKER not in text:
        text = text.replace("        </style>", f"{GLOBAL_STYLE}\n        </style>", 1)
        changed = True

    if RAW_STYLE_MARKER not in text:
        text = text.replace("        </style>", f"{RAW_STYLE}\n        </style>", 1)
        changed = True

    replacements = {
        "热点新闻分析": "来源热榜",
        "独立展示区": "来源热榜",
    }
    for old, new in replacements.items():
        if old in text:
            text = text.replace(old, new)
            changed = True

    text, nav_changed = upsert_global_nav(text)
    if nav_changed:
        changed = True

    text, rss_changed = dedupe_rss_sections(text)
    if rss_changed:
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    for raw_path in args.paths:
        path = Path(raw_path)
        changed = inject(path)
        print(f"[daily-link] {'patched' if changed else 'ok'} {path}")


if __name__ == "__main__":
    main()
