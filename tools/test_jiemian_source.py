"""Public Jiemian flashes use native timestamps and the shared news pipeline."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_events as curated
import home_source_pool as pool
import jiemian_flash as jiemian
from news_freshness import CHINA, write_json


def card(key, when, title="国产芯片新进展", summary="公开列表摘要", extra=""):
    return (f'<div class="{jiemian.ITEM_CLASS}" data-id="{key}" data-time="{int(when.timestamp())}" {extra}>'
            f'<h4><a href="https://www.jiemian.com/article/{key}.html">{title}</a></h4>'
            f'<div class="{jiemian.SUMMARY_CLASS}">{summary}</div></div>')


def document(*cards):
    return "<script>var cid = '1323kb'; var tagid = '1323';</script>" + "".join(cards)


class JiemianSourceTests(unittest.TestCase):
    def test_parser_only_reads_visible_flash_cards_with_native_time(self):
        when = datetime(2026, 10, 18, 19, 18, 8, tzinfo=CHINA)
        parser = jiemian.FlashParser()
        parser.feed('<a href="/vip">无关会员导航</a>' + document(card("123", when,
                    title="国产<b>芯片</b>&amp;算力", summary="公开摘要<script>不采集脚本</script><img src='/a.png'>")))
        self.assertEqual(len(parser.records), 1)
        normalized = jiemian.normalize_record(parser.records[0])
        self.assertEqual(normalized["title"], "国产芯片&算力")
        self.assertEqual(normalized["summary"], "公开摘要")
        self.assertEqual(normalized["published_at"], "2026-10-18T19:18:08+08:00")

    def test_restricted_unknown_permission_and_unsafe_links_are_rejected(self):
        record = {"id": "123", "title": "普通公开快报", "publishtime": "1792322288"}
        for key in ("is_vip", "is_plus", "need_login", "is_paid", "paywall"):
            self.assertIsNone(jiemian.normalize_record({**record, key: True}))
        for extra in ({"access": "unknown"}, {"is_free": 0}, {"title": "会员专享报道"},
                      {"summary": "请登录后查看"}, {"url": "https://example.com/redirect"}):
            self.assertIsNone(jiemian.normalize_record({**record, **extra}))
        for url in ("https://admin-inc.jiemian.com/", "https://papi.jiemian.com/page/api/kuaixun/checkWhiteList",
                    "https://www.jiemian.com/article/123.html", "http://www.jiemian.com/lists/4.html"):
            self.assertFalse(jiemian.allowed_url(url))
        self.assertIsNone(jiemian.normalize_record({**record, "publishtime": "19:18"}))

    def test_collector_uses_observed_pagination_and_skips_restricted_old_items(self):
        now = datetime.now(CHINA).replace(microsecond=0)
        initial = document(card("123", now - timedelta(minutes=2)),
                           card("124", now - timedelta(minutes=3), extra='data-is-vip="1"'))
        batch = {"code": "0", "result": {"list": [
            {"id": "125", "title": "普通公开汽车快报", "publishtime": int((now - timedelta(hours=1)).timestamp())},
            {"id": "126", "title": "旧闻", "publishtime": int((now - timedelta(hours=25)).timestamp())}]}}
        with mock.patch.object(jiemian, "read_public", side_effect=[initial, json.dumps(batch)]) as read, \
             mock.patch.object(jiemian.time, "sleep"):
            result = jiemian.fetch_jiemian(now, 24)
        self.assertEqual({row["id"] for row in result["items"]}, {"123", "125"})
        self.assertTrue(result["window_complete"])
        self.assertTrue(result["public_only"])
        self.assertEqual(result["skipped_restricted"], 1)
        self.assertEqual(result["pages_fetched"], 2)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args_list[1].args[0]).query)
        self.assertEqual(query["cid"], ["1323kb"])
        self.assertEqual(query["tagid"], ["1323"])
        self.assertEqual(query["page"], ["2"])
        self.assertEqual(query["start_time"], [str(int((now - timedelta(minutes=3)).timestamp()))])

    def test_empty_failure_and_nonadvancing_cursor_never_claim_full_window(self):
        now = datetime.now(CHINA).replace(microsecond=0)
        first = document(card("123", now - timedelta(minutes=1)))
        repeated = json.dumps({"code": "0", "result": {"list": [
            {"id": "123", "title": "重复快报", "publishtime": int((now - timedelta(minutes=1)).timestamp())}]}})
        for later in (RuntimeError("offline"), json.dumps({"code": "0", "result": {"list": []}}), repeated,
                      json.dumps({"code": "403", "message": "login required"})):
            with mock.patch.object(jiemian, "read_public", side_effect=[first, later]), mock.patch.object(jiemian.time, "sleep"):
                result = jiemian.fetch_jiemian(now, 24)
            self.assertEqual(len(result["items"]), 1)
            self.assertFalse(result["window_complete"])
            self.assertTrue(result["errors"])
        with mock.patch.object(jiemian, "read_public", side_effect=RuntimeError("403")) as read:
            self.assertFalse(jiemian.fetch_jiemian(now, 24)["ok"])
            self.assertEqual(read.call_count, 1)

    def test_missing_pagination_parameters_stop_without_guessing_endpoint(self):
        now = datetime.now(CHINA)
        with mock.patch.object(jiemian, "read_public", return_value=card("123", now)) as read:
            result = jiemian.fetch_jiemian(now, 24)
        self.assertEqual(read.call_count, 1)
        self.assertFalse(result["window_complete"])
        self.assertTrue(result["errors"])

    def test_source_tab_persists_empty_and_source_page_uses_newer_native_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(curated, "load_hotlist_items", return_value=[]), mock.patch.object(curated, "load_weibo_items", return_value=[]):
                curated.render_source_hotlists(root, "2026-10-16")
            page = (root / "hotlists" / "index.html").read_text()
            self.assertIn('data-source="jiemian-flash">界面新闻·快报 <span>0</span>', page)
            self.assertIn("暂无可用公开缓存", page)
            now = datetime.now(CHINA).replace(microsecond=0)
            write_json(root / "source-pool" / "jiemian-flash.json", {
                "public_only": True, "fetched_at": now.isoformat(), "window_complete": True, "items": [
                    {"id": "123", "title": "公开国产芯片进展", "published_at": now.isoformat(), "url": "https://www.jiemian.com/article/123.html"},
                    {"id": "124", "title": "会员独享不能进入页面", "published_at": now.isoformat(), "is_vip": True}]})
            with mock.patch.object(curated, "load_hotlist_items", return_value=[]), mock.patch.object(curated, "load_weibo_items", return_value=[]):
                curated.render_source_hotlists(root, "2026-10-16")
            page = (root / "hotlists" / "index.html").read_text()
            section = re.search(r'data-source-section="jiemian-flash">(.*?)</section>', page, re.S)[1]
            self.assertIn("公开国产芯片进展", section)
            self.assertNotIn("会员独享不能进入页面", page)
            self.assertIn('界面新闻·快报 <span>1</span>', page)
            self.assertIn("发布 " + now.strftime("%m-%d %H:%M"), section)
            self.assertIn("最近24小时窗口已覆盖", section)
            self.assertIn(jiemian.LIST_URL, section)

    def test_shared_pool_enters_categories_and_merges_cross_source_event(self):
        now = datetime.now(CHINA).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            root.mkdir()
            write_json(root / "source-pool" / "jiemian-flash.json", {"fetched_at": now.isoformat(), "public_only": True, "items": [
                {"id": "123", "title": "英伟达发布新一代AI芯片", "published_at": now.isoformat(), "url": "https://www.jiemian.com/article/123.html"},
                {"id": "124", "title": "旧芯片新闻", "published_at": (now - timedelta(hours=25)).isoformat()}]})
            write_json(root / "source-pool" / "cls-telegraph.json", {"fetched_at": now.isoformat(), "items": [
                {"id": 125, "title": "英伟达发布新一代AI芯片", "ctime": int(now.timestamp()), "url": "https://www.cls.cn/detail/125"}]})
            curated.generate(root, 2, 24, 50, current_pages_only=True, event_db=Path(directory) / "test-registry.sqlite3")
            events = json.loads((root / "source-pool" / "home-events.json").read_text())["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["category"], "hardtech")
            self.assertEqual(events[0]["source_count"], 2)
            self.assertEqual({report["source_id"] for report in events[0]["reports"]}, {"cls-telegraph", "jiemian-flash"})

    def test_failed_targeted_refresh_preserves_snapshot_and_other_feed_statuses(self):
        now = datetime.now(CHINA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source-pool" / "jiemian-flash.json"
            write_json(path, {"public_only": True, "fetched_at": now.isoformat(), "items": [{"id": "123"}]})
            write_json(root / "source-pool" / "status.json", {"sources": [{"source_id": "jin10-all", "ok": True}]})
            before = path.read_bytes()
            with mock.patch.object(pool, "fetch_jiemian", side_effect=RuntimeError("offline")), mock.patch.object(pool, "fetch_jin10") as jin10:
                result = pool.refresh(root, force=True, config={"native_feeds": ["jiemian-flash"]})
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(next(row for row in result["sources"] if row["source_id"] == "jiemian-flash")["ok"])
            jin10.assert_not_called()
            self.assertEqual({row["source_id"] for row in result["sources"]}, {"jiemian-flash", "jin10-all"})
            self.assertFalse(result["wechat_collection_enabled"])


if __name__ == "__main__":
    unittest.main()
