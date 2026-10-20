"""Bootstrap a fresh cloud identity registry from already-public event snapshots.

Never uploads the local SQLite registry, features, decisions or delivery records.
Does nothing when a cached cloud registry already contains identities.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

from event_registry import EventRegistry, Statement, statements, features, encode_feature, block_keys
from news_event_clustering import normalize
from news_freshness import parse_publication_time

def seed(output, path):
    registry=EventRegistry(path)
    try:
        if registry.db.execute('SELECT count(*) FROM events').fetchone()[0]:
            return {'seeded':False,'reason':'existing-registry'}
        paths=[output/'source-pool/home-events.json',*sorted((output/'daily').glob('????-??-??.json'),reverse=True)]
        aliases={};payloads=[]
        for source in paths:
            if not source.exists():continue
            payload=json.loads(source.read_text())
            if payload.get('pipeline') not in {'shared-home-v1','site-daily-v2'}: continue
            payloads.append(payload);aliases.update(payload.get('id_aliases',{}).get('events',{}))
        def canonical(key):
            seen=set()
            while key in aliases:
                if key in seen: raise ValueError('Public identity alias cycle')
                seen.add(key);key=aliases[key]
            return key
        for payload in payloads:
            for event in payload.get('events',[]):
                event_id=canonical(event['id'])
                if not re.fullmatch(r'event:[0-9a-f]{32}',event_id):raise ValueError('Invalid public event identity')
                registry.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,NULL)',(event_id,time.time()))
                docs={}
                for doc in event['reports']:
                    key=doc['doc_id'];published=parse_publication_time(doc['published_at'])
                    if not re.fullmatch(r'doc:[0-9a-f]{24}',key) or not published:raise ValueError('Invalid public document')
                    owner=registry.db.execute('SELECT event_id FROM event_docs WHERE doc_id=?',(key,)).fetchone()
                    if owner and owner[0]!=event_id:continue
                    source=doc.get('source_id') or doc.get('publisher_id') or 'public-archive'
                    publisher=doc.get('publisher_id') or source
                    value=features(SimpleNamespace(title=doc['title'],summary='',url=doc['url']))
                    registry.db.execute('INSERT OR IGNORE INTO docs VALUES(?,?,?,?,?,?,?,?)',
                        (key,'public-seed',doc['title'],doc['url'],publisher,published.timestamp(),encode_feature(value),time.time()))
                    registry.db.execute('INSERT OR IGNORE INTO event_docs VALUES(?,?)',(event_id,key))
                    registry.db.executemany('INSERT OR IGNORE INTO blocks VALUES(?,?)',[(block,key) for block in block_keys(value)])
                    docs[key]=doc
                for progress in event['progresses']:
                    key=progress['id'];first=parse_publication_time(progress['first_reported_at'])
                    if not re.fullmatch(r'progress:[0-9a-f]{32}',key) or not first:raise ValueError('Invalid public progress')
                    matches=statements(SimpleNamespace(title=progress['title'],summary='',url=''))
                    statement=next((value for value in matches if value.kind==progress['type']),
                                   Statement(progress['type'],progress['title'],normalize(progress['title'])))
                    if isinstance(progress.get('fact_identity'),dict) and progress['fact_identity'].get('family'):
                        # This evidence is already public and hash-checked. A cold
                        # cloud checkout must not downgrade it to a title-only guess.
                        statement=Statement(progress['type'],progress['title'],
                            'fact:'+json.dumps(progress['fact_identity'],ensure_ascii=False,sort_keys=True),statement.numbers)
                    registry.db.execute('INSERT OR IGNORE INTO progresses VALUES(?,?,?,?,?,?,?,NULL)',
                        (key,event_id,progress['type'],progress['title'],statement.claim,json.dumps(statement.numbers),first.timestamp()))
                    registry.db.executemany('INSERT OR IGNORE INTO progress_docs VALUES(?,?)',
                        [(key,doc_id) for doc_id in progress['doc_ids'] if doc_id in docs])
        registry.db.commit()
        return {'seeded':True,'events':registry.db.execute('SELECT count(*) FROM events').fetchone()[0],
                'docs':registry.db.execute('SELECT count(*) FROM docs').fetchone()[0]}
    finally:registry.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-root',type=Path,default=Path('output'))
    parser.add_argument('--event-db',type=Path,default=Path('.local-refresh/events-output.sqlite3'))
    args=parser.parse_args();print(json.dumps(seed(args.output_root,args.event_db)))
