"""Product-safety grouping, real progress boundaries and stable migration."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import curated_events as c
import event_registry as registry
import briefing_shared as shared
from home_categories import classify_text
from news_consumer_incidents import incident_identity
from test_event_registry import doc

NOW=datetime.now(c.CHINA)+timedelta(minutes=10)


def reports():
    summary='10月21日，甲超市收到“甲品牌”牡蛎精胶囊召回信息。该产品被抽检发现污染物，甲超市配合品牌方召回。'
    values=[
        '多名甲超市会员收到召回短信，客服：所有批次产品全部下架',
        '甲超市召回甲品牌牡蛎精',
        '甲品牌牡蛎精被检出污染物后，多名甲超市会员收到召回短信',
        '甲超市紧急下架牡蛎精胶囊',
        '甲超市紧急下架牡蛎精胶囊甲超市回应下架牡蛎精胶囊',
        '甲超市回应牡蛎精检出污染物',
        '甲品牌回应牡蛎精检出污染物：给大家退款处理',
        '牡蛎精涉事公司曾被多次整改',
    ]
    return [replace(doc(title,n+1),summary=summary,published_dt=doc(title,n+1).published_dt+timedelta(minutes=n)) for n,title in enumerate(values)]


class ConsumerIncidentTests(unittest.TestCase):
    def test_retailer_brand_notification_refund_and_background_one_event(self):
        events=c.cluster_home_events(reports())
        self.assertEqual(len(events),1)
        self.assertEqual(events[0].category,'consumer')
        self.assertEqual(len(events[0].items),8)
        self.assertEqual({p.kind for p in events[0].progresses},{'report','recall','withdrawal','response','analysis'})
        self.assertEqual(len(events[0].progresses),5)
        self.assertEqual({p.identity['action'] for p in events[0].progresses},{'disclosure','recall','withdrawal','refund','background'})

    def test_brand_refund_alone_is_consumer_not_society(self):
        item=reports()[6]
        self.assertEqual(classify_text(item.title,item.summary),'consumer')

    def test_customer_service_speaker_does_not_recreate_a_generic_report(self):
        for brand in ('Swisse','清禾'):
            items=[doc(brand+'抗糖丸被检出铅超标',1),
                   doc(brand+'客服回应抗糖丸铅超标：相关批次未进入中国销售',2)]
            events=c.cluster_home_events(items)
            self.assertEqual(len(events),1)
            self.assertEqual(len(events[0].progresses),2)
            self.assertEqual({p.identity['action'] for p in events[0].progresses},{'disclosure','response'})

    def test_identical_truncated_caption_does_not_recreate_a_response(self):
        items=reports()[:5]
        items.append(replace(doc(items[-1].title,99),summary='小调查，你买过这款“甲品牌”牡蛎精胶囊吗？',published_dt=items[-1].published_dt+timedelta(minutes=1)))
        events=c.cluster_home_events(items)
        self.assertEqual(len(events),1)
        self.assertFalse(any(p.kind=='response' for p in events[0].progresses))
        self.assertEqual(sum(p.kind=='withdrawal' for p in events[0].progresses),1)

    def test_different_brands_products_batches_issues_and_episodes_never_merge(self):
        a=replace(doc('清禾牡蛎精被检出铅超标',1),summary='清禾牡蛎精被检出铅超标，启动召回。')
        others=[
            replace(doc('蓝禾牡蛎精被检出铅超标',2),summary='蓝禾牡蛎精被检出铅超标，启动召回。'),
            replace(doc('清禾褪黑素被检出铅超标',3),summary='清禾褪黑素被检出铅超标，启动召回。'),
            replace(doc('清禾牡蛎精被检出汞超标',4),summary='清禾牡蛎精被检出汞超标，启动召回。'),
            replace(a,url='https://www.cls.cn/detail/5',published_dt=a.published_dt+timedelta(days=12)),
        ]
        for b in others:
            with self.subTest(title=b.title):self.assertEqual(len(c.cluster_home_events([a,b])),2)
        a=replace(a,title=a.title+'，批次号A01')
        b=replace(a,url='https://www.cls.cn/detail/6',title=a.title.replace('A01','B02'))
        self.assertEqual(len(c.cluster_home_events([a,b])),2)

    def test_missing_issue_cannot_bridge_known_incompatible_contaminants(self):
        items=[replace(doc(t,n),summary=t) for n,t in enumerate([
            '清禾牡蛎精被检出铅超标',
            '清禾牡蛎精被检出汞超标',
            '清禾牡蛎精被检出污染物后宣布召回',
        ],1)]
        events=c.cluster_home_events(items)
        self.assertGreaterEqual(len(events),2)
        self.assertFalse(any(any('铅超标' in i.title for i in e.items) and any('汞超标' in i.title for i in e.items) for e in events))

    def test_changed_scope_or_quantity_is_new_progress_not_a_duplicate(self):
        for left,right in [('甲品牌召回部分批次牡蛎精','甲品牌召回所有批次牡蛎精'),
                           ('甲品牌召回1000盒牡蛎精','甲品牌召回2000盒牡蛎精')]:
            a=replace(doc(left,1),summary='“甲品牌”牡蛎精检出污染物后进行召回。')
            b=replace(doc(right,2),summary=a.summary)
            events=c.cluster_home_events([a,b])
            self.assertEqual(len(events),1)
            self.assertEqual(sum(p.kind=='recall' for p in events[0].progresses),2)

    def test_plain_company_mentions_and_promotional_hashtags_do_not_link(self):
        for title in ['甲品牌发布新款饮料','小调查：你经常去甲超市吗？','电影《奶粉召回》宣布下架']:
            self.assertIsNone(incident_identity(title,'#甲超市召回甲品牌牡蛎精#','2026-10-22'))

    def test_legacy_splits_are_aliased_and_repeated_builds_do_not_allocate_ids(self):
        items=reports()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'events.sqlite3'
            with patch.object(registry,'VERSION',5),patch.object(registry,'event_match',return_value=(0,'legacy-split')):
                before=c.cluster_home_events(items,registry_path=path)
            old_events={e.key for e in before};old_progresses={p.key for e in before for p in e.progresses}
            aliases={}
            after=c.cluster_home_events(items,registry_path=path,aliases=aliases)
            self.assertEqual(len(after),1)
            canonical=lambda key,table:shared.canonical_progress(key,aliases[table])
            self.assertEqual({canonical(key,'events') for key in old_events},{after[0].key})
            self.assertEqual({canonical(key,'progresses') for key in old_progresses},{p.key for p in after[0].progresses})
            stats={}
            again=c.cluster_home_events(list(reversed(items)),registry_path=path,stats=stats)
            self.assertEqual({p.key for p in after[0].progresses},{p.key for p in again[0].progresses})
            self.assertEqual(stats.get('progresses_created',0),0)
            self.assertEqual(stats.get('event_pair_checks',0),0)

    def test_historical_background_is_not_a_new_feishu_progress(self):
        item=replace(reports()[-1],published_dt=NOW-timedelta(hours=1))
        value=c.home_event_snapshot(c.cluster_home_events([item]),1,{}, {},{},now=NOW)
        selected,reasons,_=shared.select_snapshot(value,{},NOW,'1000')
        self.assertFalse([item for items in selected.values() for item in items])
        self.assertEqual(reasons['consumer_background_not_new_progress'],1)

    def test_new_regulatory_actions_and_changed_fines_are_not_hidden_as_disclosure(self):
        summary='“清禾”牡蛎精被检出污染物。'
        items=[replace(doc(title,n+1),summary=summary) for n,title in enumerate([
            '清禾牡蛎精被检出污染物',
            '清禾牡蛎精被检出污染物后监管部门立案',
            '清禾因牡蛎精检出污染物被罚款100万元',
            '清禾因牡蛎精检出污染物被罚款200万元',
        ])]
        events=c.cluster_home_events(items)
        self.assertEqual(len(events),1)
        self.assertEqual(sum(p.kind=='penalty' for p in events[0].progresses),2)
        self.assertIn('investigation',{p.kind for p in events[0].progresses})

    def test_denial_does_not_assert_a_recall_and_planned_vs_completed_survives(self):
        summary='“清禾”牡蛎精被检出污染物。'
        a=replace(doc('清禾回应牡蛎精检出污染物：否认启动召回',1),summary=summary)
        event=c.cluster_home_events([a])[0]
        self.assertEqual([p.identity['action'] for p in event.progresses],['denial'])
        items=[replace(doc(t,n+2),summary=summary) for n,t in enumerate(['清禾计划召回牡蛎精','清禾已完成召回牡蛎精'])]
        event=c.cluster_home_events(items)[0]
        self.assertEqual(sum(p.kind=='recall' for p in event.progresses),2)

    def test_background_does_not_raise_card_rank_or_latest_factual_time(self):
        events=c.cluster_home_events(reports())
        event=events[0]
        scores=c.event_progress_ranking(event,NOW)
        background=next(p for p in event.progresses if p.kind=='analysis')
        self.assertEqual(next(p['score'] for p in scores if p['id']==background.key),0)
        markup=c.home_event_row(event,1,dynamic=False)
        self.assertIn('最新进展：'+reports()[6].title,markup)


if __name__=='__main__':unittest.main()
