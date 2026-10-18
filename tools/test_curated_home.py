"""Regression coverage for the local refresh replacing the classified homepage."""
from datetime import datetime
from pathlib import Path
import sys
import re
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_events as curated


def sample_item(title):
    at = datetime.now(curated.CHINA).replace(tzinfo=None, microsecond=0)
    return curated.RawItem(
        date=at.strftime("%Y-%m-%d"), title=title, url="https://example.com/news/" + title,
        source_id="thepaper", source_name="澎湃新闻", source_tier="权威媒体",
        source_type="综合新闻", source_weight=16, source_trust=82,
        kind="hotlist", first_dt=at, last_dt=at, best_rank=1, score=80,
        published_dt=at, publication_source="publisher:meta:article:published_time", collected_dt=at,
    )


class ClassifiedHomeTests(unittest.TestCase):
    def test_current_page_refresh_replaces_raw_home_but_preserves_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            root.mkdir()
            (root / "index.html").write_text("<title>热点新闻分析</title>")
            for folder in ("daily", "timeline"):
                (root / folder).mkdir()
                (root / folder / "index.html").write_text("keep-existing-archive")
            (root / "daily" / "2026-01-01.html").write_text("keep-existing-archive")
            items = [sample_item("英伟达发布人工智能芯片"), sample_item("黄金期货价格上涨")]
            with mock.patch.object(curated, "recent_db_dates", return_value=[items[0].date]), \
                 mock.patch.object(curated, "load_hotlist_items", return_value=items), \
                 mock.patch.object(curated, "load_weibo_items", return_value=[]), \
                 mock.patch.object(curated, "load_jin10_items", return_value=[]), \
                 mock.patch.object(curated, "load_items") as history:
                curated.generate(root, 1, 24, 50, current_pages_only=True, event_db=root.parent / "test-registry.sqlite3")
            page = (root / "index.html").read_text()
            self.assertIn("<title>今日领域分类</title>", page)
            self.assertNotIn("<title>热点新闻分析</title>", page)
            self.assertIn('data-category-section="hardtech"', page)
            self.assertIn('data-category-section="commodities"', page)
            self.assertIn('data-category-section="finance"', page)
            self.assertNotIn('data-category-section="international"', page)
            self.assertIn('id="home-item-count">2</strong>', page)
            self.assertNotIn('id="home-freshness-note"', page)
            self.assertNotIn('id="home-source-coverage"', page)
            self.assertNotIn("分类口径", page)
            self.assertNotIn("来源里有就进入候选", page)
            self.assertNotIn("页面会自动隐藏过期内容", page)
            self.assertIn('id="home-refresh-button"', page)
            self.assertIn('id="home-refresh-panel"', page)
            self.assertIn('id="home-source-counts"', page)
            self.assertIn('<h3>来源数量</h3>', page)
            self.assertTrue((root / "freshness" / "report.json").exists())
            self.assertEqual(page, (root / "curated" / "index.html").read_text())
            self.assertIn("<title>来源热榜</title>", (root / "hotlists" / "index.html").read_text())
            self.assertTrue((root / "hotlists" / "weibo" / "index.html").exists())
            history.assert_not_called()
            self.assertIn("新闻日报 Top 50", (root / "daily" / "index.html").read_text())
            self.assertIn('class="archive"', (root / "daily" / "index.html").read_text())
            self.assertIn('content="0;url=/daily/"', (root / "timeline" / "index.html").read_text())
            self.assertEqual((root / "daily" / "2026-01-01.html").read_text(), "keep-existing-archive")

    def test_refresh_script_does_not_allow_raw_crawler_to_overwrite_home(self):
        script = (Path(__file__).parent / "refresh_local_hotlists.sh").read_text()
        runner = (Path(__file__).parent / "refresh_runner.py").read_text()
        self.assertIn("refresh_service.py", script)
        self.assertIn("--enqueue", script)
        self.assertIn("STORAGE_HTML_ENABLED=false", runner)
        self.assertIn("--current-pages-only", runner)
        self.assertNotIn("--hotlists-only", runner)

    def test_foreign_and_military_events_outside_domains_do_not_enter_home(self):
        titles = ["广西大学通报学生高坠死亡", "加拿大军事基地发生爆炸", "美国纽约地铁站枪击事件"]
        events = curated.cluster_home_events([sample_item(title) for title in titles])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curated.render_home(events, events[0].items[0].date, root, 24)
            page = (root / "index.html").read_text()
        society = re.search(r'<section class="category-section" data-category-section="society">(.*?)</section>', page, re.S)[1]
        all_events = re.search(r'<section class="category-section" data-category-section="all">(.*?)</section>', page, re.S)[1]
        self.assertIn(titles[0], society)
        for title in titles[1:]:
            self.assertNotIn(title, society)
            self.assertNotIn(title, all_events)
        self.assertNotIn('data-category-section="other"', page)


if __name__ == "__main__":
    unittest.main()
