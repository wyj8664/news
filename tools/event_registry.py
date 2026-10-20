"""Private SQLite event → progress ↔ document registry for the classified home.

IDs are allocated once, never derived from the current title or member list.
No model/API calls: cheap document identity, cached features and an indexed,
bounded candidate set precede fact matching. The DB must NOT be in output/.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from types import SimpleNamespace

from news_event_clustering import (
    Features, article_url, clean_headline, event_match, features, grams,
    headline_views, normalize, publisher_id,
)
from news_freshness import china_time
from news_fact_normalization import financial_scope, quantity_tokens, scopes_conflict
from news_fact_identity import fact_identity, same_fact, event_key, market_parts, market_fact, policy_objects, policy_conflict, dated_policy

VERSION = 5
MAX_CANDIDATES = 40
MAX_REPRESENTATIVES = 4
TYPE_LABELS = {
    "report": "事件披露", "response": "回应", "recall": "召回", "withdrawal": "下架",
    "investigation": "立案调查", "penalty": "处罚", "judgment": "裁判结果",
    "opening": "开幕", "buyback-proposal": "回购提议", "buyback-approved": "回购获批",
    "buyback-completed": "回购实施", "analysis": "相关分析",
    "agreement-terminated": "合作终止", "approval": "获批",
}


def new_id(prefix):
    return f"{prefix}:{uuid.uuid4().hex}"


def document_id(item):
    identity = article_url(item.url) or getattr(item, "native_id", "")
    if not identity:
        identity = normalize(item.title) + "|" + china_time(item.published_dt or item.first_dt).isoformat()
    return "doc:" + hashlib.sha256((publisher_id(item) + "|" + identity).encode()).hexdigest()[:24]


def encode_feature(value):
    return json.dumps({key: sorted(data) if isinstance(data, set) else data
                       for key, data in asdict(value).items()}, ensure_ascii=False)


def decode_feature(value):
    data = json.loads(value)
    for key in ("entities", "actions", "objects", "tokens", "quarter", "models", "quake_time", "products", "dates", "issues"):
        data[key] = set(data.get(key) or [])
    data["views"] = tuple(data.get("views") or ())
    return Features(**data)


def block_keys(value):
    # Do not index ubiquitous bigrams/country names as candidate subjects.
    weak = {"中国", "美国", "日本", "俄罗斯", "人民币", "黄金", "白银", "原油", "ai"}
    keys = {"entity:" + entity for entity in value.entities - weak}
    if value.fact:
        keys.add('entity:fact:' + event_key(value.fact))
    if value.subject:
        keys.add("entity:" + value.subject)
    # Long concrete fragments recall unlisted universities/venues/products too.
    for text in value.views or (value.text,):
        keys.update("gram:" + token for token in grams(text, 4))
    return sorted(keys)[:240]


@dataclass
class Statement:
    kind: str
    title: str
    claim: str
    numbers: tuple[str, ...] = ()


def statement_identity(value):
    return json.loads(value.claim[5:]) if value.claim.startswith('fact:') else fact_identity(value.title)


def enriched_claim(old,new):
    a,b=statement_identity(old),statement_identity(new)
    if a and b and a['family']==b['family']=='policy' and same_fact(a,b):
        enriched={**a}
        for key in ('reference','effective_date'):
            enriched[key]=a.get(key) or b.get(key,'')
        if b.get('date_explicit'):
            enriched.update(date=b['date'],date_explicit=True)
        return 'fact:'+json.dumps(enriched,ensure_ascii=False,sort_keys=True)
    if a and b and a['family']==b['family'] and a['family'] in {'speech','press-release'} and same_fact(a,b):
        return 'fact:'+json.dumps({**a,'views':list(dict.fromkeys(a['views']+b['views']))[:8]},ensure_ascii=False,sort_keys=True)
    return old.claim


def amount_tokens(text):
    return quantity_tokens(text)


def statements(item):
    """Extract headline-backed facts, not every historical fact in the body.

    An article explicitly containing a disclosure and a response can support
    both nodes. A generic reference to an older report is not a new progress.
    """
    head = clean_headline(item)
    head = re.sub(r"^(?:财联社|金十数据)\d{1,2}月\d{1,2}日电[，,:：]?\s*", "", head)
    text = re.sub(r"#([^#]+)#", r"\1", head)
    body = str(item.summary or "")[:700]
    combined = text + "\n" + body
    def dated(identity):
        at=getattr(item,'published_dt',None) or getattr(item,'first_dt',None)
        if at:
            identity=dated_policy(identity,china_time(at).strftime('%Y-%m-%d'))
        if identity and identity['family'] in {'market','speech','usage-response','press-release'} and at:
            return {**identity,'publication_day':china_time(at).strftime('%Y-%m-%d')}
        return identity
    parts = market_parts(text)
    if all(market_fact(part) for part in parts):
        return [Statement('report',part,'fact:'+json.dumps(dated(market_fact(part)),ensure_ascii=False,sort_keys=True),amount_tokens(part)) for part in parts[:4]]
    identity = dated(fact_identity(text,body))
    if identity and identity['family']=='policy':
        return [Statement('report',text,'fact:'+json.dumps(identity,ensure_ascii=False,sort_keys=True),amount_tokens(text))]
    results = []
    quality = bool(re.search(r"铅(?:含量)?超标|检出|质量问题|不合格", text))
    response = bool(re.search(r"回应|辟谣|否认|客服.{0,6}(?:称|表示|答复)|官方客服", text)) or bool(identity and identity['family'] in {'press-release','usage-response'})
    if quality:
        disclosure = re.split(r"[，,。；;]|\s+(?=(?:官方|客服|平台|公司))", text)[0]
        # "客服回应产品超标" asserts a response, not another detection.
        if re.search(r"检出|超标|不合格", disclosure) and not re.match(r"^(?:客服|官方|公司).{0,4}回应", disclosure):
            disclosure = re.sub(r"^(?:官方|客服).*?回应", "", disclosure)
            results.append(Statement("report", disclosure, "quality-disclosure"))
    if response:
        claim = ""
        title = text
        for pattern, key in (
            (r"(?:未|没有|并未).{0,4}进入中国|未在中国.{0,3}销售", "not-sold-in-china"),
            (r"(?:已|已经).{0,3}在中国销售|承认.{0,8}(?:销售|问题|超标)", "confirmed-problem"),
            (r"永久封禁|账号.{0,4}封禁|已封禁", "account-banned"),
            (r"系谣言|不实|造谣|辟谣|纯属谣言", "denial"),
            (r"正在核查|正在调查|核实中", "checking"),
        ):
            match = re.search(pattern, combined)
            if match:
                claim = key
                if key == "not-sold-in-china":
                    title = "客服回应：涉事批次未进入中国销售"
                break
        if not claim:
            match = re.search(r"(?:回应|称|表示|答复)[：:，,]?(.{4,100})", text)
            claim = normalize(match[1]) if match else "unspecified"
            # A title repeating only the incident contains no new response detail.
            if re.search(r"检出|超标|相关问题", claim) and not re.search(r"否认|未|已|将|停止|承认|核查", claim):
                claim = "unspecified"
        if identity and identity['family'] in {'speech','usage-response','press-release'}:
            claim='fact:'+json.dumps(identity,ensure_ascii=False,sort_keys=True)
        results.append(Statement("response", title, claim))
    for kind, pattern in (
        ("withdrawal", r"下架"), ("recall", r"召回"),
        ("investigation", r"立案|启动调查|接受调查|被查"),
        ("penalty", r"处罚|被罚|罚款|罚没"), ("judgment", r"判决|获刑|裁决"),
    ):
        if re.search(pattern, text):
            claim = re.sub(r"客服回应.*$", "", text).strip("，, ")
            results.append(Statement(kind, claim or text, normalize(claim or text)))
    if not results:
        kind = "report"
        if identity and identity['family']=='capacity-approval':
            kind='approval'
        elif "回购" in text:
            kind = "buyback-completed" if re.search(r"已回购|完成回购|累计回购", text) else (
                "buyback-approved" if re.search(r"审议通过|获批|批准", text) else "buyback-proposal")
        elif re.search(r"开幕|启幕|首秀", text):
            kind = "opening"
        elif re.search(r"取消|退出|终止", text):
            kind = "agreement-terminated"
        elif re.search(r"获准|获批|批准", text):
            kind = "approval"
        elif re.search(r"评论|观察[｜|：:]|解读|怎么看", text):
            kind = "analysis"
        numbers = amount_tokens(text)
        scope = financial_scope(text, body)
        claim = 'financial:' + json.dumps(scope, sort_keys=True, ensure_ascii=False) if scope else normalize(text)
        if identity:
            claim='fact:'+json.dumps(identity,ensure_ascii=False,sort_keys=True)
        results.append(Statement(kind, text, claim, numbers))
    return results[:4]


def same_progress(left: Statement, right: Statement):
    if left.kind != right.kind:
        return False
    if policy_conflict(left.title,right.title):
        return False
    typed = same_fact(statement_identity(left),statement_identity(right))
    if typed is not None:
        return typed
    # Re-extract old persisted titles too: v1 tokens lost leading minus signs.
    # Reinterpreting the same original fact must not create a new identity.
    left_numbers = amount_tokens(left.title) or amount_tokens(' '.join(left.numbers))
    right_numbers = amount_tokens(right.title) or amount_tokens(' '.join(right.numbers))
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return False
    def scope(value):
        return json.loads(value.claim.removeprefix('financial:')) if value.claim.startswith('financial:') else financial_scope(value.title)
    a_scope, b_scope = scope(left), scope(right)
    if scopes_conflict(a_scope, b_scope):
        return False
    if a_scope and b_scope and left_numbers and left_numbers == right_numbers:
        return True
    if left.kind == "response":
        if "unspecified" in {left.claim, right.claim}:
            return True  # Missing detail is supporting coverage, never novelty.
        if left.claim == right.claim:
            return True
        fixed = {"not-sold-in-china", "confirmed-problem", "account-banned", "denial", "checking"}
        if left.claim in fixed or right.claim in fixed:
            return False
        return SequenceMatcher(None, left.claim, right.claim, autojunk=False).ratio() >= .78
    if left.claim == "quality-disclosure" or right.claim == "quality-disclosure":
        return left.claim == right.claim
    if left.kind in {"opening", "buyback-proposal", "buyback-approved", "buyback-completed", "agreement-terminated"}:
        return True  # Already constrained to a concrete event, plus number guard.
    a, b = normalize(left.title), normalize(right.title)
    ga, gb = grams(a), grams(b)
    coverage = len(ga & gb) / max(1, min(len(ga), len(gb)))
    return (a == b or SequenceMatcher(None, a, b, autojunk=False).ratio() >= .67
            or (len(ga & gb) >= 7 and coverage >= .68)
            or event_match(features(SimpleNamespace(title=left.title, summary="", url="")),
                           features(SimpleNamespace(title=right.title, summary="", url="")))[0] >= .85)


@dataclass
class Progress:
    key: str
    event_id: str
    title: str
    kind: str
    first_reported_at: datetime
    items: list
    doc_ids: list[str]
    identity: dict | None = None


@dataclass
class EventGroup:
    key: str
    items: list
    progresses: list[Progress]


class EventRegistry:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(self.path) if self.path else ":memory:", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, created REAL NOT NULL, merged_into TEXT REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS docs (
                id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, title TEXT NOT NULL,
                url TEXT NOT NULL, publisher TEXT NOT NULL, published REAL NOT NULL,
                feature TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_docs (
                event_id TEXT NOT NULL REFERENCES events(id), doc_id TEXT NOT NULL REFERENCES docs(id),
                PRIMARY KEY(event_id,doc_id)
            );
            CREATE INDEX IF NOT EXISTS event_docs_doc ON event_docs(doc_id);
            CREATE TABLE IF NOT EXISTS progresses (
                id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
                kind TEXT NOT NULL, title TEXT NOT NULL, claim TEXT NOT NULL,
                numbers TEXT NOT NULL, first_reported REAL NOT NULL,
                merged_into TEXT REFERENCES progresses(id)
            );
            CREATE INDEX IF NOT EXISTS progresses_event ON progresses(event_id);
            CREATE TABLE IF NOT EXISTS progress_docs (
                progress_id TEXT NOT NULL REFERENCES progresses(id), doc_id TEXT NOT NULL REFERENCES docs(id),
                PRIMARY KEY(progress_id,doc_id)
            );
            CREATE INDEX IF NOT EXISTS progress_docs_doc ON progress_docs(doc_id);
            CREATE TABLE IF NOT EXISTS blocks (
                key TEXT NOT NULL, doc_id TEXT NOT NULL REFERENCES docs(id),
                PRIMARY KEY(key,doc_id)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                doc_id TEXT NOT NULL, event_id TEXT NOT NULL, reason TEXT NOT NULL,
                score REAL NOT NULL, PRIMARY KEY(doc_id,event_id)
            );
            CREATE TABLE IF NOT EXISTS registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if self.path:
            self.path.chmod(0o600)
        self.cache = {}
        self.stats = Counter()

    def close(self):
        self.db.close()

    def aliases(self):
        return {table: {row[0]: self.resolve(table, row[0]) for row in self.db.execute(
            f"SELECT id FROM {table} WHERE merged_into IS NOT NULL")} for table in ("events", "progresses")}

    def compact_feature_cache(self):
        """Monthly eviction of our derived old fingerprints, not source content/IDs."""
        now = datetime.now().astimezone()
        month = now.strftime("%Y-%m")
        previous = self.db.execute("SELECT value FROM registry_meta WHERE key='feature-cache-month'").fetchone()
        if previous and previous[0] == month:
            return
        cutoff = now.timestamp() - 45 * 86400
        self.db.execute("DELETE FROM blocks WHERE doc_id IN (SELECT id FROM docs WHERE published<?)", (cutoff,))
        self.db.execute("UPDATE docs SET feature='',title='' WHERE published<?", (cutoff,))
        self.db.execute("INSERT OR REPLACE INTO registry_meta VALUES('feature-cache-month',?)", (month,))

    def resolve(self, table, key):
        while True:
            row = self.db.execute(f"SELECT merged_into FROM {table} WHERE id=?", (key,)).fetchone()
            if not row or not row[0]:
                return key
            key = row[0]

    def feature(self, row):
        if row["id"] not in self.cache:
            self.cache[row["id"]] = decode_feature(row["feature"])
        return self.cache[row["id"]]

    def candidate_rows(self, value, published):
        keys = block_keys(value)
        if not keys:
            return []
        # Select rarest concrete fragments first. The SQL is indexed and returns
        # at most 40 documents; no Python all-history or all-pairs scan.
        marks = ",".join("?" for _ in keys)
        counts = self.db.execute(f"SELECT key,COUNT(*) AS n FROM blocks WHERE key IN ({marks}) GROUP BY key", keys).fetchall()
        selected = [row["key"] for row in sorted(counts, key=lambda row: (not row["key"].startswith("entity:"), row["n"], row["key"]))[:24]]
        if not selected:
            return []
        marks = ",".join("?" for _ in selected)
        self.stats["candidate_queries"] += 1
        return self.db.execute(f"""SELECT d.*,COUNT(*) AS hits FROM blocks b JOIN docs d ON d.id=b.doc_id
             WHERE b.key IN ({marks}) AND d.published BETWEEN ? AND ?
             GROUP BY d.id ORDER BY hits DESC,d.published DESC,d.id LIMIT ?""",
                               (*selected, published - 45 * 86400, published + 45 * 86400, MAX_CANDIDATES)).fetchall()

    def merge_events(self, keys):
        rows = self.db.execute("SELECT id,created FROM events WHERE id IN (" + ",".join("?" for _ in keys) + ")", keys).fetchall()
        canonical = min(rows, key=lambda row: (row["created"], row["id"]))["id"]
        for key in keys:
            if key == canonical:
                continue
            self.db.execute("UPDATE events SET merged_into=? WHERE id=?", (canonical, key))
            self.db.execute("UPDATE progresses SET event_id=? WHERE event_id=?", (canonical, key))
            self.db.execute("INSERT OR IGNORE INTO event_docs SELECT ?,doc_id FROM event_docs WHERE event_id=?", (canonical, key))
            self.db.execute("DELETE FROM event_docs WHERE event_id=?", (key,))
            self.stats["event_aliases_created"] += 1
        self.reconcile_progresses(canonical)
        return canonical

    def reconcile_progresses(self, event_id):
        rows = self.db.execute("SELECT * FROM progresses WHERE event_id=? AND merged_into IS NULL ORDER BY first_reported,rowid", (event_id,)).fetchall()
        representatives = []
        for row in rows:
            current = Statement(row["kind"], row["title"], row["claim"], tuple(json.loads(row["numbers"])))
            match = next((other for other in representatives if same_progress(current, Statement(other["kind"], other["title"], other["claim"], tuple(json.loads(other["numbers"]))))), None)
            if match:
                merged=enriched_claim(Statement(match['kind'],match['title'],match['claim']),current)
                self.db.execute('UPDATE progresses SET claim=? WHERE id=?',(merged,match['id']))
                match['claim']=merged
                self.db.execute("UPDATE progresses SET merged_into=? WHERE id=?", (match["id"], row["id"]))
                self.db.execute("INSERT OR IGNORE INTO progress_docs SELECT ?,doc_id FROM progress_docs WHERE progress_id=?", (match["id"], row["id"]))
                self.db.execute("UPDATE progresses SET first_reported=MIN(first_reported,?) WHERE id=?", (row['first_reported'], match['id']))
                self.stats['progress_aliases_created'] += 1
                if match["claim"] == "unspecified" and row["claim"] != "unspecified":
                    self.db.execute("UPDATE progresses SET title=?,claim=? WHERE id=?", (row["title"], row["claim"], match["id"]))
                    match.update(title=row["title"], claim=row["claim"])
            else:
                representatives.append(dict(row))

    def refresh_derived_statements(self,item):
        """Reinterpret unchanged facts in place, never reset IDs or source times.

        An updated article may support old and new nodes. Only the matching
        original headline is reinterpreted; unrelated historical nodes are not.
        """
        head=clean_headline(item)
        fresh=statements(item)
        rows=self.db.execute('''SELECT p.* FROM progresses p JOIN progress_docs pd ON pd.progress_id=p.id
            WHERE pd.doc_id=? AND p.merged_into IS NULL''',(document_id(item),)).fetchall()
        for row in rows:
            # Old broad-policy grouping sometimes attached a different object
            # (e.g. dredgers) to a drone node. Remove only this proven-wrong link;
            # assignment below retains the document and gives its own node.
            if any(statement_identity(s) and statement_identity(s)['family']=='policy' for s in fresh) and policy_conflict(row['title'],head):
                self.db.execute('DELETE FROM progress_docs WHERE progress_id=? AND doc_id=?',(row['id'],document_id(item)))
                self.stats['policy_document_links_repaired']+=1
                continue
            if normalize(row['title'])!=normalize(head) and row['title'] not in {'研选•行业数据','研选·行业数据'}:
                continue
            generic=row['title'] in {'研选•行业数据','研选·行业数据'}
            promote = generic or any(statement_identity(s) and statement_identity(s)['family'] in {'press-release','usage-response','capacity-approval','policy'} for s in fresh)
            candidates=fresh if promote else [s for s in fresh if s.kind==row['kind']]
            if candidates:
                new=candidates[0]
                old=Statement(row['kind'],row['title'],row['claim'])
                old_fact,new_fact=statement_identity(old),statement_identity(new)
                if row['claim'].startswith('fact:') and old_fact and not new_fact:
                    continue  # A truncated excerpt is not evidence retracting a proven identity.
                if row['claim'].startswith('fact:') and old_fact and new_fact and old_fact['family']==new_fact['family']=='policy' and not same_fact(old_fact,new_fact):
                    continue  # A genuine revision of an existing article is new progress.
                self.db.execute('UPDATE progresses SET kind=?,title=?,claim=?,numbers=? WHERE id=?',
                    (new.kind,new.title,new.claim,json.dumps(new.numbers),row['id']))

    def repair_policy_boundaries(self,event_id):
        """Undo a legacy broad-policy merge without recycling any progress ID."""
        rows=self.db.execute('SELECT * FROM progresses WHERE event_id=? AND merged_into IS NULL ORDER BY first_reported,rowid',(event_id,)).fetchall()
        groups=[]
        for row in rows:
            objects=policy_objects(row['title'])
            objects={'无人机' if o=='无人驾驶航空器' else o for o in objects}
            if not objects:
                continue
            group=next((g for g in groups if g[0]&objects),None)
            if group:
                group[0].update(objects);group[1].append(row)
            else:
                groups.append([set(objects),[row]])
        if len(groups)<2:
            return
        for _,members in groups[1:]:
            new=new_id('event')
            self.db.execute('INSERT INTO events(id,created) VALUES(?,?)',(new,time.time()))
            for row in members:
                self.db.execute('UPDATE progresses SET event_id=? WHERE id=?',(new,row['id']))
                self.db.execute('INSERT OR IGNORE INTO event_docs SELECT ?,doc_id FROM progress_docs WHERE progress_id=?',(new,row['id']))
            self.stats['policy_event_splits']+=1
        # Keep a shared document on both events if it explicitly supports both.
        self.db.execute('''DELETE FROM event_docs WHERE event_id=? AND doc_id NOT IN
            (SELECT pd.doc_id FROM progress_docs pd JOIN progresses p ON p.id=pd.progress_id WHERE p.event_id=?)''',(event_id,event_id))

    def supporting_statement(self, item, value, statement, event_id):
        # A publisher may use a poetic website title and quote that exact title
        # inside its own factual Weibo headline. This is supporting coverage,
        # not a new, content-free progress node.
        if statement.kind != "report" or value.actions:
            return statement
        rows = self.db.execute("""SELECT p.*,d.feature FROM progresses p
            JOIN progress_docs pd ON pd.progress_id=p.id JOIN docs d ON d.id=pd.doc_id
            WHERE p.event_id=? AND p.merged_into IS NULL AND p.kind<>'report' AND d.publisher=? AND d.feature<>''""",
            (event_id, publisher_id(item))).fetchall()
        for row in rows:
            old = decode_feature(row["feature"])
            if normalize(statement.title) in old.views:
                return Statement(row["kind"], row["title"], row["claim"], tuple(json.loads(row["numbers"])))
        return statement

    def promote_caption(self, item, value, statement, event_id):
        # Inverse of supporting_statement: the website teaser may arrive first.
        # Enrich that placeholder without allocating a second progress identity.
        if statement.kind == "report" or not value.actions:
            return
        rows = self.db.execute("""SELECT DISTINCT p.*,d.feature FROM progresses p
            JOIN progress_docs pd ON pd.progress_id=p.id JOIN docs d ON d.id=pd.doc_id
            WHERE p.event_id=? AND p.merged_into IS NULL AND p.kind='report'
            AND d.publisher=? AND d.feature<>''""", (event_id, publisher_id(item))).fetchall()
        for row in rows:
            if normalize(row["title"]) in value.views and not decode_feature(row["feature"]).actions:
                self.db.execute("UPDATE progresses SET kind=?,title=?,claim=?,numbers=? WHERE id=?",
                    (statement.kind, statement.title, statement.claim, json.dumps(statement.numbers), row["id"]))

    def assign(self, items):
        started = time.perf_counter()
        self.stats["input_docs"] = len(items)
        self.stats["all_pairs_baseline"] = len(items) * max(0, len(items) - 1) // 2
        current = {}
        affected_events = set()
        # Early reports establish a node; later coverage must not reset its age.
        ordered = sorted(items, key=lambda item: (china_time(item.published_dt or item.first_dt), document_id(item)))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.compact_feature_cache()
            # Two-phase feature refresh: upgrading a rule version must not
            # compare today's fingerprints with stale fingerprints of another
            # CURRENT document merely because it appears later in source order.
            prepared = {}
            for item in ordered:
                key = document_id(item)
                digest = hashlib.sha256((str(VERSION) + "|" + item.title + "|" + str(item.summary or "")[:900]
                                         + "|" + china_time(item.published_dt or item.first_dt).isoformat()).encode()).hexdigest()
                existing = self.db.execute("SELECT * FROM docs WHERE id=?", (key,)).fetchone()
                owners = self.db.execute("SELECT event_id FROM event_docs WHERE doc_id=?", (key,)).fetchall()
                prepared[key] = digest, existing, owners
                if not (existing and existing["content_hash"] == digest and owners):
                    self.cache[key] = features(item)
                    self.stats["feature_extractions"] += 1
            for item in ordered:
                key = document_id(item)
                item.doc_id = key
                current[key] = item
                digest, existing, owners = prepared[key]
                if existing and existing["content_hash"] == digest and owners:
                    self.stats["unchanged_docs"] += 1
                    continue
                value = self.cache[key]
                if existing and owners:
                    self.refresh_derived_statements(item)
                published = china_time(item.published_dt or item.first_dt).timestamp()
                matches = {}
                representatives = {}
                compared = Counter()
                for candidate in self.candidate_rows(value, published):
                    if candidate["id"] == key:
                        continue
                    for owner in self.db.execute("SELECT event_id FROM event_docs WHERE doc_id=?", (candidate["id"],)):
                        event_id = self.resolve("events", owner[0])
                        if compared[event_id] >= MAX_REPRESENTATIVES:
                            continue
                        compared[event_id] += 1
                        self.stats["event_pair_checks"] += 1
                        score, reason = event_match(value, self.feature(candidate))
                        if score >= .85 and score > matches.get(event_id, (0, ""))[0]:
                            matches[event_id] = (score, reason)
                            representatives[event_id] = self.feature(candidate)
                existing_owner = self.resolve("events", owners[0][0]) if owners else None
                if existing_owner:
                    event_id = existing_owner
                    representatives.setdefault(event_id,value)
                elif matches:
                    # Insertion follows the deterministic indexed document order;
                    # a randomly allocated UUID must never break a score tie.
                    event_id = max(matches, key=lambda candidate: matches[candidate][0])
                else:
                    event_id = new_id("event")
                    self.db.execute("INSERT INTO events(id,created) VALUES(?,?)", (event_id, time.time()))
                    self.stats["events_created"] += 1
                # A bridging document can repair a previous split only when the
                # candidate representatives also agree. Never union a topic chain.
                mergeable = [event_id]
                if event_id in representatives:
                    for other in matches:
                        if other != event_id and event_match(representatives[event_id], representatives[other])[0] >= .85:
                            mergeable.append(other)
                if len(mergeable) > 1:
                    event_id = self.merge_events(mergeable)
                affected_events.add(event_id)
                published = china_time(item.published_dt or item.first_dt).timestamp()
                self.db.execute("""INSERT INTO docs VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    content_hash=excluded.content_hash,title=excluded.title,url=excluded.url,published=excluded.published,feature=excluded.feature,updated=excluded.updated""",
                    (key, digest, item.title, article_url(item.url) or item.url, publisher_id(item), published, encode_feature(value), time.time()))
                self.cache[key] = value
                self.db.execute("INSERT OR IGNORE INTO event_docs VALUES(?,?)", (event_id, key))
                self.db.execute("DELETE FROM blocks WHERE doc_id=?", (key,))
                self.db.executemany("INSERT OR IGNORE INTO blocks VALUES(?,?)", [(block, key) for block in block_keys(value)])
                score, reason = matches.get(event_id, (1, "same-document" if existing_owner else "new-event"))
                self.db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?,?)", (key, event_id, reason, score))
                for statement in statements(item):
                    statement = self.supporting_statement(item, value, statement, event_id)
                    self.promote_caption(item, value, statement, event_id)
                    rows = self.db.execute("SELECT * FROM progresses WHERE event_id=? AND merged_into IS NULL ORDER BY first_reported,rowid", (event_id,)).fetchall()
                    match = None
                    for row in rows:
                        self.stats["progress_pair_checks"] += 1
                        old = Statement(row["kind"], row["title"], row["claim"], tuple(json.loads(row["numbers"])))
                        if same_progress(statement, old):
                            match = row
                            break
                    if match:
                        progress_id = match["id"]
                        self.db.execute('UPDATE progresses SET claim=? WHERE id=?',
                            (enriched_claim(Statement(match['kind'],match['title'],match['claim']),statement),progress_id))
                        self.db.execute("UPDATE progresses SET first_reported=MIN(first_reported,?) WHERE id=?", (published, progress_id))
                        if match["claim"] == "unspecified" and statement.claim != "unspecified":
                            self.db.execute("UPDATE progresses SET title=?,claim=? WHERE id=?", (statement.title, statement.claim, progress_id))
                        elif statement.claim.startswith('financial:') and not match['claim'].startswith('financial:'):
                            self.db.execute("UPDATE progresses SET claim=?,numbers=? WHERE id=?", (statement.claim, json.dumps(statement.numbers), progress_id))
                    else:
                        progress_id = new_id("progress")
                        self.db.execute("INSERT INTO progresses VALUES(?,?,?,?,?,?,?,NULL)",
                                        (progress_id, event_id, statement.kind, statement.title, statement.claim, json.dumps(statement.numbers), published))
                        self.stats["progresses_created"] += 1
                    self.db.execute("INSERT OR IGNORE INTO progress_docs VALUES(?,?)", (progress_id, key))
                self.stats["updated_docs" if existing else "new_docs"] += 1
            # Upgrade existing duplicate nodes too, not just newly arriving docs.
            # The older stable ID survives and the removed identity becomes an
            # alias, so previous Feishu ACKs continue to suppress repeats.
            for event_id in {self.resolve('events', key) for key in affected_events}:
                self.reconcile_progresses(event_id)
                self.repair_policy_boundaries(event_id)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        groups = defaultdict(dict)
        progresses = defaultdict(list)
        if current:
            keys = list(current)
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                for row in self.db.execute(f"SELECT event_id,doc_id FROM event_docs WHERE doc_id IN ({marks})", batch):
                    groups[self.resolve("events", row["event_id"])][row["doc_id"]] = current[row["doc_id"]]
            for event_id in groups:
                rows = self.db.execute("SELECT * FROM progresses WHERE event_id=? AND merged_into IS NULL ORDER BY first_reported,rowid", (event_id,)).fetchall()
                for row in rows:
                    doc_ids = [r[0] for r in self.db.execute("SELECT doc_id FROM progress_docs WHERE progress_id=? ORDER BY doc_id", (row["id"],)) if r[0] in current]
                    if doc_ids:
                        progresses[event_id].append(Progress(row["id"], event_id, row["title"], row["kind"],
                            datetime.fromtimestamp(row["first_reported"], china_time(ordered[0].published_dt or ordered[0].first_dt).tzinfo),
                            [current[key] for key in doc_ids], doc_ids,
                            statement_identity(Statement(row['kind'],row['title'],row['claim']))))
        self.stats["events"] = len(groups)
        self.stats["progresses"] = sum(map(len, progresses.values()))
        self.stats["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        self.stats["database_bytes"] = self.path.stat().st_size if self.path else 0
        return [EventGroup(key, list(values.values()), progresses[key]) for key, values in groups.items()]
