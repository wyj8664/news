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
from news_freshness import CHINA, parse_publication_time, write_json
from briefing_window import delivery_window
from news_ranking import POLICY as RANKING_POLICY, rank_snapshot_progress

PIPELINE = "shared-home-v1"
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
            yield from entry.get("delivered_items", [])
        elif entry.get("status") == "complete":
            yield from entry.get("items", [])


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
                held.update(canonical_progress(item["progress_id"], aliases) for item in part["items"])
    return held


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
    for event in snapshot["events"]:
        category = event["category"]
        if not event.get("displayed_on_home") or category not in selected:
            reasons["outside_feishu_tracks"] += len(event["progresses"])
            continue
        docs = {doc["doc_id"]: doc for doc in event["reports"]}
        for progress in event["progresses"]:
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
            title = progress["title"]
            if title.startswith("客服回应："):
                title = re.split(r"[，,；;]", event["title"])[0] + "；" + title
            ranking = rank_snapshot_progress(event, progress, now)
            candidates.append({"track": category, "progress_id": key, "event_id": event["id"],
                "title": title, "url": representative["url"], "doc_ids": [doc["doc_id"] for doc in members],
                **ranking, "progress_type": progress["type"]})
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
            title = item["title"].replace("[", "［").replace("]", "］").replace("\n", " ")
            url = item["url"].replace("(", "%28").replace(")", "%29")
            lines.append(f"{index}. [{title}]({url})")
        if not selected[key]:
            lines.append("暂无新进展。")
        lines.append("")
    return "\n".join(lines)


def render_html(selected, now, batch, *, empty=False):
    # Reuse the website's presentation shell; no second classification engine.
    from curated_events import layout
    sections = []
    for key in FEISHU_CATEGORY_ORDER:
        rows = ''.join(f'<li><a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noreferrer">{html.escape(item["title"])}</a></li>' for item in selected[key])
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
