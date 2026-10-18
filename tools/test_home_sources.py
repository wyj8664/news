from dataclasses import replace
from datetime import datetime, timedelta
import json
import html
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_events as curated
import home_source_pool as pool
from news_event_clustering import article_url, deduplicate_reports
from news_freshness import CHINA, select_home_items, write_json


def report(title, *, source="cls-telegraph", url="", at=None, score=20):
    at = at or datetime.now(CHINA).replace(tzinfo=None, microsecond=0)
    return curated.RawItem(date=at.strftime("%Y-%m-%d"), title=title, url=url,
                           source_id=source, source_name=source, source_tier="媒体", source_type="来源动态",
                           source_weight=12, source_trust=80, kind="cls" if source == "cls-telegraph" else "weibo",
                           first_dt=at, last_dt=at, published_dt=at, collected_dt=at,
                           publication_source="native:publication", score=score)


class EventGroupingTests(unittest.TestCase):
    def test_same_event_cross_media_paraphrases_and_aliases_merge(self):
        items = [report("英伟达发布新一代人工智能芯片", url="https://www.cls.cn/detail/1001"),
                 report("NVIDIA推出新一代AI芯片", source="weibo-account:1638782947", url="https://m.weibo.cn/detail/5000000000000001")]
        events = curated.cluster_home_events(items)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_count, 2)
        self.assertEqual(len(events[0].items), 2)

    def test_same_company_different_events_do_not_merge(self):
        events = curated.cluster_home_events([report("英伟达发布新一代AI芯片"), report("英伟达完成50亿美元融资")])
        self.assertEqual(len(events), 2)

    def test_same_company_and_action_different_products_do_not_merge(self):
        self.assertEqual(len(curated.cluster_home_events([report("苹果发布新款手机"), report("苹果发布人工智能模型")])), 2)

    def test_broad_iran_or_gold_topic_is_not_an_event_key(self):
        items = [report("伊朗发生地震救援正在进行"), report("伊朗与美国举行核谈判"),
                 report("黄金价格上涨创下新高"), report("央行增加黄金储备")]
        self.assertEqual(len(curated.cluster_home_events(items)), 4)

    def test_different_quarters_and_model_versions_stay_separate(self):
        items = [report("Anthropic Q1营收大涨46%"), report("Anthropic Q3营收大涨46%"),
                 report("OpenAI发布GPT5模型"), report("OpenAI发布GPT6模型")]
        self.assertEqual(len(curated.cluster_home_events(items)), 4)

    def test_website_and_own_weibo_are_one_media_but_keep_both_links(self):
        items = [report("Swisse褪黑素检出铅超标", url="https://www.cls.cn/detail/1001"),
                 report("Swisse褪黑素铅含量超标", source="cls-hot", url="https://m.weibo.cn/detail/5000000000000001")]
        events = curated.cluster_home_events(items)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_count, 1)
        self.assertEqual(len(events[0].items), 2)

    def test_repeated_capture_same_url_does_not_add_a_report_or_heat(self):
        first = report("同一条内容", url="https://www.cls.cn/detail/1001")
        repeated = replace(first, source_id="cls-hot", count=99)
        chosen = deduplicate_reports([first, repeated])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].count, 1)

    def test_thepaper_website_and_new_weibo_account_are_one_publisher(self):
        items = [report("上海警方通报一起诈骗案件", source="thepaper", url="https://www.thepaper.cn/newsDetail_forward_1001"),
                 report("上海警方通报一起诈骗案件", source="weibo-account:5044281310", url="https://m.weibo.cn/detail/5000000000000003")]
        event = curated.cluster_home_events(items)[0]
        self.assertEqual(event.source_count, 1)
        self.assertEqual(len(event.items), 2)
        self.assertEqual(event.category, "society")

    def test_quote_or_stock_page_url_is_not_a_unique_news_report(self):
        self.assertEqual(article_url("https://xueqiu.com/S/SH600519"), "")
        self.assertEqual(article_url("https://www.jin10.com/"), "")

    def test_identical_headlines_from_two_media_keep_both_original_links(self):
        events = curated.cluster_home_events([report("央行宣布下调政策利率", url="https://www.cls.cn/detail/1001"),
            report("央行宣布下调政策利率", source="weibo-account:1638782947", url="https://m.weibo.cn/detail/5000000000000001")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_count, 2)
        html = curated.home_event_row(events[0], 1)
        self.assertIn("2 条来源报道", html)
        self.assertIn("https://www.cls.cn/detail/1001", html)
        self.assertIn("https://m.weibo.cn/detail/5000000000000001", html)

    def test_same_incident_long_and_short_reports_merge(self):
        titles = ["财联社10月18日电，加拿大温赖特军事基地发生爆炸和火灾，至少7人死亡。", "加拿大军事基地爆炸起火多人伤亡"]
        self.assertEqual(len(curated.cluster_home_events([report(title) for title in titles])), 1)

    def test_distinct_quakes_and_news_digests_do_not_merge(self):
        titles = ["中国地震台网正式测定：10月18日05时08分在缅甸发生4.3级地震", "中国地震台网正式测定：10月18日02时30分在西藏阿里地区发生3.8级地震",
                  "金十数据整理：每日科技要闻速递（2026-10-18）", "金十数据整理：昨日今晨重要新闻汇总（2026-10-18）"]
        self.assertEqual(len(curated.cluster_home_events([report(title) for title in titles])), 4)

    def test_event_keeps_current_report_when_its_older_report_expires(self):
        now = datetime.now(CHINA).replace(tzinfo=None)
        old = report("英伟达发布新一代AI芯片", at=now - timedelta(hours=23), url="https://www.cls.cn/detail/1001")
        new = report("英伟达发布新一代AI芯片", at=now - timedelta(hours=1), source="weibo-account:1638782947", url="https://m.weibo.cn/detail/5000000000000001")
        event = curated.cluster_home_events([old, new])[0]
        markup = curated.home_event_row(event, 1)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        self.assertEqual(len(states), 1)
        self.assertIn(new.url, states[0]["html"])
        self.assertNotIn(old.url, states[0]["html"])
        self.assertIn('data-item-count="1"', states[0]["html"])
        self.assertGreater(states[0]["until"], int((now + timedelta(hours=1)).replace(tzinfo=CHINA).timestamp() * 1000))


class SourcePoolTests(unittest.TestCase):
    def test_home_can_be_built_from_cls_alone_without_any_hotlist_database(self):
        now = datetime.now(CHINA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            root.mkdir()
            write_json(root / "source-pool" / "cls-telegraph.json", {"fetched_at": now.isoformat(), "window_complete": True, "items": [
                {"id": 1001, "title": "国产芯片普通电报没有上过热榜也进入领域分类", "ctime": int(now.timestamp()), "level": "C", "url": "https://www.cls.cn/detail/1001"}]})
            with mock.patch.object(curated, "load_jin10_items", return_value=[]):
                curated.generate(root, 2, 24, 50, current_pages_only=True, event_db=root.parent / "test-registry.sqlite3")
            page = (root / "index.html").read_text()
            self.assertIn("国产芯片普通电报没有上过热榜也进入领域分类", page)
            self.assertIn('id="home-item-count">1</strong>', page)

    def test_cls_normal_level_and_jin10_unimportant_record_enter_without_rank(self):
        now = datetime.now(CHINA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "source-pool" / "cls-telegraph.json", {"fetched_at": now.isoformat(), "window_complete": True, "items": [
                {"id": 1001, "title": "普通电报也要进入分类", "ctime": int(now.timestamp()), "level": "C", "url": "https://www.cls.cn/detail/1001"}]})
            write_json(root / "source-pool" / "jin10-all.json", {"fetched_at": now.isoformat(), "public_only": True, "window_complete": True, "items": [
                {"id": "j1", "title": "没有重要和热度标签的金十快讯", "published_at": now.isoformat(), "important": 0, "hot": "", "url": "https://www.jin10.com/"}]})
            items, statuses = curated.load_native_pool_items(root)
            selected, _ = select_home_items(items, root, now=now, max_fetches=0)
            self.assertEqual(len(selected), 2)
            self.assertTrue(all(value.best_rank is None for value in selected))
            self.assertEqual({status["source_id"] for status in statuses}, {"cls-telegraph", "jin10-all", "jiemian-flash"})

    def test_failed_fetch_preserves_cache_and_does_not_touch_wechat(self):
        now = datetime.now(CHINA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source-pool" / "jin10-all.json"
            old = {"fetched_at": (now - timedelta(hours=1)).isoformat(), "items": [{"id": "old"}], "window_complete": True}
            write_json(path, old)
            before = path.read_bytes()
            with mock.patch.object(pool, "fetch_jin10", side_effect=RuntimeError("offline")):
                result = pool.refresh(root, force=True, config={"native_feeds": ["jin10-all"]})
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(result["sources"][0]["ok"])
            self.assertFalse(result["wechat_collection_enabled"])

    def test_partial_fetch_keeps_recent_cache_with_original_observation_time(self):
        now = datetime.now(CHINA)
        previous_capture = (now - timedelta(hours=1)).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source-pool" / "jin10-all.json"
            write_json(path, {"fetched_at": previous_capture, "public_only": True, "items": [{"id": "old", "published_at": (now - timedelta(hours=2)).isoformat()}], "window_complete": True})
            partial = {"ok": True, "items": [{"id": "new", "published_at": now.isoformat()}], "window_complete": False, "errors": ["partial"]}
            with mock.patch.object(pool, "fetch_jin10", return_value=partial):
                pool.refresh(root, force=True, config={"native_feeds": ["jin10-all"]})
            saved = json.loads(path.read_text())
            self.assertEqual({entry["id"] for entry in saved["items"]}, {"new", "old"})
            self.assertEqual(saved["item_observed_at"]["old"], previous_capture)
            self.assertFalse(saved["window_complete"])

    def test_collector_includes_unimportant_jin10_and_stops_at_window_boundary(self):
        now = datetime.now(CHINA).replace(microsecond=0)
        batch = [{"id": "ordinary", "time": now.strftime("%Y-%m-%d %H:%M:%S"), "important": 0, "data": {"content": "【普通新闻】不是热门也要进入来源池"}},
                 {"id": "old", "time": (now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S"), "important": 1, "data": {"content": "旧闻"}}]
        with mock.patch.object(curated, "fetch_remote_json", return_value={"data": batch}):
            result = pool.fetch_jin10(now, 2)
        self.assertEqual([entry["id"] for entry in result["items"]], ["ordinary"])
        self.assertTrue(result["window_complete"])


if __name__ == "__main__":
    unittest.main()
