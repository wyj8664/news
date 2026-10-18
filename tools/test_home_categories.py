"""Content-based homepage sector classification and boundary regressions."""
from dataclasses import replace
from datetime import timedelta
import html
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_events as curated
from home_categories import ALL_ONLY_CATEGORY, CATEGORY_LABELS, CATEGORY_ORDER, classify_text
from test_curated_home import sample_item


class HomeCategoryTests(unittest.TestCase):
    def test_exact_user_order_and_labels(self):
        self.assertEqual([CATEGORY_LABELS[key] for key in CATEGORY_ORDER],
                         ["硬科技", "大宗", "新消费", "汽车", "新能源", "金融", "物流", "社会"])

    def test_content_examples_and_boundaries(self):
        cases = {
            "英伟达发布新一代AI芯片": "hardtech",
            "Anthropic宣布新一轮融资": "hardtech",
            "小米发布折叠屏手机": "hardtech",
            "国产大飞机航空制造取得突破": "hardtech",
            "黄金期货价格上涨": "commodities",
            "央行连续增持黄金储备": "commodities",
            "布伦特原油价格回落": "commodities",
            "碳酸锂期货价格下跌": "commodities",
            "茅台公布三季度财报": "consumer",
            "Swisse褪黑素检出铅超标": "consumer",
            "黄金周旅游消费创新高": "consumer",
            "新能源汽车销量增长30%": "auto",
            "特斯拉三季度营收超预期": "auto",
            "小米汽车正式发布新车": "auto",
            "华为发布新一代智能驾驶方案": "auto",
            "比亚迪发布固态电池": "newenergy",
            "特斯拉上海储能工厂投产": "newenergy",
            "光伏行业利润改善": "newenergy",
            "宁德时代宣布扩产": "newenergy",
            "美联储宣布降息25个基点": "finance",
            "平安银行公布三季报": "finance",
            "英伟达股价大跌": "finance",
            "半导体ETF领涨科技板块": "finance",
            "黄金ETF上涨": "finance",
            "沪指开盘上涨": "finance",
            "中通快递三季度营收增长": "logistics",
            "京东物流新增自动化仓储中心": "logistics",
            "港口集装箱吞吐量创历史新高": "logistics",
            "地震救援正在进行": ALL_ONLY_CATEGORY,
            "俄圣彼得堡遭乌无人机袭击致多人伤亡": ALL_ONLY_CATEGORY,
            "加拿大军事基地发生爆炸": ALL_ONLY_CATEGORY,
            "俄罗斯国防部称击落30架无人机": ALL_ONLY_CATEGORY,
            "乌克兰请求获得军用卫星支持远程打击": ALL_ONLY_CATEGORY,
            "阿根廷将获德法33.55亿美元融资购买潜艇": ALL_ONLY_CATEGORY,
            "汽车冲入足球比赛场地造成伤亡": ALL_ONLY_CATEGORY,
            "总统称从委内瑞拉拿走了数千亿美元": ALL_ONLY_CATEGORY,
            "美国总统与英国首相举行会谈": ALL_ONLY_CATEGORY,
            "全国铁路发送旅客创同期新高": "society",
            "世界杯比赛正式开幕": ALL_ONLY_CATEGORY,
            "这是普通综合新闻": ALL_ONLY_CATEGORY,
        }
        for title, category in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify_text(title), category)

    def test_financial_publisher_and_no_rank_do_not_determine_category(self):
        for source in ("cls-telegraph", "jin10-flash", "weibo-account:1638782947"):
            item = replace(sample_item("光伏企业推出新型储能电池"), source_id=source,
                           best_rank=None, score=5)
            event = curated.cluster_home_events([item])[0]
            self.assertEqual(event.category, "newenergy")

    def test_consumer_includes_platform_operations_and_creator_business(self):
        cases = [
            "疯狂小杨哥新号粉丝被清零",
            "三只羊获准重返抖音恢复经营",
            "小杨哥新账号无法涨粉",
            "三只羊将从MCN公司转型为电商公司",
            "董宇辉直播间带货销售额创新高",
            "东方甄选调整主播分成规则",
            "李佳琦宣布复播",
            "爱奇艺上调VIP会员价格",
            "腾讯视频调整会员投屏规则",
            "B站月活用户增长，广告收入上升",
            "哔哩哔哩三季度财报公布",
            "优酷推出付费会员新服务",
            "抖音上线新功能，拓展商家获客渠道",
            "快手处罚违规带货主播",
            "小红书调整商业化业务",
            "Netflix订阅收入增长",
            "网易云音乐推出会员订阅新服务",
            "米哈游公布新游戏上线计划",
            "网易游戏发布三季度财报",
        ]
        for title in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title), "consumer")
        self.assertEqual(classify_text("小杨哥回应了", "新注册的抖音账号粉丝被清零，无法新增关注。"), "consumer")

    def test_consumer_includes_cultural_products_and_experience_services(self):
        cases = [
            "国庆档电影票房突破50亿元",
            "电影《地震》宣布定档",
            "战争电影发布新预告片",
            "春节档新片预售开启",
            "电视剧《山海》宣布开播",
            "腾讯视频新剧集上线",
            "爱奇艺综艺宣布定档",
            "微短剧分账收入增长",
            "网络文学IP改编带动数字阅读消费",
            "动漫产业授权业务增长",
            "国产网络游戏取得新版号",
            "游戏发行商宣布新产品上线",
            "演唱会票价上涨引发退票争议",
            "音乐节赞助收入增长",
            "线下演出市场收入增长",
            "泡泡玛特推出新系列潮玩",
            "霸王茶姬发布新品茶饮",
        ]
        for title in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title), "consumer")

    def test_platform_mentions_gossip_and_real_incidents_are_not_consumer(self):
        cases = [
            ("某演员恋情曝光", ""),
            ("女演员生日造型登上热搜", ""),
            ("电影明星红毯穿搭引发热议", ""),
            ("三只羊主播举办婚礼", ""),
            ("嫁金钗周末你推荐看什么电视剧？", ""),
            ("这部电影你喜欢吗", ""),
            ("粉丝为电影明星组织生日应援", ""),
            ("网友在抖音发布日常视频", ""),
            ("抖音博主说自己在巴黎遭遇盗抢", ""),
            ("抖音视频记录伊朗遭导弹袭击", ""),
            ("电影院发生火灾致多人伤亡", ""),
            ("世界杯比赛正式开幕", "有人在快手发布了比赛视频。"),
        ]
        for title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title, summary), ALL_ONLY_CATEGORY)
        self.assertEqual(classify_text("上海电影院发生火灾，消防紧急救援"), "society")
        self.assertEqual(classify_text("某演员恋情曝光，代言品牌宣布解约"), "consumer")

    def test_expanded_consumer_does_not_swallow_other_sectors(self):
        cases = {
            "爱奇艺股价大跌": "finance",
            "影视ETF涨停": "finance",
            "快手发布新一代AI大模型": "hardtech",
            "字节跳动宣布研发AI芯片": "hardtech",
            "小米汽车推出电影联名车型": "auto",
            "比亚迪发布固态电池": "newenergy",
            "黄金期货价格上涨": "commodities",
            "京东物流宣布新仓储项目": "logistics",
            "女研究员老鼠仓3年结局让人唏嘘": "finance",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify_text(title), expected)

    def test_digital_consumer_cluster_and_expiry_keep_one_event(self):
        old = replace(sample_item("疯狂小杨哥新号粉丝被清零"), source_id="weibo-account:1651428902",
                      summary="抖音账号的粉丝数归零，属于直播电商账号经营问题。")
        new = replace(old, source_id="weibo-account:1885454921", url="https://m.weibo.cn/detail/5000000000000019",
                      published_dt=old.published_dt + timedelta(minutes=1))
        events = curated.cluster_home_events([old, new])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, "consumer")
        self.assertEqual(events[0].source_count, 2)
        markup = curated.home_event_row(events[0], 1)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        self.assertEqual(len(states), 1)
        self.assertIn('data-category="consumer"', states[0]["html"])

    def test_new_consumer_event_is_rendered_on_home_once_per_section(self):
        import tempfile
        item = replace(sample_item("疯狂小杨哥新号粉丝被清零"), source_id="weibo-account:1651428902",
                       url="https://m.weibo.cn/detail/5355441821582286")
        events = curated.cluster_home_events([item])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curated.render_home(events, item.date, root, 24)
            page = (root / "index.html").read_text()
        section = re.search(r'<section class="category-section" data-category-section="consumer">(.*?)</section>', page, re.S)[1]
        self.assertEqual(section.count('data-event-id="' + events[0].key + '"'), 1)
        self.assertIn(item.title, section)

    def test_securities_misconduct_is_finance_not_society_or_company_sector(self):
        titles = [
            "女研究员老鼠仓3年结局让人唏嘘",
            "女研究员老鼠仓3年多，结局让人唏嘘",
            "上海法院通报女研究员老鼠仓案件判决",
            "芯片公司实控人内幕交易被处罚",
            "车企董事长涉嫌操纵证券市场被调查",
            "基金经理利用未公开信息交易获刑",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title), "finance")
        self.assertEqual(classify_text("女研究员获刑三年", "上海法院判决其利用未公开信息交易，俗称老鼠仓。"), "finance")
        reports = [sample_item(titles[0]), replace(sample_item(titles[1]), source_id="weibo-account:1638782947")]
        event = curated.cluster_home_events(reports)[0]
        self.assertEqual(event.category, "finance")
        self.assertEqual(len(event.items), 2)

    def test_summary_fills_only_vague_headline(self):
        self.assertEqual(classify_text("这家公司又有新动作", "宁德时代宣布投产新电池基地"), "newenergy")
        self.assertEqual(classify_text("地震救援进行中", "人工智能企业为灾区捐款"), ALL_ONLY_CATEGORY)
        self.assertEqual(classify_text("李强同乌兹别克斯坦总理会谈", "深化人工智能技术合作"), ALL_ONLY_CATEGORY)
        self.assertEqual(classify_text("官方回应网友在地铁不文明行为", "该网友此前关注人工智能产品"), ALL_ONLY_CATEGORY)
        self.assertEqual(classify_text("日媒曝日本政府考虑增税筹措防卫费", "相关人工智能技术用于军事"), ALL_ONLY_CATEGORY)
        self.assertEqual(classify_text("【黄金期货上涨】美元走弱，美联储或将降息"), "commodities")

    def test_society_requires_domestic_evidence_not_chinese_publisher(self):
        domestic = [
            ("广西大学通报学生高坠死亡", ""),
            ("中国地震台网正式测定西藏阿里发生3.8级地震", ""),
            ("冷空气接连影响我国 多地气温将创新低", ""),
            ("深圳警方通报一起诈骗案件", ""),
            ("官方回应网友曝有人在地铁上排泄", "事发北京立水桥，相关部门正调查。"),
            ("央视曝光老年人神药骗局", ""),
        ]
        for title, summary in domestic:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title, summary), "society")
        excluded = [
            ("中国地震台网正式测定缅甸发生4.3级地震", ""),
            ("人民日报报道美国纽约地铁站枪击事件", ""),
            ("中国驻日本使馆回应有关问题", ""),
            ("南海海域实弹射击", "国内发布的航行警告"),
            ("我国海军组织军事演习", ""),
            ("中国总理会见乌兹别克斯坦总统", ""),
            ("地震救援进行中", "新华社北京电，缅甸发生地震，救援仍在进行。"),
            ("军事基地爆炸致多人伤亡", "新华社北京报道"),
            ("多地发生大规模抗议", "目前法国警方已逮捕数百人。"),
            ("IMF亚太部副主任：中国积极维护多边贸易体系", "中国新闻发布"),
            ("蔷琵加强为超强台风", "中央气象台称台风位于关岛附近洋面。"),
            ("中国企业发布一款未识别领域的新产品", "中国企业正在推广产品。"),
        ]
        for title, summary in excluded:
            with self.subTest(title=title):
                self.assertEqual(classify_text(title, summary), ALL_ONLY_CATEGORY)

    def test_international_industry_news_keeps_its_sector(self):
        self.assertEqual(classify_text("美国英伟达发布新一代AI芯片"), "hardtech")
        self.assertEqual(classify_text("欧洲央行宣布降息"), "finance")
        self.assertEqual(classify_text("伊朗战争导致国际原油价格上涨"), "commodities")
        self.assertEqual(classify_text("【电报解读】深圳机器人出口激增"), "hardtech")
        self.assertEqual(classify_text("上海超导三期基地项目封顶"), "hardtech")

    def test_society_scope_remains_domestic_when_old_report_expires(self):
        old = sample_item("加拿大军事基地发生爆炸致多人伤亡")
        new = replace(old, source_id="weibo-account:1638782947", url="https://m.weibo.cn/detail/5000000000000002",
                      published_dt=old.published_dt + timedelta(minutes=1))
        event = curated.cluster_home_events([old, new])[0]
        self.assertEqual(event.category, ALL_ONLY_CATEGORY)
        markup = curated.home_event_row(event, 1)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        self.assertEqual(len(states), 1)
        self.assertIn('data-category="other"', states[0]["html"])
        self.assertNotIn('data-category="society"', states[0]["html"])

    def test_cluster_and_expiry_keep_new_taxonomy(self):
        old = sample_item("英伟达发布新一代AI芯片")
        new = replace(old, source_id="weibo-account:1638782947", url="https://m.weibo.cn/detail/5000000000000001",
                      published_dt=old.published_dt + timedelta(minutes=1))
        events = curated.cluster_home_events([old, new])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, "hardtech")
        markup = curated.home_event_row(events[0], 1)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        self.assertEqual(len(states), 1)
        self.assertIn('data-category="hardtech"', states[0]["html"])
        self.assertIn("硬科技</span>", states[0]["html"])


if __name__ == "__main__":
    unittest.main()
