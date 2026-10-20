"""Public-build acceptance: no local sender modules, credentials or transports."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import briefing_shared as shared
import curated_events as curated
import event_registry as registry
from home_categories import classify_text
from news_fact_identity import fact_identity
from news_freshness import CHINA
from seed_event_registry import seed
from test_event_registry import doc, mapping


class DedupReleaseTests(unittest.TestCase):
    def policy(self,n=1):
        return replace(doc('商务部等三部门：未经许可不得出口特定无人驾驶航空器相关物项',n),
            summary='商务部海关总署公告2026年第49号，决定优化调整特定无人驾驶航空器相关物项出口管制措施。本公告自2026年11月18日起正式实施。',
            published_dt=datetime(2026,10,19,15,tzinfo=CHINA))

    def test_truncated_same_headline_never_downgrades_policy_evidence(self):
        full=self.policy()
        short=replace(self.policy(2),summary='出口管制范围涵盖多类物项，包括无人机整机和专用发动机。')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'registry.sqlite3'
            before=curated.cluster_home_events([full,short],registry_path=path)
            key=before[0].progresses[0].key
            with patch.object(registry,'VERSION',registry.VERSION+1):
                after=curated.cluster_home_events([short,full],registry_path=path)
                self.assertEqual(len(after[0].progresses),1)
                node=after[0].progresses[0]
                self.assertEqual(node.key,key)
                self.assertEqual(node.identity['reference'],'2026-49')
                self.assertEqual(node.first_reported_at,full.published_dt)
                self.assertEqual(len(node.doc_ids),2)
                again=curated.cluster_home_events([full,short],registry_path=path)
                self.assertEqual(mapping(after),mapping(again))

    def test_policy_extract_and_official_qa_are_same_but_revision_is_not(self):
        full=self.policy()
        qa=replace(self.policy(2),title='商务部新闻发言人就优化调整无人机相关物项出口管制措施答记者问',
            summary='2026年10月19日，商务部联合发布公告，优化调整无人机相关物项出口管制措施。相关措施将于11月18日起正式实施。')
        events=curated.cluster_home_events([full,qa])
        self.assertEqual((len(events),len(events[0].progresses)),(1,1))
        revision=replace(full,title='商务部修订无人机相关物项出口管制措施，新增限制',url='https://www.cls.cn/detail/3')
        self.assertFalse(registry.same_progress(registry.statements(full)[0],registry.statements(revision)[0]))
        different=replace(self.policy(4),summary=full.summary.replace('49号','50号'))
        self.assertEqual(len(curated.cluster_home_events([full,different])),2)

    def test_cloud_bootstrap_preserves_public_policy_evidence(self):
        original=curated.cluster_home_events([self.policy()])
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'output';path=Path(directory)/'state/events.sqlite3'
            snapshot=curated.home_event_snapshot(original,1,{}, {}, {},now=datetime(2026,10,19,15,55,tzinfo=CHINA))
            curated.write_json(output/'source-pool/home-events.json',snapshot)
            seed(output,path)
            registry_copy=registry.EventRegistry(path)
            try:
                row=registry_copy.db.execute('SELECT kind,title,claim FROM progresses WHERE merged_into IS NULL').fetchone()
                identity=registry.statement_identity(registry.Statement(row['kind'],row['title'],row['claim']))
                self.assertEqual(identity['reference'],'2026-49')
                self.assertEqual(identity['effective_date'],'2026-11-18')
            finally:
                registry_copy.close()

    def test_core_fact_and_industry_rules(self):
        a=doc('甲企业旗下乙公司增资至5.29亿美元 增幅约23%',1)
        b=doc('甲企业旗下乙公司增资至5.29亿美元',2)
        self.assertEqual(len(curated.cluster_home_events([a,b])[0].progresses),1)
        self.assertEqual(classify_text('顾客高喊山姆倒闭视频系AI造假'),'consumer')
        self.assertEqual(classify_text('全球芯片LOF停牌'),'finance')
        self.assertEqual(classify_text('投票：今年金鸡奖提名的电影，你看过几部？'),'other')

    def test_commodity_presentation_does_not_merge_factual_prices(self):
        def item(title,n):
            return dict(title=title,progress_id=f'progress:{n}',event_id=f'event:{n}',track='commodities',
                progress_type='report',first_reported_at=f'2026-10-19T15:0{n}:00+08:00',
                fact_identity=fact_identity(title),url=f'https://www.cls.cn/detail/{n}',doc_ids=[f'doc:{n}'],score=80-n)
        gold=[item('纽约期金日内涨0.69%突破4210美元/盎司',1),item('现货黄金日内涨0.62%突破4190美元/盎司',2)]
        self.assertFalse(shared.content_same(*gold))
        row=shared.compact_markets(gold,Counter())[0]
        self.assertIn('4210',shared.presentation_title(row))
        self.assertIn('4190',shared.presentation_title(row))
        self.assertEqual(len(list(shared.covered_items(row))),2)
        brent=shared.compact_markets([item('布伦特原油失守105美元/桶',1),item('布伦特原油失守107美元/桶',2)],Counter())
        self.assertEqual(len(brent),1)
        self.assertEqual(shared.presentation_title(brent[0]),'布伦特原油日内下跌（多来源报价）')
        shared.validate_coverage_group(brent[0])


if __name__=='__main__':
    unittest.main()
