from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import html
import json
import math
from pathlib import Path
import re
import tempfile
import unittest

import curated_events as c
import briefing_shared as shared
from news_ranking import POLICY, decay_factor, rank_progress, rank_snapshot_progress, card_rank
from news_freshness import CHINA
from test_curated_home import sample_item

NOW = datetime(2026, 10, 19, 14, 43, tzinfo=CHINA)


def item(title, uid, age=1, weight=16):
    at=(NOW-timedelta(hours=age)).replace(tzinfo=None)
    return replace(sample_item(title), source_id='publisher:'+uid, source_name=uid,
        source_weight=weight, doc_id='doc:'+uid, url='https://example.com/'+uid,
        first_dt=at,last_dt=at,published_dt=at,collected_dt=at,date=at.strftime('%Y-%m-%d'))


def event_with_nodes():
    old=[item('英伟达发布新一代人工智能算力芯片','a'+str(i),age=23,weight=25) for i in range(4)]
    new=[item('英伟达客服回应相关消息','b',age=1,weight=2)]
    nodes=[c.HomeProgress('progress:old','event:one',old[0].title,'launch',NOW-timedelta(hours=23),old,[v.doc_id for v in old]),
           c.HomeProgress('progress:new','event:one',new[0].title,'response',NOW-timedelta(hours=1),new,[v.doc_id for v in new])]
    return c.Event(key='event:one',title=old[0].title,items=old+new,progresses=nodes)


class DecayTests(unittest.TestCase):
    def test_exact_policy_and_expected_coefficients(self):
        self.assertEqual(POLICY['version'],'progress-decay-v1')
        self.assertEqual(decay_factor(0),1)
        self.assertEqual(decay_factor(1),1)
        for age,expected in [(3,.896850263),(6,.780615512),(12,.640307756),(18,.570153878),(24,.535076939)]:
            self.assertAlmostEqual(decay_factor(age),expected,places=8)

    def test_continuous_monotone_and_floor_even_for_monday_weekend(self):
        scores=[decay_factor(age/100) for age in range(0,7201)]
        self.assertEqual(scores,sorted(scores,reverse=True))
        self.assertTrue(all(.5 <= value <= 1 for value in scores))
        self.assertLess(abs(decay_factor(6-.0001)-decay_factor(6+.0001)),.0001)
        self.assertGreaterEqual(decay_factor(55),.5)

    def test_more_recent_good_news_beats_old_high_score_but_importance_still_matters(self):
        old=rank_progress(106,NOW-timedelta(hours=23),NOW)
        fresh=rank_progress(85,NOW-timedelta(hours=1),NOW)
        self.assertGreater(fresh['score'],old['score'])
        self.assertGreater(old['score'],rank_progress(10,NOW,NOW)['score'])

    def test_no_compounding_from_snapshot_or_repeated_calls(self):
        progress={'first_reported_at':(NOW-timedelta(hours=12)).isoformat(),'importance_score':100,'score':1}
        event={'score':1,'importance_score':2}
        first=rank_snapshot_progress(event,progress,NOW)
        second=rank_snapshot_progress(event,{**progress,**first},NOW)
        self.assertEqual(first,second)
        self.assertAlmostEqual(first['score'],64.0307756,places=6)

    def test_naive_beijing_and_utc_have_the_same_age(self):
        at=NOW-timedelta(hours=6)
        self.assertEqual(rank_progress(100,at,NOW),rank_progress(100,at.astimezone(__import__('datetime').timezone.utc),NOW))


class ProgressRankingTests(unittest.TestCase):
    def test_each_progress_uses_its_own_importance_not_hot_old_event(self):
        event=event_with_nodes();nodes=c.event_progress_ranking(event,NOW)
        old,new=nodes
        self.assertGreater(old['importance_score'],new['importance_score'])
        self.assertEqual(new['score'],new['importance_score'])
        self.assertEqual(card_rank(nodes),max(node['score'] for node in nodes))
        self.assertLess(card_rank(nodes),old['importance_score'])

    def test_recrawl_and_capture_crossing_midnight_cannot_increase_score(self):
        event=event_with_nodes();before=c.event_progress_ranking(event,NOW)
        for value in event.items:
            value.count=9999;value.date='2026-10-20'
            value.last_dt=value.collected_dt=NOW.replace(tzinfo=None)+timedelta(days=1)
            value.score=9999
        self.assertEqual(c.event_progress_ranking(event,NOW),before)

    def test_same_publisher_repeat_does_not_stack_but_independent_source_can_help(self):
        value=item('英伟达发布新一代芯片','one')
        node=c.HomeProgress('p','e',value.title,'launch',NOW-timedelta(hours=1),[value],[value.doc_id])
        event=c.Event(key='e',items=[value],progresses=[node])
        first=c.event_progress_ranking(event,NOW)[0]['importance_score']
        repeated=replace(value,doc_id='doc:repeat',url='https://example.com/repeat',count=99)
        node.items.append(repeated);event.items.append(repeated)
        self.assertEqual(c.event_progress_ranking(event,NOW)[0]['importance_score'],first)
        independent=replace(value,source_id='publisher:two',source_name='two',doc_id='doc:two')
        node.items.append(independent);event.items.append(independent)
        self.assertGreater(c.event_progress_ranking(event,NOW)[0]['importance_score'],first)

    def test_snapshot_and_html_export_same_scores_and_stable_three_level_ids(self):
        event=event_with_nodes()
        snapshot=c.home_event_snapshot([event],len(event.items),{}, {},{},now=NOW)
        record=snapshot['events'][0]
        self.assertEqual(snapshot['ranking_policy'],POLICY)
        self.assertEqual(record['score'],max(node['score'] for node in record['progresses']))
        self.assertEqual({node['id'] for node in record['progresses']},{'progress:old','progress:new'})
        view=c.current_event_view(event,NOW-timedelta(hours=24),now=NOW)
        markup=c.home_event_row(view,1,ranking_now=NOW)
        exported=json.loads(html.unescape(re.search(r'data-ranking-progresses="([^"]+)"',markup)[1]))
        self.assertEqual({node['id']:node['score'] for node in exported},{node['id']:node['score'] for node in record['progresses']})
        shared.validate_snapshot(snapshot,NOW)

    def test_feishu_ranks_progress_independently_and_keeps_its_window(self):
        event=event_with_nodes()
        snapshot=c.home_event_snapshot([event],len(event.items),{}, {},{},now=NOW)
        selected,_,_=shared.select_snapshot(snapshot,{},NOW,'0700')
        chosen=[item for values in selected.values() for item in values]
        self.assertEqual(len(chosen),2)  # Monday's weekend window, not just 24/12h.
        for value in chosen:
            node=next(p for p in snapshot['events'][0]['progresses'] if p['id']==value['progress_id'])
            self.assertEqual(value['score'],rank_snapshot_progress(snapshot['events'][0],node,NOW)['score'])

    def test_legacy_daily_importance_and_events_are_not_mutated(self):
        event=event_with_nodes();event.score=123.4
        before=deepcopy(event)
        c.event_progress_ranking(event,NOW)
        self.assertEqual(event,before)
        c.home_event_snapshot([event],len(event.items),{}, {},{},now=NOW)
        self.assertEqual(event,before)


if __name__=='__main__':unittest.main()
