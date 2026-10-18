"""Calendar-day archive using the original daily template and shared event IDs.

Candidate ledgers are private, cumulative and separate from Feishu delivery state.
Only selected daily records are public. Never infer publication from crawl time.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path

from news_freshness import CHINA, china_time, load_cache, parse_publication_time, write_json
from news_event_clustering import deduplicate_reports, publisher_id, publisher_name, safe_link
from daily_digest import ArchiveEntry, render_html

PIPELINE = "site-daily-v2"
DATE_FIELDS = ("first_dt", "last_dt", "published_dt", "collected_dt")


def week_dates(now):
    return [(now - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(7)]


def private_directory(output_root, registry_path=None):
    registry_path = registry_path or output_root.resolve().parent / ".local-refresh" / f"events-{output_root.name}.sqlite3"
    return registry_path.parent / f"site-daily-{output_root.name}"


def read_object(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("pipeline") != PIPELINE:
        raise ValueError(f"Unsupported daily archive: {path.name}")
    return value


def backfill_events(output_root, registry_path, *, now=None):
    """Bootstrap saved RSS/hotlist history once per day, with zero web requests."""
    import curated_events as c
    now = china_time(now or datetime.now(CHINA))
    directory = private_directory(output_root, registry_path)
    marker = read_object(directory / "bootstrap.json")
    if marker.get("through") == now.strftime("%Y-%m-%d"):
        return []
    dates = week_dates(now)
    cache = load_cache(output_root / "freshness/publication-times.json")
    verified = []
    for date in dates:
        for item in [*c.load_hotlist_items(output_root, date), *c.load_rss_items(output_root, date)]:
            native = parse_publication_time(item.published_dt)
            record = cache.get(item.url, {})
            original = parse_publication_time(record.get("published_at")) if record.get("evidence") else None
            use_original = original is not None and (native is None or original < native)
            published = original if use_original else native
            evidence = record.get("evidence", "") if use_original else item.publication_source
            observed = parse_publication_time(item.collected_dt or item.last_dt)
            if (published is None or not evidence or published.strftime("%Y-%m-%d") not in dates
                    or published > now + timedelta(minutes=5)
                    or (observed and published > observed + timedelta(minutes=5))):
                continue
            verified.append(replace(item, published_dt=published.replace(tzinfo=None), publication_source=evidence))
    return c.cluster_home_events(deduplicate_reports(verified), registry_path=registry_path) if verified else []


def encode_item(item):
    data = asdict(item)
    for key in DATE_FIELDS:
        data[key] = china_time(data[key]).isoformat() if data[key] is not None else None
    return data


def decode_item(data):
    import curated_events as c
    values = dict(data)
    for key in DATE_FIELDS:
        values[key] = parse_publication_time(values.get(key))
    return c.RawItem(**values)


def canonical(key, aliases):
    seen = set()
    while key in aliases:
        if key in seen:
            raise ValueError("Daily archive ID alias cycle")
        seen.add(key)
        key = aliases[key]
    return key


def merge_progress(target, key, value):
    previous = target.setdefault(key, {**value, "doc_ids": []})
    previous["doc_ids"] = sorted(set(previous["doc_ids"]) | set(value["doc_ids"]))
    if value["first_reported_at"] < previous["first_reported_at"]:
        previous["first_reported_at"] = value["first_reported_at"]


def normalize_ledger(ledger, aliases):
    events = {}
    for event_id, progresses in ledger.get("events", {}).items():
        target = events.setdefault(canonical(event_id, aliases.get("events", {})), {})
        for progress_id, value in progresses.items():
            merge_progress(target, canonical(progress_id, aliases.get("progresses", {})), value)
    ledger["events"] = events


def ledger_events(ledger):
    import curated_events as c
    docs = {key: decode_item(value) for key, value in ledger["documents"].items()}
    result = []
    for event_id, nodes in ledger["events"].items():
        progresses = []
        for progress_id, node in nodes.items():
            members = [docs[key] for key in node["doc_ids"] if key in docs]
            if members:
                progresses.append(c.HomeProgress(progress_id, event_id, node["title"], node["kind"],
                    parse_publication_time(node["first_reported_at"]), members, [item.doc_id for item in members]))
        ids = sorted({key for progress in progresses for key in progress.doc_ids})
        if ids:
            result.append(c.Event(key=event_id, items=[docs[key] for key in ids], progresses=progresses))
    return result


def public_snapshot_events(records, date):
    """A new cloud checkout keeps already-published closed days, without private ledgers."""
    import curated_events as c
    result=[]
    for record in records:
        if record['category'] not in c.DAILY_CATEGORY_ORDER:
            raise ValueError('Invalid archived sector')
        docs={}
        for doc in record['reports']:
            at=parse_publication_time(doc['published_at'])
            if not at or at.strftime('%Y-%m-%d')!=date:
                raise ValueError('Archived publication date mismatch')
            source=doc.get('source_id') or doc['publisher_id']
            tier,kind,weight,trust=c.profile_for(source,doc['source_name'])
            docs[doc['doc_id']]=c.RawItem(date=date,title=doc['title'],url=safe_link(doc['url']),
                source_id=source,source_name=doc['source_name'],source_tier=tier,source_type=kind,
                source_weight=weight,source_trust=trust,kind='public-archive',first_dt=at,last_dt=at,
                published_dt=at,publication_source='published-daily-snapshot',doc_id=doc['doc_id'])
        progresses=[c.HomeProgress(node['id'],record['id'],node['title'],node['type'],
            parse_publication_time(node['first_reported_at']),[docs[key] for key in node['doc_ids']],node['doc_ids'])
            for node in record['progresses']]
        result.append(c.Event(key=record['id'],title=record['title'],url=record['url'],items=list(docs.values()),
            source_ids={publisher_id(item) for item in docs.values()},category=record['category'],
            score=record['score'],progresses=progresses))
    return result


def event_record(event):
    return {"id": event.key, "title": event.title, "url": safe_link(event.url), "category": event.category,
        "score": event.score, "source_count": event.source_count,
        "reports": [{"doc_id": item.doc_id, "title": item.title, "url": safe_link(item.url),
            "source_name": item.source_name, "publisher_id": publisher_id(item),
            "published_at": china_time(item.published_dt).isoformat()} for item in event.items],
        "progresses": [{"id": progress.key, "title": progress.title, "type": progress.kind,
            "first_reported_at": china_time(progress.first_reported_at).isoformat(),
            "doc_ids": progress.doc_ids} for progress in event.progresses]}


def event_row(event, rank):
    import curated_events as c
    title = c.e(event.title)
    url = safe_link(event.url)
    title_html = f'<a href="{c.e(url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
    sources = " / ".join(sorted({publisher_name(item) for item in event.items}))
    dates = sorted(china_time(item.published_dt) for item in event.items)
    span = dates[0].strftime("%H:%M") + "~" + dates[-1].strftime("%H:%M")
    nodes = []
    for progress in sorted(event.progresses, key=lambda p: (p.first_reported_at, p.key), reverse=True):
        links = "".join(f'<li data-doc-id="{c.e(item.doc_id)}"><a href="{c.e(safe_link(item.url))}" target="_blank" rel="noopener noreferrer">{c.e(item.title)}</a> · {c.e(item.source_name)} · {china_time(item.published_dt):%m-%d %H:%M}</li>'
                        for item in sorted(progress.items, key=lambda i: (china_time(i.published_dt), i.doc_id)))
        nodes.append(f'<section data-progress-id="{c.e(progress.key)}"><p>{c.e(c.TYPE_LABELS.get(progress.kind,"进展"))} · {c.e(progress.title)}</p><ul>{links}</ul></section>')
    details = (f'<details><summary>展开 {len(event.progresses)} 个进展 · {len(event.items)} 条来源报道</summary>{"".join(nodes)}</details>'
               if len(event.items)>1 or len(event.progresses)>1 else "")
    return f'''<article class="item" data-event-id="{c.e(event.key)}" data-category="{c.e(event.category)}" data-score="{event.score:.1f}" data-progress-ids="{c.e(json.dumps([p.key for p in event.progresses]))}" data-doc-ids="{c.e(json.dumps([i.doc_id for i in event.items]))}">
      <div class="rank">{rank}</div><div class="body">
        <div class="topline"><span class="badge">{c.e(c.home_category_label(event.category))}</span><span>{c.e(sources)} · {span} · {event.source_count}源</span></div>
        <h2>{title_html}</h2>{details}
      </div></article>'''


def publish(events, output_root, top=50, *, now=None, registry_path=None, aliases=None):
    import curated_events as c
    now = china_time(now or datetime.now(CHINA))
    dates = week_dates(now)
    directory = private_directory(output_root, registry_path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    aliases = aliases or {}
    ledgers = {}
    for date in dates:
        ledger = read_object(directory / f"{date}.json") or {"pipeline": PIPELINE, "date": date, "documents": {}, "events": {}}
        saved=output_root / 'daily' / f'{date}.json'
        if date<dates[0] and not ledger['documents'] and not ledger['events'] and 'frozen' not in ledger and saved.is_file():
            snapshot=json.loads(saved.read_text())
            if snapshot.get('pipeline')==PIPELINE and snapshot.get('date')==date:
                # Public selected records contain their original score/IDs; do not
                # invent old source weights or replace them with a partial backfill.
                public_snapshot_events(snapshot['events'],date)
                ledger['frozen']=snapshot['events']
        normalize_ledger(ledger, aliases)
        ledgers[date] = ledger
    for event in events:
        event_id = canonical(event.key, aliases.get("events", {}))
        for progress in event.progresses:
            by_day = defaultdict(list)
            for item in progress.items:
                published = parse_publication_time(item.published_dt)
                if published and item.publication_source and published <= now + timedelta(minutes=5):
                    date = published.strftime("%Y-%m-%d")
                    if date in ledgers:
                        by_day[date].append(item)
            for date, members in by_day.items():
                ledger = ledgers[date]
                if 'frozen' in ledger:
                    continue
                for item in members:
                    encoded = encode_item(item)
                    previous = ledger["documents"].get(item.doc_id)
                    if previous and previous["published_dt"] < encoded["published_dt"]:
                        encoded["published_dt"] = previous["published_dt"]
                    ledger["documents"][item.doc_id] = encoded
                node = {"kind": progress.kind, "title": c.clean_headline(c.home_representative(members)),
                        "first_reported_at": china_time(progress.first_reported_at).isoformat(),
                        "doc_ids": [item.doc_id for item in members]}
                target = ledger["events"].setdefault(event_id, {})
                merge_progress(target, canonical(progress.key, aliases.get("progresses", {})), node)
    selected = {}
    for date, ledger in ledgers.items():
        ledger["updated_at"] = now.isoformat()
        write_json(directory / f"{date}.json", ledger)
        (directory / f"{date}.json").chmod(0o600)
        selected[date] = (public_snapshot_events(ledger['frozen'],date)[:min(50,top)] if 'frozen' in ledger
                          else c.select_site_daily(ledger_events(ledger), top, now=now, date=date))
    entries = [ArchiveEntry(date, selected[date][0].title if selected[date] else "暂无已核实内容", len(selected[date])) for date in dates]
    daily = output_root / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    for date in dates:
        chosen = selected[date]
        rows = "".join(event_row(event, rank) for rank, event in enumerate(chosen, 1))
        if not rows:
            rows = '<p id="daily-empty" role="status">当天暂无已核实内容。</p>'
        subtitle = f"七赛道 · 综合排序 · 共{len(chosen)}条 · 更新于 {now:%Y-%m-%d %H:%M}（北京时间）"
        page = render_html([], date, now.strftime("%Y-%m-%d %H:%M"), min(50, top), entries,
            rows_html=rows, subtitle=subtitle, main_attributes=f'data-daily-date="{date}" data-daily-generation="{now.isoformat()}"')
        c.atomic_text(daily / f"{date}.html", page)
        payload = {"schema_version": 2, "pipeline": PIPELINE, "date": date, "generated_at": now.isoformat(),
            "period_start": f"{date}T00:00:00+08:00", "period_end": (datetime.strptime(date,"%Y-%m-%d").replace(tzinfo=CHINA)+timedelta(days=1)).isoformat(),
            "categories": list(c.DAILY_CATEGORY_ORDER), "events": [event_record(event) for event in chosen]}
        write_json(daily / f"{date}.json", payload)
        lines = [f"# {date} 新闻日报 Top 50", ""]
        for rank, event in enumerate(chosen, 1):
            lines.extend([f"{rank}. [{event.title}]({safe_link(event.url)}) · {c.home_category_label(event.category)} · 综合分 {event.score:.1f}", ""])
        c.atomic_text(daily / f"{date}.md", "\n".join(lines))
        if date == dates[0]:
            c.atomic_text(daily / "index.html", page)
            c.atomic_text(daily / "latest.md", "\n".join(lines))
    write_json(directory / "bootstrap.json", {"pipeline": PIPELINE, "through": dates[0]})
