from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import news_freshness as fresh
import curated_events as curated

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=fresh.CHINA)
URL = "https://wallstreetcn.com/articles/3783652"


def item(*, published=None, observed=NOW, url=URL, kind="hotlist", evidence="publisher:meta:article:published_time", score=80):
    at = fresh.china_time(observed).replace(tzinfo=None)
    pub = fresh.china_time(published).replace(tzinfo=None) if published else None
    return curated.RawItem(date=at.strftime("%Y-%m-%d"), title="英伟达发布人工智能芯片", url=url,
                           source_id="wallstreetcn-hot", source_name="华尔街见闻", source_tier="财经专业源",
                           source_type="财经市场", source_weight=17, source_trust=84, kind=kind,
                           first_dt=at, last_dt=at, collected_dt=at, published_dt=pub,
                           publication_source=evidence if pub else "", score=score)


class PublicationExtractionTests(unittest.TestCase):
    def test_publication_not_modification(self):
        document = '<meta property="article:modified_time" content="2026-10-18T15:00:00+08:00"><meta property="article:published_time" content="2026-10-15T06:31:00+08:00">'
        published, evidence = fresh.extract_publication(document)
        self.assertEqual(published.isoformat(), "2026-10-15T06:31:00+08:00")
        self.assertIn("published_time", evidence)
        self.assertEqual(fresh.extract_publication(document.split('<meta property="article:published_time"')[0]), (None, ""))

    def test_jsonld_requires_article_datepublished(self):
        document = '<script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "datePublished": "2026-10-17T23:00:00Z", "dateModified": NOW.isoformat()}) + '</script>'
        self.assertEqual(fresh.extract_publication(document)[0].isoformat(), "2026-10-18T07:00:00+08:00")
        wrong = document.replace('"NewsArticle"', '"WebSite"')
        self.assertEqual(fresh.extract_publication(wrong), (None, ""))

    def test_related_body_dates_cannot_supply_main_article_time(self):
        document = '<html><head></head><body><aside><meta itemprop="datePublished" content="2026-10-18T15:00:00+08:00"><time itemprop="datePublished" datetime="2026-10-18T15:00:00+08:00"></time></aside></body></html>'
        self.assertEqual(fresh.extract_publication(document), (None, ""))

    def test_wallstreetcn_reads_exact_main_article_not_related_articles(self):
        data = {"state": {"default": {"children": {"default": {"data": {"id": 3783652, "article": {"id": 3783652, "display_time": 1792017063}, "related": [{"display_time": int(NOW.timestamp())}]}}}}}}
        document = '<script>__SSR__ = ' + json.dumps(data) + ';</script>'
        self.assertEqual(fresh.extract_publication(document, URL)[0].isoformat(), "2026-10-15T06:31:03+08:00")
        self.assertEqual(fresh.extract_publication(document, URL.replace("3783652", "3783653")), (None, ""))

    def test_partial_dates_unknown_and_future_dates_not_guessed(self):
        self.assertIsNone(fresh.parse_publication_time("10/15 06:31"))
        self.assertIsNone(fresh.parse_publication_time("2026-10-18"))
        self.assertIsNone(fresh.parse_publication_time("just now"))
        self.assertEqual(fresh.parse_publication_time(1792017063000), fresh.parse_publication_time(1792017063))

    def test_only_supported_public_article_urls_can_be_fetched(self):
        self.assertTrue(fresh.safe_article_url(URL))
        for url in ("http://127.0.0.1:8789/", "https://wallstreetcn.com.evil.example/articles/1", "https://wallstreetcn.com:8443/articles/1", "https://user:password@wallstreetcn.com/articles/1", "https://weibo.com/hot/search", "http://wallstreetcn.com/articles/1"):
            self.assertFalse(fresh.safe_article_url(url), url)


class FreshnessFilterTests(unittest.TestCase):
    def select(self, items, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return fresh.select_home_items(items, Path(directory), now=NOW, max_fetches=0, **kwargs)

    def test_old_article_recently_recrawled_is_excluded(self):
        chosen, report = self.select([item(published=NOW - timedelta(days=3))])
        self.assertEqual(chosen, [])
        self.assertEqual(report["excluded_by_reason"], {"old_publication": 1})

    def test_unknown_time_is_not_substituted_with_capture_time(self):
        chosen, report = self.select([item()])
        self.assertFalse(chosen)
        self.assertEqual(report["excluded_by_reason"], {"unknown_publication": 1})

    def test_rolling_window_includes_yesterday_not_older_than_24h(self):
        chosen, report = self.select([item(published=NOW - timedelta(hours=23))])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].published_dt.date().isoformat(), "2026-10-17")

    def test_future_date_and_post_capture_date_are_excluded(self):
        _, report = self.select([item(published=NOW + timedelta(hours=1))])
        self.assertEqual(report["excluded_by_reason"], {"future_publication": 1})
        _, report = self.select([item(published=NOW, observed=NOW - timedelta(hours=2))])
        self.assertEqual(report["excluded_by_reason"], {"publication_after_capture": 1})

    def test_fresh_report_stays_eligible_even_after_leaving_hotlist(self):
        older = item(published=NOW - timedelta(hours=2), observed=NOW - timedelta(hours=1))
        newer = item(published=NOW - timedelta(hours=1), url=URL.replace("3783652", "3783653"))
        chosen, report = self.select([older, newer])
        self.assertEqual([value.url for value in chosen], [older.url, newer.url])
        self.assertEqual(report["excluded_by_reason"], {})

    def test_cached_unknown_snapshot_cannot_become_current(self):
        chosen, report = self.select([item(observed=NOW - timedelta(days=2))])
        self.assertFalse(chosen)
        self.assertEqual(report["excluded_by_reason"], {"unknown_publication": 1})
        self.assertTrue(report["platform_cache_stale"])

    def test_filter_happens_before_clustering_or_scoring(self):
        old = item(published=NOW - timedelta(days=3), score=999)
        current = replace(item(published=NOW - timedelta(hours=1), url="https://example.com/current", kind="weibo", score=20), source_id="weibo-account:1", source_name="微博·媒体")
        chosen, _ = self.select([old, current])
        event = curated.cluster_events(chosen)[0]
        self.assertEqual(event.source_count, 1)
        self.assertEqual(event.url, current.url)
        self.assertNotIn("新近", event.status)

    def test_positive_cache_keeps_original_time_and_fetch_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = NOW - timedelta(days=3)
            fresh.write_json(root / "freshness" / "publication-times.json", {"schema_version": 1, "items": {URL: {"published_at": old.isoformat(), "evidence": "publisher:meta:article:published_time", "checked_at": old.isoformat()}}})
            unknowns = [item(url=URL.replace("3783652", str(3783660 + index))) for index in range(5)]
            with mock.patch.object(fresh, "fetch_publication", return_value=(None, "")) as fetch:
                chosen, report = fresh.select_home_items([item()] + unknowns, root, now=NOW, max_fetches=2)
            self.assertFalse(chosen)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(report["excluded_by_reason"]["old_publication"], 1)
            self.assertEqual(fresh.load_cache(root / "freshness" / "publication-times.json")[URL]["published_at"], old.isoformat())

    def test_missing_provenance_and_corrupt_cache_fail_closed(self):
        self.assertFalse(self.select([item(published=NOW, evidence="")])[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh.write_json(root / "cache.json", {"schema_version": 1, "items": []})
            self.assertEqual(fresh.load_cache(root / "cache.json"), {})

    def test_weibo_reshared_old_original_or_missing_original_stays_off_home(self):
        from weibo_feed import save_json
        now = datetime.now(fresh.CHINA)
        timestamp = int(now.timestamp() * 1000)
        payload = {"accounts": [{"id": "1651428902"}], "fetched_at": now.isoformat(), "items": [
            {"account_id": "1651428902", "title": "旧闻今天转发", "url": "https://m.weibo.cn/detail/5000000000000001", "timestamp": timestamp, "is_retweet": True, "original_timestamp": int((now - timedelta(days=3)).timestamp() * 1000)},
            {"account_id": "1651428902", "title": "原文时间缺失的转发", "url": "https://m.weibo.cn/detail/5000000000000002", "timestamp": timestamp, "is_retweet": True, "original_timestamp": None},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_json(root / "weibo" / "latest.json", payload)
            loaded = curated.load_weibo_items(root)
            chosen, report = fresh.select_home_items(loaded, root, now=now, max_fetches=0)
            self.assertEqual(chosen, [])
            self.assertEqual(report["excluded_by_reason"], {"old_publication": 1, "unknown_publication": 1})


if __name__ == "__main__":
    unittest.main()
