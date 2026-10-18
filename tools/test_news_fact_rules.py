"""Generalized positive/negative matrices and durable upgrade coverage."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import curated_events as curated
import event_registry as registry
from home_categories import ALL_ONLY_CATEGORY, classify_text, classify_event
from news_fact_normalization import canonical_quantities, financial_scope, quantity_tokens
from test_event_registry import doc


class MilitaryIntentTests(unittest.TestCase):
    def test_operational_and_diplomatic_tech_nouns_do_not_make_industry_news(self):
        titles = [
            '乌克兰外交部发言人称，俄方可能将乌克兰无人机转向立陶宛领空，乌方表示遗憾',
            '某国外交部回应卫星监控无人机领空争议',
            '俄军宣布研发新型AI无人机',
            '某国军方宣布量产新型无人机用于作战',
            '北约发言人称卫星将用于军事行动',
            '军用半导体芯片支持新型战斗机远程打击',
            '海军发布新一代无人机军演视频',
            '总统大选候选人称AI是未来，双方展开辩论',
            '两国首脑会谈，表示将推进量子技术合作',
            '韩国总统会见科技企业代表',
            '某国海军驱逐舰在区域海域航行，支持针对另一国港口的海上封锁行动',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title,'相关企业研发人工智能芯片并投产'),ALL_ONLY_CATEGORY)
                self.assertEqual(classify_event(title,[doc(title,1),doc('某芯片公司发布新产品',2)]),ALL_ONLY_CATEGORY)

    def test_civilian_industry_policy_markets_and_cultural_products_survive(self):
        cases = {
            '大疆农业无人机量产交付':'hardtech',
            '工业无人机用于电网巡检，民用订单增长':'hardtech',
            '商业航天公司发布卫星量产计划':'hardtech',
            '美国总统宣布芯片工厂投产':'hardtech',
            '美国国防部提出AI芯片出口管制新规':'hardtech',
            '欧盟上调中国新能源汽车关税':'auto',
            '委内瑞拉代总统：未来五年石油天然气投资额760亿美元':'commodities',
            '战争推高原油价格':'commodities',
            '外交争端推高航运运价':'logistics',
            '某国军方称海峡仍保持通航':'logistics',
            '电商平台：一处物流枢纽在无人机袭击后起火':'logistics',
            '美联储主席宣布降息25个基点':'finance',
            '战争电影发布新预告片':'consumer',
            '军事题材游戏上线，首日销量创新高':'consumer',
        }
        for title,category in cases.items():
            with self.subTest(title=title): self.assertEqual(classify_text(title),category)

    def test_vague_title_does_not_borrow_military_technology_from_summary(self):
        self.assertEqual(classify_text('这家公司又有新动作','俄军宣布研发军用AI无人机用于作战'),ALL_ONLY_CATEGORY)


class QuantityFactTests(unittest.TestCase):
    def test_precision_grouping_fullwidth_signs_and_unit_conversions(self):
        for a,b in [('104.20%','104.2%'),('１０４．２００％','104.2%'),('1,000.00万元','0.1亿元'),
                    ('5000万元','0.5亿元'),('1亿美元','10000万美元'),('5亿至8亿元','5亿元-8亿元'),
                    ('-1.00亿元','-10000万元'),('+104.2%','104.20%')]:
            with self.subTest(a=a,b=b): self.assertEqual(quantity_tokens(a),quantity_tokens(b))
        for a,b in [('1亿元','1亿美元'),('1%','1个百分点'),('-1亿元','1亿元'),('104.2%','104.3%')]:
            with self.subTest(a=a,b=b): self.assertNotEqual(quantity_tokens(a),quantity_tokens(b))
        self.assertEqual(canonical_quantities('GPT-5.10版, iPhone18, Q3'), 'GPT-5.10版, iPhone18, Q3')

    def test_financial_synonyms_are_one_fact_for_unlisted_companies_too(self):
        cases = [
            ['恒力石化：预计前三季度净利润同比增长104.20%','恒力石化：预计前三季度归母净利润同比增长104.2%'],
            ['甲材料：预计前三季度归母净利润同比增长25.00%','甲材料：预计前三季度归属于上市公司股东的净利润同比增加25%'],
            ['乙科技：2026年一季度营收10亿元','乙科技：2026年第一季度营业收入100000万元'],
            ['丙制造：前三季度扣非归母净利润增长20.00%','丙制造：前三季度扣非净利润同比增长20%'],
        ]
        for titles in cases:
            with self.subTest(titles=titles):
                events=curated.cluster_home_events([doc(title,i) for i,title in enumerate(titles)])
                self.assertEqual(len(events),1)
                self.assertEqual(len(events[0].progresses),1)

    def test_metric_period_stage_direction_currency_and_real_values_do_not_collapse(self):
        pairs = [
            ('甲公司前三季度净利润同比增长20%','甲公司前三季度营收同比增长20%'),
            ('甲公司前三季度归母净利润增长20%','甲公司前三季度扣非净利润增长20%'),
            ('甲公司前三季度合并净利润增长20%','甲公司前三季度归母净利润增长20%'),
            ('甲公司前三季度净利润增长20%','甲公司第三季度净利润增长20%'),
            ('甲公司2025年前三季度净利润增长20%','甲公司2026年前三季度净利润增长20%'),
            ('甲公司预计前三季度净利润增长20%','甲公司前三季度净利润实现增长20%'),
            ('甲公司前三季度净利润增长20%','甲公司前三季度净利润下降20%'),
            ('甲公司前三季度净利润同比增长20%','甲公司前三季度净利润环比增长20%'),
            ('甲公司前三季度净利润1亿元','甲公司前三季度净利润1亿美元'),
            ('甲公司前三季度净利润亏损1亿元','甲公司前三季度净利润盈利1亿元'),
            ('甲公司前三季度净利润同比增长20%','甲公司前三季度净利润同比增长21%'),
            ('甲公司预计前三季度净利润20亿元','甲公司修正前三季度净利润预告为20亿元'),
        ]
        for a,b in pairs:
            with self.subTest(a=a,b=b):
                left,right=registry.statements(doc(a,1))[0],registry.statements(doc(b,2))[0]
                self.assertFalse(registry.same_progress(left,right))

    def test_summary_resolves_abbreviation_without_borrowing_later_quarter(self):
        scope=financial_scope('甲公司：预计前三季度净利润增长20%',
            '预计2026年前三季度归属于上市公司股东的净利润增长20%。注：第三季度环比下降10%。')
        self.assertEqual(scope['metrics'],['parent-net-profit'])
        self.assertEqual(scope['year'],'2026')
        self.assertEqual(scope['period'],'YTDQ3')
        self.assertEqual(scope['basis'],'')

    def test_datelines_and_money_normalization_do_not_merge_different_companies(self):
        titles = ['财联社10月18日电，甲材料：董事长提议回购2亿至3亿元股份',
                  '财联社10月18日电，乙科技：董事长提议回购5000万元至1亿元股份',
                  '金十数据10月18日电，丙制造：董事长提议回购2亿至3亿元股份']
        events=curated.cluster_home_events([doc(title,i) for i,title in enumerate(titles)])
        self.assertEqual(len(events),3)
        social=['甲材料拟回购最高3亿元股份 【甲材料董事长提议回购2亿至3亿元股份】',
                '乙科技回购【乙科技：董事长提议回购5000万元至1亿元股份】']
        events=curated.cluster_home_events([doc(title,i) for i,title in enumerate(social+titles)])
        self.assertEqual(len(events),3)

    def test_old_unsigned_tokens_are_upgraded_without_a_new_progress(self):
        title='某统计局：一季度GDP修正为-1.5%，此前为-1.7%'
        old=registry.Statement('report',title,'old-derived-claim',('1.5%','1.7%'))
        self.assertTrue(registry.same_progress(old,registry.statements(doc(title,1))[0]))

    def test_rule_upgrade_does_not_mix_stale_subject_features(self):
        docs=[doc('财联社10月18日电，甲材料：董事长提议回购2亿至3亿元股份',1),
              doc('财联社10月18日电，乙科技：董事长提议回购5000万元至1亿元股份',2)]
        extractor=registry.features
        def old_features(item):
            value=extractor(item)
            return replace(value,subject='',entities=set())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.sqlite3'
            with mock.patch.object(registry,'VERSION',1), mock.patch.object(registry,'features',side_effect=old_features), \
                 mock.patch.object(registry,'event_match',return_value=(0,'old-separate')):
                before=curated.cluster_home_events(docs,registry_path=path)
            after=curated.cluster_home_events(docs,registry_path=path)
            self.assertEqual(len(after),2)
            self.assertEqual({e.key for e in before},{e.key for e in after})

    def test_upgrade_merges_existing_nodes_with_old_id_alias_and_no_resend(self):
        a=doc('甲材料：预计前三季度净利润同比增长104.20%',1)
        b=replace(doc('甲材料：预计前三季度归母净利润同比增长104.2%',2),published_dt=a.published_dt+timedelta(minutes=2))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.sqlite3'
            # Reproduce the v1 strict-string bug, then upgrade the SAME database.
            with mock.patch.object(registry,'VERSION',1),mock.patch.object(registry,'amount_tokens',side_effect=lambda text: ('104.20%',) if '104.20%' in text else ('104.2%',)), \
                 mock.patch.object(registry,'same_progress',side_effect=lambda x,y:x.kind==y.kind and x.numbers==y.numbers):
                before=curated.cluster_home_events([a,b],registry_path=path)[0]
            self.assertEqual(len(before.progresses),2)
            aliases={}
            after=curated.cluster_home_events([a,b],registry_path=path,aliases=aliases)[0]
            self.assertEqual(after.key,before.key)
            self.assertEqual(len(after.progresses),1)
            self.assertEqual(after.progresses[0].key,before.progresses[0].key)
            self.assertEqual(after.progresses[0].first_reported_at,before.progresses[0].first_reported_at)
            self.assertEqual(len(after.progresses[0].doc_ids),2)
            old=before.progresses[1].key
            self.assertEqual(aliases['progresses'][old],after.progresses[0].key)
            from briefing_shared import sent_progress_ids
            history={'batches':{'previous':{'pipeline':'shared-home-v1','delivered_items':[{'progress_id':old}]}}}
            self.assertIn(after.progresses[0].key,sent_progress_ids(history,aliases['progresses']))
            repeated=curated.cluster_home_events([b,a],registry_path=path)[0]
            self.assertEqual(repeated.progresses[0].key,after.progresses[0].key)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())


if __name__=='__main__': unittest.main()
