"""Durable identities, incremental work, progress novelty and merge boundaries."""
from dataclasses import replace
from datetime import timedelta
import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

import curated_events as c
from event_registry import document_id
from test_home_sources import report


def doc(title, n, **kwargs):
    return replace(report(title, url=f"https://www.cls.cn/detail/{n}", **kwargs), summary="")


def mapping(events):
    return {item.doc_id: (event.key, tuple(sorted(p.key for p in event.progresses if item.doc_id in p.doc_ids)))
            for event in events for item in event.items}


class EventRegistryTests(unittest.TestCase):
    def test_registry_is_rejected_inside_published_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "public"
            with self.assertRaisesRegex(ValueError, "outside"):
                c.generate(root, 2, 24, 50, current_pages_only=True, event_db=root / "registry.sqlite3")

    def test_repeat_reverse_order_and_new_source_keep_all_existing_ids(self):
        a = doc("Swisse抗糖丸被检出铅超标", 1)
        b = replace(doc("Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售", 2),
                    published_dt=a.published_dt + timedelta(seconds=1))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite3"
            first = c.cluster_home_events([a, b], registry_path=path)
            stats = {}
            second = c.cluster_home_events([b, a], registry_path=path, stats=stats)
            self.assertEqual(mapping(first), mapping(second))
            self.assertEqual(stats["unchanged_docs"], 2)
            self.assertEqual(stats.get("event_pair_checks", 0), 0)
            third = c.cluster_home_events([a, b, doc(a.title, 3, source="weibo-account:1638782947")], registry_path=path)
            for key, value in mapping(first).items():
                self.assertEqual(mapping(third)[key], value)

    def test_same_progress_collapses_but_different_progress_survives(self):
        a = doc("Swisse抗糖丸被检出铅超标", 1)
        b = doc("Swisse抗糖丸铅含量超标", 2)
        response = doc("Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售", 3)
        withdrawal = replace(doc("Swisse抗糖丸下架", 4), summary="Swisse抗糖丸因铅超标问题下架。")
        events = c.cluster_home_events([a, b, response, withdrawal])
        self.assertEqual(len(events), 1)
        self.assertEqual({p.kind for p in events[0].progresses}, {"report", "response", "withdrawal"})
        self.assertEqual(len([p for p in events[0].progresses if p.kind == "report"]), 1)
        # One article explicitly discloses both facts, without duplicating docid.
        self.assertEqual(len(mapping(events)[document_id(response)][1]), 2)
        self.assertEqual(len({item.doc_id for item in events[0].items}), 4)

    def test_distinct_response_claims_are_not_deduplicated(self):
        a = doc("Swisse客服回应抗糖丸铅超标：相关批次未进入中国销售", 1)
        b = doc("Swisse客服回应抗糖丸铅超标：承认已在中国销售", 2)
        events = c.cluster_home_events([a, b])
        self.assertEqual(len(events), 1)
        self.assertEqual(len([p for p in events[0].progresses if p.kind == "response"]), 2)

    def test_updated_article_has_one_docid_and_preserves_new_progress(self):
        original = doc("Swisse抗糖丸被检出铅超标", 1)
        revised = replace(original, title="Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite3"
            first = c.cluster_home_events([original], registry_path=path)[0]
            later = c.cluster_home_events([revised], registry_path=path)[0]
            self.assertEqual(first.key, later.key)
            self.assertEqual(first.items[0].doc_id, later.items[0].doc_id)
            self.assertIn(first.progresses[0].key, {p.key for p in later.progresses})
            self.assertEqual(len(later.progresses), 2)

    def test_number_formatting_is_not_new_buyback_progress(self):
        values = ["海光信息：董事长提议回购5亿至8亿元股份",
                  "海光信息：董事长提议回购5亿元-8亿元公司股份",
                  "海光信息：董事长提议拟以人民币5亿元-人民币8亿元回购公司股份"]
        events = c.cluster_home_events([doc(title, i) for i, title in enumerate(values)])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].progresses), 1)

    def test_changed_amount_and_approval_are_new_progresses(self):
        events = c.cluster_home_events([
            doc("海光信息：董事长提议回购5亿元-8亿元公司股份", 1),
            doc("海光信息：董事会审议通过回购5亿元-8亿元公司股份", 2),
            doc("海光信息：董事长提议回购6亿元-9亿元公司股份", 3)])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].progresses), 3)

    def test_different_products_and_quality_problems_keep_separate_events(self):
        events = c.cluster_home_events([doc("Swisse抗糖丸被检出铅超标", 1),
                                       doc("Swisse褪黑素被检出铅超标", 2),
                                       doc("Swisse抗糖丸被检出汞超标", 3)])
        self.assertEqual(len(events), 3)

    def test_different_entities_and_counterparties_keep_separate_events(self):
        pairs = [
            ("苹果发布新款智能手机", "华为发布新款智能手机"),
            ("微软取消与Nscale关于英国数据中心的合作", "微软取消与Oracle关于英国数据中心的合作"),
            ("上海甲歌剧院开幕首秀", "上海乙歌剧院开幕首秀"),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertEqual(len(c.cluster_home_events([doc(a, 1), doc(b, 2)])), 2)

    def test_shared_promotional_hashtag_is_not_an_event_key(self):
        a = replace(doc("甲公司宣布推出新款手机", 1), summary="#2026全球领导者大会##数字产业新未来# 甲公司推出新款手机。")
        b = replace(doc("乙公司完成新一轮融资", 2), summary="#2026全球领导者大会##数字产业新未来# 乙公司完成融资。")
        self.assertEqual(len(c.cluster_home_events([a, b])), 2)

    def test_own_website_caption_supports_opening_not_a_second_progress(self):
        a = replace(doc("上海大歌剧院开幕首秀【扇开第一声，是上海的声音】", 1), source_id="weibo-account:5044281310")
        b = replace(doc("扇开第一声，是上海的声音", 2), source_id="thepaper", published_dt=a.published_dt + timedelta(seconds=1))
        events = c.cluster_home_events([a, b])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].progresses), 1)
        self.assertIn("开幕", events[0].title)

    def test_caption_arriving_first_is_enriched_with_same_progress_id(self):
        a = replace(doc("扇开第一声，是上海的声音", 1), source_id="thepaper")
        b = replace(doc("上海大歌剧院开幕首秀【扇开第一声，是上海的声音】", 2),
                    source_id="weibo-account:5044281310", published_dt=a.published_dt + timedelta(seconds=1))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite3"
            first = c.cluster_home_events([a], registry_path=path)[0]
            later = c.cluster_home_events([a, b], registry_path=path)[0]
            self.assertEqual(first.key, later.key)
            self.assertEqual(len(later.progresses), 1)
            self.assertEqual(first.progresses[0].key, later.progresses[0].key)
            self.assertEqual(later.progresses[0].kind, "opening")

    def test_full_weibo_hashtags_and_body_do_not_split_same_event(self):
        a = doc("Swisse抗糖丸被检出铅超标", 1)
        b = replace(doc("Swisse客服回应抗糖丸铅超标Swisse客服称铅超标产品未进入中国销售【Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售】10月18日消息，近日，海关总署进出口食品安全局发布通报称…", 2),
                    summary="#Swisse客服回应抗糖丸铅超标#【Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售】")
        self.assertEqual(len(c.cluster_home_events([a, b])), 1)

    def test_termination_paraphrases_are_one_progress(self):
        values = ["微软据悉已取消与Nscale关于英国数据中心的相关交易",
                  "微软退出英国数据中心项目，Nscale上市计划承压",
                  "微软取消与英国数据中心合作 【微软取消AI相关合作，英国本土数据中心先锋企业遭遇重大挫折】微软已退出一笔位于埃塞克斯的数据中心交易，对英国企业Nscale上市计划构成打击。"]
        events = c.cluster_home_events([doc(title, i) for i, title in enumerate(values)])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].progresses), 1)

    def test_old_source_expiry_does_not_change_any_identity(self):
        a = doc("Swisse抗糖丸被检出铅超标", 1)
        b = replace(doc(a.title, 2), published_dt=a.published_dt + timedelta(seconds=1))
        event = c.cluster_home_events([a, b])[0]
        view = c.current_event_view(event, c.china_time(a.published_dt) + timedelta(microseconds=1))
        self.assertEqual(event.key, view.key)
        self.assertEqual(event.progresses[0].key, view.progresses[0].key)
        self.assertEqual(event.progresses[0].first_reported_at, view.progresses[0].first_reported_at)
        self.assertEqual(len(view.items), 1)

    def test_html_preserves_three_levels_and_unique_progress_ids(self):
        event = c.cluster_home_events([doc("Swisse抗糖丸被检出铅超标", 1),
            doc("Swisse抗糖丸被检出铅超标，官方客服：相关批次未进入中国销售", 2)])[0]
        markup = c.home_event_row(event, 1)
        self.assertIn(f'data-event-id="{event.key}"', markup)
        self.assertEqual(len(re.findall(r'data-progress-id="', markup)), 2)
        self.assertIn('data-doc-id="doc:', markup)
        self.assertIn("2 个进展", markup)
        states = json.loads(html.unescape(re.search(r'data-future-states="([^"]*)"', markup)[1]))
        for state in states:
            self.assertIn(event.key, state["html"])

    def test_failure_rolls_back_partial_assignment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite3"
            a = doc("Swisse抗糖丸被检出铅超标", 1)
            c.cluster_home_events([a], registry_path=path)
            from unittest.mock import patch
            with patch("event_registry.statements", side_effect=RuntimeError("failed")):
                with self.assertRaises(RuntimeError):
                    c.cluster_home_events([doc("微软取消英国数据中心合作", 2)], registry_path=path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 1)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.close()


if __name__ == "__main__":
    unittest.main()
