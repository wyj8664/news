"""Only public Jin10 flashes enter the shared, date-independent source pool."""
from datetime import datetime, timedelta
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_events as curated
import home_source_pool as pool
from jin10_public import public_jin10_record
from news_freshness import CHINA, write_json


def raw_record(title="金十测试重要快讯", *, at="2026-10-18 16:00:00", important=1, hot=""):
    return {"id": title, "title": title, "time": at, "important": important, "hot": hot,
            "data": {"source_link": "https://www.jin10.com/"}}


class Jin10SourceTests(unittest.TestCase):
    def test_live_ordinary_items_are_not_filtered_by_old_platform_date(self):
        normal = [raw_record(), raw_record("普通快讯", important=0)]
        normal.append(raw_record("金十昨日热门快讯", at="2026-10-17 23:30:00", important=0, hot="热"))
        with mock.patch.object(curated, "fetch_jin10_classify_map", return_value={}), \
             mock.patch.object(curated, "fetch_jin10_normal_flash_pages", return_value=normal):
            items = curated.load_jin10_items("2026-10-16")
        self.assertEqual({item.title for item in items}, {"金十测试重要快讯", "普通快讯", "金十昨日热门快讯"})
        self.assertEqual(items[0].date, "2026-10-18")
        ordinary = next(item for item in items if item.title == "普通快讯")
        self.assertEqual(ordinary.rank_label, "普通快讯")

    def write_snapshot(self, root, records, complete=True):
        value = {"public_only": True, "fetched_at": "2026-10-18T17:00:00+08:00", "window_complete": complete,
                 "items": [{"id": str(index), "published_at": "2026-10-18T16:00:00+08:00",
                            "important": 0, "hot": "", **record} for index, record in enumerate(records)]}
        path = root / "source-pool" / "jin10-all.json"
        write_json(path, value)
        return path

    def test_shared_pool_is_read_without_network_or_rewriting_capture_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = self.write_snapshot(root, [{"title": "普通快讯"}, {"title": "重要快讯", "important": 1}])
            original = cache.read_bytes()
            with mock.patch.object(curated, "fetch_remote_json", side_effect=AssertionError("unexpected network")):
                items, state = curated.load_jin10_source_items(root)
            self.assertEqual(cache.read_bytes(), original)
            self.assertEqual({item.title for item in items}, {"普通快讯", "重要快讯"})
            self.assertTrue(state["cached"])
            self.assertTrue(state["window_complete"])
            self.assertEqual(state["fetched_at"].isoformat(), "2026-10-18T17:00:00+08:00")

    def test_more_than_80_ordinary_records_and_permission_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root, [{"title": f"普通快讯{index}"} for index in range(130)] + [
                {"title": "不采集的受限新闻", "is_vip": True}, {"title": "PLUS专享快讯"}])
            items, state = curated.load_jin10_source_items(root)
            self.assertEqual(len(items), 130)
            self.assertTrue(state["window_complete"])

    def test_unverified_legacy_cache_is_not_reused_but_fixed_entry_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "source-pool" / "jin10-all.json", {"items": [{"title": "旧权限状态未核验"}]})
            with mock.patch.object(curated, "load_hotlist_items", return_value=[]), \
                 mock.patch.object(curated, "load_weibo_items", return_value=[]):
                curated.render_source_hotlists(root, "2026-10-16")
            page = (root / "hotlists" / "index.html").read_text()
            controls = re.search(r'id="source-controls">(.*?)</div>', page, re.S)[1]
            self.assertIn('data-source="jin10-flash">金十数据 <span>0</span>', controls)
            self.assertNotIn('id="jin10-refresh"', controls)
            section = re.search(r'data-source-section="jin10-flash">(.*?)</section>', page, re.S)[1]
            self.assertIn('id="jin10-refresh"', section)
            self.assertIn('id="jin10-refresh-status"', section)
            self.assertIn('aria-controls="jin10-items"', section)
            self.assertIn('>刷新快讯</button>', section)
            self.assertEqual(page.count('id="jin10-refresh"'), 1)
            self.assertIn('data-source-section="jin10-flash"', page)
            self.assertNotIn("旧权限状态未核验", page)
            self.assertIn("金十与关注微博按各自更新时间加载", page)

    def test_ordinary_news_is_visible_on_old_platform_snapshot_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root, [{"title": "Anthropic发布普通AI智能体研究"}])
            with mock.patch.object(curated, "load_hotlist_items", return_value=[]), \
                 mock.patch.object(curated, "load_weibo_items", return_value=[]):
                curated.render_source_hotlists(root, "2026-10-16")
            page = (root / "hotlists" / "index.html").read_text()
            self.assertIn("Anthropic发布普通AI智能体研究", page)
            self.assertIn("发布 10-18 16:00", page)
            self.assertIn('id="source-total-count">1</strong>', page)
            self.assertIn('data-source="jin10-flash">金十数据 <span>1</span>', page)
            self.assertIn("普通+重要+热门均收录", page)
            controls = re.search(r'id="source-controls">(.*?)</div>', page, re.S)[1]
            self.assertNotIn('id="jin10-refresh"', controls)

    def test_permission_flags_and_unlock_placeholders_are_rejected(self):
        for field in ("is_vip", "is_plus", "need_login", "need_pay", "login_required", "paywall"):
            for raw in (dict(raw_record(), **{field: True}), dict(raw_record(), data={field: True})):
                with self.subTest(raw=raw):
                    self.assertFalse(public_jin10_record(raw))
                    self.assertIsNone(curated.normalize_jin10_item(raw, {}))
        for title in ("VIP专享快讯", "PLUS专享快讯", "请登录后查看", "解锁VIP内容", "金十PLUS专享快讯", "解锁文章"):
            self.assertIsNone(curated.normalize_jin10_item(raw_record(title), {}))
        self.assertTrue(public_jin10_record(dict(raw_record(), is_vip=0, need_login="false")))
        self.assertFalse(public_jin10_record(dict(raw_record(), access="unknown")))

    def test_collector_skips_restricted_and_empty_page_is_not_complete(self):
        now = datetime.now(CHINA).replace(microsecond=0)
        at = now.strftime("%Y-%m-%d %H:%M:%S")
        batch = [raw_record("普通公开新闻", at=at, important=0),
                 dict(raw_record("受限新闻", at=at), is_vip=True),
                 raw_record("旧闻", at=(now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S"))]
        with mock.patch.object(curated, "fetch_remote_json", return_value={"data": batch}):
            result = pool.fetch_jin10(now, 2)
        self.assertEqual([item["title"] for item in result["items"]], ["普通公开新闻"])
        self.assertEqual(result["skipped_restricted"], 1)
        self.assertTrue(result["public_only"])
        self.assertTrue(result["window_complete"])
        with mock.patch.object(curated, "fetch_remote_json", return_value={"data": []}):
            self.assertFalse(pool.fetch_jin10(now, 2)["window_complete"])

    def test_industry_news_enters_home_military_stays_in_sources(self):
        now = datetime.now(CHINA).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            root.mkdir()
            path = self.write_snapshot(root, [{"title": "Anthropic发布公开AI智能体研究", "published_at": now},
                                       {"title": "加拿大军事基地爆炸", "published_at": now},
                                       {"title": "受限VIP报道", "is_vip": True, "published_at": now}])
            snapshot = pool.read_snapshot(path)
            snapshot["fetched_at"] = now
            write_json(path, snapshot)
            curated.generate(root, 2, 24, 50, current_pages_only=True, event_db=Path(directory) / "test-registry.sqlite3")
            home = (root / "index.html").read_text()
            sources = (root / "hotlists" / "index.html").read_text()
            self.assertIn("Anthropic发布公开AI智能体研究", home)
            self.assertNotIn("加拿大军事基地爆炸", home)
            self.assertIn("加拿大军事基地爆炸", sources)
            self.assertNotIn("受限VIP报道", home + sources)


if __name__ == "__main__":
    unittest.main()
