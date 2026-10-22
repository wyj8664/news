"""Read-only real-data replay; mutate only a temporary registry, never send."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile

import curated_events as c
from event_registry import document_id
from news_freshness import write_json
from briefing_shared import canonical_progress, sent_progress_ids

ROOT=Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    output=root/'output'
    snapshot=json.loads((output/'source-pool/home-events.json').read_text())
    before=[event for event in snapshot['events'] if any('牡蛎' in doc['title'] for doc in event['reports'])]
    ids={doc['doc_id'] for event in before for doc in event['reports']}
    items=[*c.load_weibo_items(output),*c.load_native_pool_items(output)[0]]
    items=[item for item in items if document_id(item) in ids]
    if not ids or {document_id(item) for item in items}!=ids:
        raise ValueError('Replay does not cover the original source documents')
    with tempfile.TemporaryDirectory(prefix='consumer-safety-replay-') as temp:
        registry=Path(temp)/'events.sqlite3'
        with sqlite3.connect((root/'.local-refresh/events-output.sqlite3').as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(registry) as target:
            source.backup(target)
        aliases={};stats={}
        after=c.cluster_home_events(items,registry_path=registry,aliases=aliases,stats=stats)
        repeated_stats={}
        repeated=c.cluster_home_events(list(reversed(items)),registry_path=registry,stats=repeated_stats)
        event_ids={event.key for event in after}
        progress_ids={node.key for event in after for node in event.progresses}
        assert len(after)==1 and after[0].category=='consumer'
        assert {item.doc_id for event in after for item in event.items}==ids
        assert {canonical_progress(event['id'],aliases['events']) for event in before}==event_ids
        assert {canonical_progress(node['id'],aliases['progresses']) for event in before for node in event['progresses']}<=progress_ids
        assert {node.key for event in repeated for node in event.progresses}==progress_ids
        assert repeated_stats.get('progresses_created',0)==0
        history=json.loads((output/'briefing/sent_history.json').read_text())
        previous_sent=sent_progress_ids(history,{})
        resolved_sent=sent_progress_ids(history,aliases['progresses'])
        old_sent={node['id'] for event in before for node in event['progresses']} & previous_sent
        assert {canonical_progress(key,aliases['progresses']) for key in old_sent}<=resolved_sent
        return {'source_generation':snapshot['generated_at'],'events_before':len(before),
            'consumer_cards_before':sum(event['category']=='consumer' for event in before),
            'events_after':len(after),'consumer_cards_after':1,'source_documents_preserved':len(ids),
            'canonical_event':after[0].key,'progresses':[{'id':p.key,'kind':p.kind,'action':(p.identity or {}).get('action'),
                'title':p.title,'first_reported_at':str(p.first_reported_at),'source_count':len(p.doc_ids)} for p in after[0].progresses],
            'event_aliases':{event['id']:canonical_progress(event['id'],aliases['events']) for event in before},
            'old_sent_nodes_resolve_to_existing_ack':len(old_sent),
            'repeat_new_events':repeated_stats.get('events_created',0),'repeat_new_progresses':repeated_stats.get('progresses_created',0)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    report=validate()
    if args.report:
        target=args.report.resolve()
        if (ROOT/'output').resolve() in target.parents:
            raise ValueError('Keep validation receipts outside the public output directory')
        write_json(target,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
