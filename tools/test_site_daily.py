"""Website daily shares home rules/identities, but excludes society and delivery state."""
from dataclasses import replace
from datetime import datetime, timedelta
import html
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

import curated_events as c
from test_curated_home import sample_item
import site_daily_archive as archive


class SiteDailyTests(unittest.TestCase):
    def test_seven_sectors_only_with_no_hotlist_requirement(self):
        titles = {
            "英伟达发布新一代AI芯片": "hardtech",
            "黄金期货价格上涨": "commodities",
            "泡泡玛特公布三季度财报": "consumer",
            "特斯拉发布新款汽车": "auto",
            "宁德时代发布固态电池": "newenergy",
            "全球芯片LOF发布停牌公告": "finance",
            "顺丰快递公布三季度财报": "logistics",
        }
        excluded = ["广西大学通报学生高坠死亡", "加拿大军事基地发生爆炸", "美国总统宣布军事打击"]
        events = c.cluster_home_events([replace(sample_item(t), kind="weibo", best_rank=None)
                                      for t in [*titles, *excluded]])
        chosen = c.select_site_daily(events)
        self.assertEqual(len(chosen), 7)
        self.assertEqual({event.category for event in chosen}, set(c.DAILY_CATEGORY_ORDER))
        self.assertEqual({event.title: event.category for event in chosen}, titles)

    def test_score_order_top_fifty_not_recrawl_time(self):
        at = datetime.now(c.CHINA)
        events = [c.Event(key=f"event-{n:03}", category="finance", score=n,
                          items=[replace(sample_item("银行发布利率公告"), published_dt=at)],
                          progresses=[c.HomeProgress(f"progress-{n}", f"event-{n:03}", "利率公告", "report",
                                                     at - timedelta(minutes=n), [], [])])
                  for n in range(60)]
        # Isolate sorting: actual visibility/score recomputation has separate tests.
        with mock.patch.object(c, "current_event_view", side_effect=lambda event, cutoff: event):
            chosen = c.select_site_daily(events)
        self.assertEqual([event.score for event in chosen], list(range(59, 9, -1)))
        self.assertEqual(len(chosen), 50)
        with mock.patch.object(c, "current_event_view", side_effect=lambda event, cutoff: event):
            self.assertEqual(len(c.select_site_daily(events, 100)), 50)
        events[0].score = events[1].score = 100
        events[0].items[0].last_dt = at + timedelta(days=5)
        events[0].progresses[0].first_reported_at = at - timedelta(hours=2)
        events[1].progresses[0].first_reported_at = at - timedelta(hours=1)
        with mock.patch.object(c, "current_event_view", side_effect=lambda event, cutoff: event):
            self.assertEqual(c.select_site_daily(events, 1)[0].key, events[1].key)
            self.assertEqual(c.select_site_daily(events, 0), [])

    def test_publication_cutoff_not_collection_and_unverified_times_excluded(self):
        old = replace(sample_item("黄金期货价格上涨"), published_dt=datetime.now(c.CHINA)-timedelta(hours=25))
        unverified = replace(sample_item("银行利率下调"), publication_source="")
        missing = replace(sample_item("特斯拉发布新款汽车"), published_dt=None)
        chosen = c.select_site_daily(c.cluster_home_events([old, unverified, missing]))
        self.assertEqual(chosen, [])

    def test_reuses_registry_event_progress_doc_ids_and_keeps_new_progress(self):
        a = sample_item("Swisse抗糖丸被检出铅超标")
        b = replace(a, title="Swisse抗糖丸铅含量超标", url="https://example.com/2")
        reply = replace(a, title="Swisse客服回应抗糖丸铅超标：相关批次未进入中国销售", url="https://example.com/3")
        events = c.cluster_home_events([a, b, reply])
        chosen = c.select_site_daily(events)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(len(chosen[0].progresses), 2)
        self.assertEqual(chosen[0].key, events[0].key)
        self.assertEqual({p.key for p in chosen[0].progresses}, {p.key for p in events[0].progresses})
        self.assertEqual({i.doc_id for i in chosen[0].items}, {i.doc_id for i in events[0].items})
        self.assertEqual(len([p for p in chosen[0].progresses if p.kind == "report"]), 1)

    def test_render_empty_and_normal_does_not_touch_feishu_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "briefing" / "sent_history.json"
            history.parent.mkdir()
            history.write_text("original history")
            c.render_site_daily([], root)
            self.assertIn('id="daily-empty" role="status"', (root / "daily/index.html").read_text())
            events = c.cluster_home_events([sample_item("全球芯片LOF发布停牌公告")])
            c.render_site_daily(events, root)
            page = (root / "daily/index.html").read_text()
            self.assertNotIn('href="/timeline/"', page)
            self.assertNotIn('data-category="society"', page)
            self.assertEqual(len(re.findall(r'<button[^>]+data-category=', page)), 0)
            self.assertEqual(len(re.findall(r'<article class="item"', page)), 1)
            self.assertIn('class="archive"', page)
            self.assertNotIn('class="classified-row"', page)
            self.assertNotIn('data-expires-at', page)
            self.assertIn(f'data-event-id="{events[0].key}"', page)
            self.assertIn("全球芯片LOF", (root / "daily/latest.md").read_text())
            self.assertEqual(history.read_text(), "original history")

    def test_expiry_states_cannot_reintroduce_society(self):
        a = sample_item("英伟达发布新一代AI芯片")
        b = replace(a, published_dt=a.published_dt + timedelta(seconds=1), url="https://example.com/2")
        event = c.cluster_home_events([a, b])[0]
        future = c.current_event_view(event, c.china_time(a.published_dt) + timedelta(microseconds=1))
        future.category = "society"
        with mock.patch.object(c, "current_event_view", return_value=future):
            markup = c.home_event_row(event, 1, allowed_categories=c.DAILY_CATEGORY_ORDER)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["html"], "")

    def test_full_and_current_generation_never_use_legacy_daily_or_timeline(self):
        for current_only in (False, True):
            with self.subTest(current_only=current_only), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "public"
                root.mkdir()
                item = sample_item("全球芯片LOF发布停牌公告")
                with mock.patch.object(c, "recent_db_dates", return_value=[item.date]), \
                     mock.patch.object(c, "load_hotlist_items", return_value=[item]), \
                     mock.patch.object(c, "load_rss_items", return_value=[]), \
                     mock.patch.object(c, "load_weibo_items", return_value=[]), \
                     mock.patch.object(c, "load_native_pool_items", return_value=([], [])), \
                     mock.patch.object(c, "render_daily", side_effect=AssertionError("legacy daily")), \
                     mock.patch.object(c, "render_timeline", side_effect=AssertionError("legacy timeline")), \
                     mock.patch.object(c, "load_items", side_effect=AssertionError("legacy taxonomy")):
                    c.generate(root, 2, 24, 50, current_pages_only=current_only, cached_only=True)
                page = (root / "daily/index.html").read_text()
                self.assertIn("新闻日报 Top 50", page)
                self.assertIn('data-category="finance"', page)
                self.assertNotIn('href="/timeline/"', (root / "index.html").read_text())
                self.assertIn('content="0;url=/daily/"', (root / "timeline/index.html").read_text())

    def test_workflow_does_not_restore_pause_and_all_current_navs_remove_timeline(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/cloudflare-pages.yml").read_text()
        self.assertNotIn("daily_pause.py", workflow)
        self.assertNotIn("--skip-daily", workflow)
        import cls_telegraph
        self.assertNotIn("/timeline/", cls_telegraph.nav_html())
        for source in ("daily_digest.py", "daily_pause.py", "inject_daily_link.py"):
            self.assertNotIn('href="/timeline/"', (root / "tools" / source).read_text())

    def test_calendar_days_do_not_mix_yesterday_or_tomorrow(self):
        now = datetime.now(c.CHINA)
        yesterday = now - timedelta(days=1)
        old = replace(sample_item("黄金期货价格上涨"), published_dt=yesterday.replace(hour=0, minute=1))
        today = replace(sample_item("全球芯片LOF发布停牌公告"), published_dt=now)
        events = c.cluster_home_events([old, today])
        chosen = c.select_site_daily(events, now=now, date=yesterday.strftime('%Y-%m-%d'))
        self.assertEqual([event.title for event in chosen], [old.title])
        self.assertEqual([event.title for event in c.select_site_daily(events, now=now)], [today.title])

    def test_week_archive_keeps_saved_reports_across_empty_refresh_and_new_day(self):
        now = datetime.now(c.CHINA)
        yesterday = now - timedelta(days=1)
        events = c.cluster_home_events([sample_item("英伟达发布新一代AI芯片"),
            replace(sample_item("黄金期货价格上涨"), published_dt=yesterday)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'public'
            c.render_site_daily(events, root, now=now)
            dates = archive.week_dates(now)
            for date in dates:
                self.assertTrue((root / f'daily/{date}.html').exists())
                data = json.loads((root / f'daily/{date}.json').read_text())
                self.assertLessEqual(len(data['events']), 50)
                self.assertTrue(all(doc['published_at'].startswith(date) for event in data['events'] for doc in event['reports']))
            page = (root / 'daily/index.html').read_text()
            links = re.findall(r'class="archive-row[^"]*" href="/daily/([\d-]+).html"', page)
            self.assertEqual(links, dates)
            before = json.loads((root / f'daily/{dates[0]}.json').read_text())['events']
            c.render_site_daily([], root, now=now + timedelta(days=1))
            after = json.loads((root / f'daily/{dates[0]}.json').read_text())['events']
            self.assertEqual(before, after)
            self.assertNotIn('setInterval', (root / f'daily/{dates[0]}.html').read_text())
            self.assertIn('当天暂无已核实内容', (root / 'daily/index.html').read_text())

    def test_each_refresh_accumulates_documents_and_reuses_original_template_css(self):
        from daily_digest import render_html
        first = sample_item('全球芯片LOF发布停牌公告')
        second = replace(first, source_id='weibo-account:1638782947', url='https://m.weibo.cn/detail/2')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'public'
            registry = Path(directory) / 'private/events.sqlite3'
            a = c.cluster_home_events([first], registry_path=registry)
            c.render_site_daily(a, root, registry_path=registry)
            b = c.cluster_home_events([second], registry_path=registry)
            c.render_site_daily(b, root, registry_path=registry)
            date = datetime.now(c.CHINA).strftime('%Y-%m-%d')
            data = json.loads((root / f'daily/{date}.json').read_text())
            self.assertEqual(len(data['events']), 1)
            self.assertEqual(len(data['events'][0]['reports']), 2)
            self.assertEqual(a[0].key, data['events'][0]['id'])
            self.assertEqual(a[0].progresses[0].key, data['events'][0]['progresses'][0]['id'])
            page = (root / 'daily/index.html').read_text()
            old_css = re.search(r'<style>(.*?)</style>', render_html([], date, '', 50, []), re.S)[1]
            self.assertEqual(old_css, re.search(r'<style>(.*?)</style>', page, re.S)[1])

    def test_backfill_uses_verified_publication_cache_not_crawl_date(self):
        now = datetime.now(c.CHINA)
        previous = now - timedelta(days=2)
        first = replace(sample_item('黄金期货价格上涨'), published_dt=None, publication_source='')
        unknown = replace(first, title='银行利率下调', url='https://example.com/unverified')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'public'
            c.write_json(root / 'freshness/publication-times.json', {'schema_version':1, 'items': {
                first.url:{'published_at':previous.isoformat(), 'evidence':'publisher:meta:article:published_time'}}})
            with mock.patch.object(c, 'load_hotlist_items', return_value=[first,unknown]), \
                 mock.patch.object(c, 'load_rss_items', return_value=[]), \
                 mock.patch('urllib.request.urlopen', side_effect=AssertionError('no collection')):
                events = archive.backfill_events(root, Path(directory)/'private/events.sqlite3', now=now)
            self.assertEqual(len(events), 1)
            chosen = c.select_site_daily(events, now=now, date=previous.strftime('%Y-%m-%d'))
            self.assertEqual([e.title for e in chosen], [first.title])
            self.assertEqual(c.select_site_daily(events, now=now), [])


if __name__ == "__main__":
    unittest.main()
