"""Financial-vehicle activity outranks themed names, not real industry facts."""
from dataclasses import replace
from datetime import timedelta
import html
import json
import re
import unittest

import curated_events as curated
from home_categories import classify_text, classify_event
from test_curated_home import sample_item


class FundCategoryTests(unittest.TestCase):
    def test_fund_trading_notices_are_finance_across_sectors_and_vehicles(self):
        titles = [
            '全球芯片LOF：本基金将于2026年10月19日开市起停牌，10:30起复牌',
            '人工智能LOF发布临时停牌公告',
            '半导体ETF发布分红公告',
            '科创50ETF今日净申购超过10亿元',
            '中证A500ETF份额突破百亿份',
            '黄金ETF溢价过高，发布交易风险提示',
            '汽车ETF恢复交易',
            '光伏ETF暂停申购和赎回',
            '新能源基金净值大涨',
            '消费主题基金宣布分红',
            '物流主题基金调仓，增持快递龙头',
            '军工ETF发布溢价风险提示',
            '国防LOF明日复牌',
            '港股科技QDII限购',
            '机器人FOF下调管理费',
            '新能源基础设施REITs份额上市交易',
            '科技指数基金托管费率调整',
            '芯片lof将恢复交易',
            '【半导体ETF暂停交易】新一代AI芯片研发取得突破',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title,'芯片企业研发大模型，新品将投产'), 'finance')
                self.assertEqual(classify_event(title,[sample_item(title),sample_item('芯片公司发布新产品')]),'finance')

    def test_financial_product_launch_is_not_an_industrial_product_launch(self):
        for title in ['首只人工智能主题基金获批发行','新消费ETF正式推出',
                      '光伏LOF发布上市公告','半导体ETF联接基金今日发售']:
            with self.subTest(title=title): self.assertEqual(classify_text(title),'finance')

    def test_fund_mentions_do_not_swallow_real_industry_activity(self):
        cases = {
            '英伟达发布新一代AI芯片':'hardtech',
            '芯片公司公布三季度财报，净利润增长':'hardtech',
            'AI芯片企业获得产业基金融资':'hardtech',
            '国家集成电路产业投资基金投资新建晶圆厂':'hardtech',
            '基金公司发布自研AI投研大模型':'hardtech',
            '半导体公司发布研发进展，基金经理参与调研':'hardtech',
            'ETF重仓企业英伟达公布季度财报':'hardtech',
            '半导体ETF重仓股发布财报':'hardtech',
            '基金重仓的芯片公司推出新产品':'hardtech',
            '基金重仓股宁德时代发布固态电池':'newenergy',
            '产业基金支持光伏企业扩建生产基地':'newenergy',
            '茅台公布三季度财报':'consumer',
            '特斯拉三季度营收超预期':'auto',
            '黄金期货价格上涨':'commodities',
            'LOFTER发布AI创作功能':'hardtech',
        }
        for title,category in cases.items():
            with self.subTest(title=title): self.assertEqual(classify_text(title),category)
        self.assertEqual(classify_text('芯片企业发布新产品','该公司也被基金持仓，ETF今日分红'),'hardtech')
        self.assertEqual(classify_text('这只产品有新安排','全球芯片LOF发布停牌及复牌公告'),'finance')
        self.assertEqual(classify_text('这家公司又有新动作','英伟达发布新一代AI芯片。其股东是某基金，基金发布分红公告。'),'hardtech')

    def test_feed_dateline_and_same_event_alternative_do_not_change_finance(self):
        title='财联社10月18日电，全球芯片LOF将于明日起停牌。'
        item=sample_item(title)
        self.assertEqual(classify_text(title),'finance')
        event=curated.cluster_home_events([item])[0]
        self.assertEqual(event.category,'finance')

    def test_expiry_reclassification_uses_the_same_financial_rule(self):
        old=sample_item('全球芯片LOF发布临时停牌公告')
        new=replace(old,source_id='weibo-account:1638782947',url='https://m.weibo.cn/detail/5000000000000089',
                    published_dt=old.published_dt+timedelta(minutes=1))
        event=curated.cluster_home_events([old,new])[0]
        self.assertEqual(event.category,'finance')
        markup=curated.home_event_row(event,1)
        states=json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"',markup)[1]))
        self.assertEqual(len(states),1)
        self.assertIn('data-category="finance"',states[0]['html'])


if __name__=='__main__': unittest.main()
