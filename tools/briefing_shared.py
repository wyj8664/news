"""Feishu selection from the authoritative homepage snapshot, with no collection.

Grain: one stable progress ID, supporting many source documents. Neither a new
repost nor a later crawl resets first_reported_at. Delivery history is all-time.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import html
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from event_registry import statements, same_progress
from home_categories import CATEGORY_LABELS, CATEGORY_ORDER
from news_event_clustering import article_url, normalize
from news_event_clustering import features, event_match, grams
from news_fact_identity import fact_identity, same_fact, event_key
from news_editorial_quality import rejection_reason
from news_freshness import CHINA, parse_publication_time, write_json
from briefing_window import delivery_window
from news_ranking import POLICY as RANKING_POLICY, rank_snapshot_progress

PIPELINE = "shared-home-v1"
SELECTION_POLICY = 'fact-aware-v1'
COMMODITY_ROUNDUP = 'commodity-roundup-v1'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "output/source-pool/home-events.json"
# Output policy only: the website's eight-domain contract remains authoritative.
FEISHU_CATEGORY_ORDER = tuple(key for key in CATEGORY_ORDER if key != "society")


def read_object(path: Path, *, missing_ok=False):
    if missing_ok and not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def canonical_progress(key, aliases):
    seen = set()
    while key in aliases:
        if key in seen:
            raise ValueError("Progress alias cycle")
        seen.add(key)
        key = aliases[key]
    return key


def delivered_items(history):
    """Only acknowledged parts count; pending/uncertain messages are not success."""
    for entry in history.get("batches", {}).values():
        if entry.get("pipeline") == PIPELINE:
            for item in entry.get("delivered_items", []):
                yield from covered_items(item)
        elif entry.get("status") == "complete":
            for item in entry.get("items", []):
                yield from covered_items(item)


def covered_items(item):
    """One visible row may acknowledge several independently proven progresses."""
    yield item
    yield from item.get('related_items',[])


def sent_progress_ids(history, aliases):
    return {canonical_progress(str(item["progress_id"]), aliases)
            for item in delivered_items(history) if item.get("progress_id")}


def uncertain_progress_ids(history, aliases, *, exclude_batch=None):
    held = set()
    for key, entry in history.get("batches", {}).items():
        if key == exclude_batch:
            continue
        for part in entry.get("parts", []):
            if part.get("status") in {"sending", "uncertain"}:
                held.update(canonical_progress(member["progress_id"], aliases)
                            for item in part["items"] for member in covered_items(item))
    return held


def content_same(a,b):
    if a.get('progress_type')!=b.get('progress_type'):
        return False
    ta,tb=(parse_publication_time(x.get('first_reported_at')) for x in (a,b))
    if not ta or not tb or ta.date()!=tb.date():
        return False
    typed=same_fact(a.get('fact_identity') or fact_identity(a.get('title','')),
                    b.get('fact_identity') or fact_identity(b.get('title','')))
    if typed is not None:
        return typed
    if min(len(a.get('title','')),len(b.get('title',''))) < 12:
        return False
    aa,bb=(SimpleNamespace(title=x.get('title',''),summary='',url='') for x in (a,b))
    left=[s for s in statements(aa) if s.kind==a.get('progress_type')]
    right=[s for s in statements(bb) if s.kind==b.get('progress_type')]
    return event_match(features(aa),features(bb))[0]>=.85 and any(same_progress(x,y) for x in left for y in right)


class ContentIndex:
    """Bounded candidate checks, not all-history pairwise semantic comparison."""
    def __init__(self):
        self.rows=[];self.index={}

    def keys(self,item):
        at=parse_publication_time(item.get('first_reported_at'))
        if not at:
            return []
        prefix=at.date().isoformat()+'|'+str(item.get('progress_type'))+'|'
        fact=item.get('fact_identity') or fact_identity(item.get('title',''))
        keys={'fact:'+event_key(fact)} if fact else set()
        keys.update('gram:'+g for g in sorted(grams(normalize(item.get('title','')),4))[:80])
        return [prefix+k for k in keys]

    def add(self,item):
        index=len(self.rows);self.rows.append(item)
        for key in self.keys(item):
            self.index.setdefault(key,[]).append(index)

    def match(self,item):
        hits=Counter(i for key in self.keys(item) for i in self.index.get(key,[]))
        return next((self.rows[i] for i,_ in hits.most_common(40) if content_same(item,self.rows[i])),None)


def history_content_indexes(history,start,end,*,exclude_batch=None):
    past,uncertain=ContentIndex(),ContentIndex()
    for item in delivered_items(history):
        at=parse_publication_time(item.get('first_reported_at'))
        if at and start.date()<=at.date()<=end.date():
            past.add(item)
    for key,entry in history.get('batches',{}).items():
        if key==exclude_batch:
            continue
        for part in entry.get('parts',[]):
            if part.get('status') in {'sending','uncertain'}:
                for item in part.get('items',[]):
                    for member in covered_items(item):
                        at=parse_publication_time(member.get('first_reported_at'))
                        if at and start.date()<=at.date()<=end.date():
                            uncertain.add(member)
    return past,uncertain


def merge_coverage(winner,other):
    old={x['progress_id'] for x in covered_items(winner)}
    winner.setdefault('related_items',[]).extend(base_item(x)
        for x in covered_items(other) if x['progress_id'] not in old)


def base_item(item):
    """Strip presentation-only metadata, never change a factual node's fields."""
    return {k:v for k,v in item.items() if k not in {'related_items','presentation_policy'}}


def market_series_key(item):
    fact=item.get('fact_identity')
    if not fact or fact.get('family')!='market':
        return None
    day=parse_publication_time(item['first_reported_at']).date().isoformat()
    return (item['track'],day,*(fact.get(k,'') for k in ('actor','contract','phase','direction','currency')))


def commodity_key(item):
    """An editorial beat, NOT an event/progress identity or a comparable price."""
    fact=item.get('fact_identity') or {}
    if item.get('track')!='commodities' or fact.get('family')!='market':
        return None
    actor=fact.get('actor','')
    metal={'现货黄金':'黄金','comex黄金':'黄金','现货白银':'白银','comex白银':'白银'}
    name=metal.get(actor)
    if not name:
        match=re.fullmatch(r'(布伦特原油|wti原油|焦煤|焦炭|铁矿石|螺纹钢|沪铜|沪铝|碳酸锂)(?:期货|现货)?',actor)
        name=match[1] if match else None
    at=parse_publication_time(item.get('first_reported_at'))
    if not name or not at or fact.get('direction') not in {'up','down'} or fact.get('phase') not in {'intraday','open','close'}:
        return None
    # Keep different days, open/close, currencies and reversals separate. Brent
    # and WTI are different beats. Contract/spot/futures differences stay in the
    # component records and must not be presented as a single price trajectory.
    return (name,at.date().isoformat(),fact['phase'],fact['direction'],fact.get('currency',''))


def validate_coverage_group(item):
    members=list(covered_items(item))
    if len({m['progress_id'] for m in members})!=len(members) or any(m.get('related_items') for m in members[1:]):
        raise ValueError('Duplicate or nested covered progress')
    if 'presentation_policy' in item:
        key=commodity_key(item)
        if item['presentation_policy']!=COMMODITY_ROUNDUP or not key or len(members)<2:
            raise ValueError('Invalid commodity presentation policy')
        if any(commodity_key(m)!=key or m.get('presentation_policy')
               or parse_publication_time(m['first_reported_at'])>parse_publication_time(item['first_reported_at'])
               for m in members[1:]):
            raise ValueError('Unrelated or newer commodity quote in roundup')
        return
    for other in members[1:]:
        if content_same(item,other):
            continue
        if not market_series_key(item) or market_series_key(item)!=market_series_key(other) or parse_publication_time(other['first_reported_at'])>parse_publication_time(item['first_reported_at']):
            raise ValueError('Unrelated or newer progress hidden in one row')
        probe=compact_market_series([base_item(x) for x in (other,item)],Counter())
        if len(probe)!=1:
            raise ValueError('Contradictory market prices')


def compact_market_series(items,reasons):
    """Feishu-only latest update; registry/site retain every real milestone."""
    groups={};ordinary=[]
    for item in items:
        fact=item.get('fact_identity')
        if not fact or fact.get('family')!='market':
            ordinary.append(item);continue
        key=market_series_key(item)
        groups.setdefault(key,[]).append(item)
    for group in groups.values():
        group.sort(key=lambda x:(parse_publication_time(x['first_reported_at']),x['progress_id']))
        # Contradictory price barriers (possibly different unlabelled contracts)
        # cannot be compressed into a manufactured price trajectory.
        prices=[float(x['fact_identity']['value']) for x in group if x['fact_identity']['metric']=='threshold']
        direction=group[0]['fact_identity']['direction']
        conflict=any((b>a if direction=='down' else b<a) for a,b in zip(prices,prices[1:]))
        if conflict:
            reasons['market_price_conflict_kept']+=1
            ordinary.extend(x for x in group if x['fact_identity']['metric']=='threshold')
            ordinary.extend(compact_market_series([x for x in group if x['fact_identity']['metric']!='threshold'],reasons))
            continue
        latest=group[-1]
        for old in group[:-1]:
            merge_coverage(latest,old)
            reasons['market_updates_compacted']+=1
        ordinary.append(latest)
    return ordinary


def compact_markets(items,reasons):
    """One commodity beat per batch; keep all independently verified facts.

    This deliberately does not merge registry identities. Ambiguous contract
    prices are retained as labelled details, not forged into a common quote.
    """
    groups={};other=[]
    for item in items:
        key=commodity_key(item)
        if key:
            groups.setdefault(key,[]).append(item)
        else:
            other.append(item)
    output=compact_market_series(other,reasons)
    for group in groups.values():
        if len(group)==1:
            output.extend(group);continue
        members=[base_item(m) for row in group for m in covered_items(row)]
        members.sort(key=lambda x:(parse_publication_time(x['first_reported_at']),x['progress_id']))
        latest=members[-1]
        for old in members[:-1]:
            merge_coverage(latest,old)
        reasons['market_updates_compacted']+=len(group)-1
        latest['presentation_policy']=COMMODITY_ROUNDUP
        validate_coverage_group(latest)
        output.append(latest)
    return output


def presentation_title(item):
    """Derive captions from bound evidence; never trust a stored display title."""
    if 'presentation_policy' not in item:
        return item['title']
    validate_coverage_group(item)
    members=sorted(covered_items(item),key=lambda x:(parse_publication_time(x['first_reported_at']),x['progress_id']))
    series={}
    for member in members:
        series.setdefault(market_series_key(member),[]).append(member)
    name,_,phase,direction,_=commodity_key(item)
    name='WTI原油' if name=='wti原油' else name
    action={'intraday':'日内','open':'开盘','close':'收盘'}[phase]+('上涨' if direction=='up' else '下跌')
    # Only use a latest numeric title when one exact series has no conflicting
    # barriers. Otherwise the caption states the common direction, not a price.
    conflict=any(len(compact_market_series([base_item(m) for m in group],Counter()))!=1 for group in series.values())
    if conflict:
        return f'{name}{action}（多来源报价）'
    if len(series)==1:
        return item['title']
    if name in {'黄金','白银'} and all(not m['fact_identity'].get('contract') for m in members):
        titles=[group[-1]['title'].rstrip('。；; ') for _,group in sorted(series.items())]
        return f'{name}行情：'+'；'.join(titles)
    return f'{name}{action}（不同合约/口径行情）'


def presentation_links(item):
    title=presentation_title(item)
    links=[(title,item['url'])]
    if item.get('presentation_policy')==COMMODITY_ROUNDUP:
        members=sorted(covered_items(item),key=lambda x:(x['first_reported_at'],x['progress_id']),reverse=True)
        counts=Counter(m['url'] for m in members)
        # Prefer the concrete article supporting the most components over a feed
        # homepage, without altering any component's provenance or canonical URL.
        primary=max(members,key=lambda m:(bool(article_url(m['url'])),counts[m['url']],m['first_reported_at'],m['progress_id']))['url']
        links=[(title,primary)]
        seen={primary}
        for member in members:
            if member['url'] not in seen:
                seen.add(member['url'])
                links.append((f'行情来源{len(links)+1}',member['url']))
    return links


def legacy_matches(snapshot, history):
    """Conservatively bridge old title-hash IDs to current factual progress IDs.

    Require the same concrete URL and headline, then the same fact type/claim.
    A changed article at the same URL must not mark its new fact as sent.
    Unmapped legacy partial deliveries remain explicitly unresolved, not success.
    """
    old = {}
    held = {}
    for entry in history.get("batches", {}).values():
        if entry.get("pipeline") == PIPELINE:
            continue
        target = old if entry.get("status") == "complete" else held if entry.get("parts_sent") else None
        if target is None:
            continue
        for item in entry.get("items", []):
            url = article_url(item.get("url", ""))
            if url:
                target[(url, normalize(item.get("title", "")))] = item.get("title", "")
    sent, uncertain = set(), set()
    for event in snapshot["events"]:
        docs = {doc["doc_id"]: doc for doc in event["reports"]}
        for progress in event["progresses"]:
            for mapping, result in ((old, sent), (held, uncertain)):
                for doc_id in progress["doc_ids"]:
                    doc = docs[doc_id]
                    title = (mapping.get((article_url(doc["url"]), normalize(doc["title"])))
                             or mapping.get((article_url(doc["url"]), normalize(progress["title"]))))
                    if not title:
                        continue
                    old_facts = statements(SimpleNamespace(title=title, summary="", source_id=doc["source_id"], url=doc["url"]))
                    new_facts = statements(SimpleNamespace(title=progress["title"], summary="", source_id=doc["source_id"], url=doc["url"]))
                    if any(a.kind == progress["type"] and b.kind == progress["type"]
                           and not (a.kind == 'response' and a.claim == 'unspecified' and b.claim != 'unspecified')
                           and same_progress(a, b)
                           for a in old_facts for b in new_facts):
                        result.add(progress["id"])
    return sent, uncertain


def validate_snapshot(snapshot, now, *, max_age_hours=2):
    if snapshot.get("schema_version") != 3 or snapshot.get("pipeline") != PIPELINE:
        raise ValueError("网站事件快照需要由当前版本重新生成；日报不会退回旧采集器。")
    if 'ranking_policy' in snapshot and snapshot['ranking_policy'] != RANKING_POLICY:
        raise ValueError('未知排序规则，停止发送。')
    generated = parse_publication_time(snapshot.get("generated_at"))
    if not generated or generated > now + timedelta(minutes=5) or now - generated > timedelta(hours=max_age_hours):
        raise ValueError("网站事件快照过期或时间无效，停止发送；请检查来源更新任务。")
    expected = [{"id": key, "name": CATEGORY_LABELS[key]} for key in CATEGORY_ORDER]
    if snapshot.get("categories") != expected:
        raise ValueError("网站分类契约不一致，停止发送。")
    events, progresses = set(), set()
    for event in snapshot.get("events", []):
        if not event.get("id") or event["id"] in events:
            raise ValueError("Duplicate/missing event ID")
        events.add(event["id"])
        docs = {doc["doc_id"] for doc in event["reports"]}
        if len(docs) != len(event["reports"]):
            raise ValueError("Duplicate document ID")
        if not math.isfinite(float(event["score"])):
            raise ValueError("Invalid website ranking score")
        for doc in event["reports"]:
            if not parse_publication_time(doc.get("published_at")) or not doc.get("publication_source"):
                raise ValueError("Missing verified original publication")
        for progress in event["progresses"]:
            if 'importance_score' in progress and (not math.isfinite(float(progress['importance_score'])) or float(progress['importance_score']) < 0):
                raise ValueError('Invalid progress importance')
            key = progress.get("id")
            if not key or key in progresses or progress.get("event_id") != event["id"]:
                raise ValueError("Duplicate/orphan progress ID")
            progresses.add(key)
            if not progress["doc_ids"] or len(set(progress["doc_ids"])) != len(progress["doc_ids"]) or not set(progress["doc_ids"]) <= docs:
                raise ValueError("Invalid progress-document join")
            if not parse_publication_time(progress.get("first_reported_at")):
                raise ValueError("Missing original progress time")


def select_snapshot(snapshot, history, now, batch, *, limit=20):
    validate_snapshot(snapshot, now)
    window = delivery_window(now, batch)
    cutoff = parse_publication_time(window["window_start"])
    aliases = snapshot.get("id_aliases", {}).get("progresses", {})
    sent = sent_progress_ids(history, aliases)
    held = uncertain_progress_ids(history, aliases)
    legacy_sent, legacy_held = legacy_matches(snapshot, history)
    sent.update(legacy_sent)
    held.update(legacy_held)
    selected = {key: [] for key in FEISHU_CATEGORY_ORDER}
    candidates = []
    reasons = Counter()
    past,uncertain=history_content_indexes(history,cutoff,now)
    for event in snapshot["events"]:
        category = event["category"]
        if not event.get("displayed_on_home") or category not in selected:
            reasons["outside_feishu_tracks"] += len(event["progresses"])
            continue
        docs = {doc["doc_id"]: doc for doc in event["reports"]}
        for progress in event["progresses"]:
            identity=progress.get('fact_identity') or {}
            if identity.get('family')=='consumer-safety' and identity.get('action')=='background':
                reasons['consumer_background_not_new_progress']+=1
                continue
            key = canonical_progress(progress["id"], aliases)
            at = parse_publication_time(progress["first_reported_at"])
            if key in sent:
                reasons["already_sent_progress"] += 1
                continue
            if key in held:
                reasons["uncertain_previous_delivery"] += 1
                continue
            if not cutoff <= at <= now:
                reasons["outside_progress_window"] += 1
                continue
            members = [docs[key] for key in progress["doc_ids"]
                       if cutoff <= parse_publication_time(docs[key]["published_at"]) <= now]
            members = [doc for doc in members if urlsplit(doc["url"]).scheme in {"https", "http"} and urlsplit(doc["url"]).hostname]
            if not members:
                reasons["no_current_linked_document"] += 1
                continue
            representative = next((doc for doc in members if doc["doc_id"] == progress.get("representative_doc_id")), members[0])
            # Prefer an actual freely linked report over a feed homepage or an
            # investment-column teaser when equivalent factual coverage exists.
            if not article_url(representative['url']) or re.search(r'^【(?:研选|盘中宝|电报解读)',representative['title']):
                representative=next((doc for doc in members if article_url(doc['url'])
                    and not re.search(r'^【(?:研选|盘中宝|电报解读)',doc['title'])),representative)
            title = progress["title"]
            if title.startswith("客服回应："):
                title = re.split(r"[，,；;]", event["title"])[0] + "；" + title
            ranking = rank_snapshot_progress(event, progress, now)
            rejection=rejection_reason(title)
            if rejection:
                reasons[rejection]+=1;continue
            candidate={"track": category, "progress_id": key, "event_id": event["id"],
                "title": title, "url": representative["url"], "doc_ids": [doc["doc_id"] for doc in members],
                **ranking, "progress_type": progress["type"],
                'fact_identity':progress.get('fact_identity') or fact_identity(progress['title'])}
            if past.match(candidate):
                reasons['already_sent_same_fact']+=1;continue
            if uncertain.match(candidate):
                reasons['uncertain_same_fact']+=1;continue
            candidates.append(candidate)
    deduped=[];within=ContentIndex()
    for item in sorted(candidates,key=lambda x:(-x['score'],x['progress_id'])):
        duplicate=within.match(item)
        if duplicate:
            merge_coverage(duplicate,item);reasons['same_fact_duplicate']+=1
        else:
            within.add(item);deduped.append(item)
    candidates=compact_markets(deduped,reasons)
    seen = set()
    for item in sorted(candidates, key=lambda item: (-item["score"], -parse_publication_time(item["first_reported_at"]).timestamp(), item["progress_id"])):
        if item["progress_id"] in seen:
            continue
        seen.add(item["progress_id"])
        if len(selected[item["track"]]) >= limit:
            reasons["track_limit"] += 1
            continue
        selected[item["track"]].append(item)
    return selected, dict(reasons), len(sent)


def render_markdown(selected, now, batch):
    lines = [f"# {now:%Y-%m-%d} {batch[:2]}:{batch[2:]} 编辑选题池", "",
             f"> dry-run 预览，生成于 {now:%Y-%m-%d %H:%M:%S}，尚未发送飞书。", ""]
    for key in FEISHU_CATEGORY_ORDER:
        lines.extend([f"## {CATEGORY_LABELS[key]}", ""])
        for index, item in enumerate(selected[key], 1):
            links=[]
            for title,url in presentation_links(item):
                title=title.replace('[','［').replace(']','］').replace('\n',' ')
                url=url.replace('(','%28').replace(')','%29')
                links.append(f'[{title}]({url})')
            lines.append(f"{index}. "+' · '.join(links))
        if not selected[key]:
            lines.append("暂无新进展。")
        lines.append("")
    return "\n".join(lines)


def render_html(selected, now, batch, *, empty=False):
    # Reuse the website's presentation shell; no second classification engine.
    from curated_events import layout
    sections = []
    for key in FEISHU_CATEGORY_ORDER:
        rows=[]
        for item in selected[key]:
            links=' · '.join(f'<a href="{html.escape(url,quote=True)}" target="_blank" rel="noreferrer">{html.escape(title)}</a>' for title,url in presentation_links(item))
            details=''
            if item.get('presentation_policy')==COMMODITY_ROUNDUP:
                quotes=sorted(covered_items(item),key=lambda x:(x['first_reported_at'],x['progress_id']),reverse=True)
                evidence=''.join(f'<li>{html.escape(m["first_reported_at"][11:16])} <a href="{html.escape(m["url"],quote=True)}" target="_blank" rel="noreferrer">{html.escape(m["title"])}</a></li>' for m in quotes)
                details=f'<details class="quote-evidence"><summary>查看 {len(quotes)} 条原始行情</summary><ul>{evidence}</ul></details>'
            rows.append(f'<li>{links}{details}</li>')
        rows=''.join(rows)
        sections.append(f'<section class="panel"><h2>{CATEGORY_LABELS[key]}</h2><ol>{rows or "<li>暂无新进展。</li>"}</ol></section>')
    badge = '本批无新进展 · 未发消息' if empty else 'DRY-RUN · 未发送飞书'
    return layout(f"{now:%Y-%m-%d} {batch[:2]}:{batch[2:]} 编辑选题池", f'<div class="page-head"><h1>飞书日报预览</h1><span class="badge">{badge}</span></div>' + ''.join(sections), "daily")


def write_preview(report, output_dir):
    now = parse_publication_time(report['generated_at'])
    batch = report['batch']
    selected = {key: [] for key in FEISHU_CATEGORY_ORDER}
    for item in report['selected_items']:
        if item['track'] in selected:
            selected[item['track']].append(item)
    archive = output_dir / now.strftime('%Y-%m-%d')
    archive.mkdir(parents=True, exist_ok=True)
    from curated_events import atomic_text
    markdown, page = render_markdown(selected, now, batch), render_html(selected, now, batch, empty=report.get('mode')=='empty')
    for path in (output_dir / 'latest.md', archive / f'{batch}.md'):
        atomic_text(path, markdown)
    for path in (output_dir / 'latest.html', output_dir / 'index.html', archive / f'{batch}.html'):
        atomic_text(path, page)
    write_json(output_dir / 'debug/report.json', report)
    write_json(archive / f'{batch}.json', report)


def generate(snapshot_path, history_path, output_dir, now, batch, *, limit=20, refresh_gate=None):
    snapshot = read_object(snapshot_path)
    history = read_object(history_path, missing_ok=True)
    selected, reasons, sent_count = select_snapshot(snapshot, history, now, batch, limit=limit)
    items = [item for key in FEISHU_CATEGORY_ORDER for item in selected[key]]
    report = {"pipeline": PIPELINE, "mode": "dry-run", "batch_id": f"{now:%Y%m%d}-{batch}",
        "batch": batch, "batch_time": batch[:2] + ':' + batch[2:], "generated_at": now.isoformat(),
        **delivery_window(now, batch), "limit_per_track": limit, "ranking_policy": RANKING_POLICY,
        "selection_policy":SELECTION_POLICY,
        "delivery_tracks": list(FEISHU_CATEGORY_ORDER),
        "snapshot_generated_at": snapshot["generated_at"], "id_aliases": snapshot.get("id_aliases", {}),
        "source_status": snapshot.get("source_status", []), "selected_items": items,
        "metrics": {"selected": len(items), "selected_by_track": {key: len(selected[key]) for key in FEISHU_CATEGORY_ORDER},
                    "sent_progress_ids_loaded": sent_count, "rejection_reasons": reasons},
        "feishu_sent": False, "sent_history_written": False}
    if refresh_gate is not None:
        report['refresh_gate']=refresh_gate
    write_preview(report, output_dir)
    return report
