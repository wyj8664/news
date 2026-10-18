import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weibo_feed
from curated_events import cluster_events, load_weibo_items
from weibo_page import render_weibo_page

CHINA = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=CHINA)
USERS = [{"id": "1651428902", "screen_name": "21世纪经济报道", "last_fetched_at": int(NOW.timestamp() * 1000)},
         {"id": "1638782947", "screen_name": "新浪财经", "last_fetched_at": int(NOW.timestamp() * 1000)}]


def post(mid="5000000000000001", uid="1651428902", *, at=NOW, text="【#英伟达发布AI芯片#】新一代芯片正式发布。"):
    return {"id": mid, "user_id": uid, "user_name": "测试媒体", "text": text,
            "created_at_ts": int(at.timestamp() * 1000), "likes_count": 3}


class WeiboFeedTests(unittest.TestCase):
    def test_only_followed_accounts_within_publication_window(self):
        posts = [post(), post("5000000000000002", "9999999999"),
                 post("5000000000000003", at=NOW - timedelta(hours=25)),
                 post("5000000000000004", at=NOW + timedelta(hours=1))]
        data = weibo_feed.normalize_payload(posts, USERS, {}, now=NOW)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["published_at"], "2026-10-18T15:00:00+08:00")
        self.assertEqual(data["items"][0]["title"], "英伟达发布AI芯片")

    def test_deduplicate_ids_but_keep_two_accounts_covering_same_event(self):
        first = post()
        second = post("5000000000000002", "1638782947")
        data = weibo_feed.normalize_payload([first, first, second], USERS, {}, now=NOW)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len({item["source_id"] for item in data["items"]}), 2)

    def test_new_account_batch_is_exported_without_replacing_original_accounts(self):
        added = [("1646051850", "贝壳财经"), ("1650111241", "中国经营报"), ("1641561812", "经济观察报"),
                 ("5044281310", "澎湃新闻"), ("1664176597", "证券时报"), ("1926909715", "第一财经日报")]
        users = USERS + [{"id": uid, "screen_name": name, "last_fetched_at": int(NOW.timestamp() * 1000)} for uid, name in added]
        posts = [post(str(5000000000000001 + index), user["id"]) for index, user in enumerate(users)]
        data = weibo_feed.normalize_payload(posts, users, {}, now=NOW)
        self.assertEqual({account["id"] for account in data["accounts"]}, {user["id"] for user in users})
        self.assertEqual(data["count"], len(users))
        self.assertTrue(all(account["count"] == 1 for account in data["accounts"]))

    def test_auth_and_raw_extra_fields_never_enter_public_payload(self):
        raw = post()
        raw.update({"cookie": "secret-cookie-value", "api_token": "secret-token-value"})
        data = weibo_feed.normalize_payload([raw], USERS, {}, now=NOW)
        serialized = json.dumps(data)
        self.assertNotIn("secret-cookie-value", serialized)
        self.assertNotIn("secret-token-value", serialized)
        self.assertEqual(data["items"][0]["url"], "https://m.weibo.cn/detail/5000000000000001")

    def test_china_auto_news_is_added_without_forcing_all_posts_into_auto(self):
        from curated_events import cluster_home_events
        from news_event_clustering import publisher_id, publisher_name
        now = datetime.now(CHINA)
        uid = "2011593450"
        users = USERS + [{"id": uid, "screen_name": "中国汽车报", "last_fetched_at": int(now.timestamp() * 1000)}]
        posts = [post(uid=uid, at=now, text="【比亚迪发布新款汽车】汽车产业新产品。"),
                 post("5000000000000002", uid, at=now, text="【英伟达发布AI芯片】新一代人工智能芯片。"),
                 post("5000000000000003", uid, at=now - timedelta(hours=25))]
        for item in posts:
            item["user_name"] = "中国汽车报"
        data = weibo_feed.normalize_payload(posts, users, {}, now=now)
        self.assertEqual({account["id"] for account in data["accounts"]}, {user["id"] for user in users})
        self.assertEqual(data["count"], 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weibo_feed.save_json(root / "weibo/latest.json", data)
            items = load_weibo_items(root)
            self.assertTrue(all(publisher_id(item) == "cnautonews" and publisher_name(item) == "中国汽车报" for item in items))
            self.assertEqual({event.category for event in cluster_home_events(items)}, {"auto", "hardtech"})

    def test_sync_time_does_not_become_publication_or_export_time(self):
        old_users = [{**USERS[0], "last_fetched_at": int((NOW - timedelta(hours=3)).timestamp() * 1000)}]
        data = weibo_feed.normalize_payload([post(at=NOW - timedelta(hours=1))], old_users, {}, now=NOW)
        self.assertTrue(data["stale"])
        self.assertNotEqual(data["fetched_at"], data["generated_at"])
        self.assertEqual(data["items"][0]["published_at"], "2026-10-18T14:00:00+08:00")

    def test_new_three_publishers_are_in_roster_exported_and_classified_by_content(self):
        from curated_events import cluster_home_events
        from news_event_clustering import publisher_id, publisher_name
        expected={'7399555658':'红星资本局','1684012053':'财经','7209515062':'科创板日报'}
        roster_path=Path(__file__).resolve().parents[2]/'weiboX-server/local/accounts.json'
        if roster_path.exists():  # Deployment roster is not bundled in standalone CI checkouts.
            roster=json.loads(roster_path.read_text())
            self.assertTrue(set(expected)<={user['id'] for user in roster})
            self.assertEqual(len({user['id'] for user in roster}),len(roster))
        now=datetime.now(CHINA)
        new_users=[{'id':uid,'screen_name':name,'last_fetched_at':int(now.timestamp()*1000)} for uid,name in expected.items()]
        titles=['【英伟达发布AI芯片】新一代人工智能芯片。','【女研究员老鼠仓被罚款】证券监管处罚。','【瑞幸咖啡新店开业】消费品牌经营。']
        posts=[post(str(5000000000000100+i),uid,at=now,text=titles[i]) for i,uid in enumerate(expected)]
        for item,user in zip(posts,new_users): item['user_name']=user['screen_name']
        data=weibo_feed.normalize_payload(posts,USERS+new_users,{},now=now)
        self.assertTrue(set(expected)<={user['id'] for user in data['accounts']})
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            weibo_feed.save_json(root/'weibo/latest.json',data)
            items=load_weibo_items(root)
            self.assertEqual({publisher_id(item) for item in items},{'redstar-capital','caijing','star-daily'})
            self.assertEqual({publisher_name(item) for item in items},set(expected.values()))
            self.assertEqual({event.category for event in cluster_home_events(items)},{'hardtech','finance','consumer'})

    def test_reshare_does_not_reset_original_post_publication(self):
        raw = post()
        raw.update(is_retweet=True, retweet={"created_at_ts": int((NOW - timedelta(days=3)).timestamp() * 1000), "text": "原始报道"})
        data = weibo_feed.normalize_payload([raw], USERS, {}, now=NOW)
        self.assertEqual(data["items"][0]["original_timestamp"], int((NOW - timedelta(days=3)).timestamp() * 1000))
        self.assertEqual(data["items"][0]["published_at"], NOW.isoformat())
        raw["retweet"] = None
        missing = weibo_feed.normalize_payload([raw], USERS, {}, now=NOW)
        self.assertIsNone(missing["items"][0]["original_timestamp"])

    def test_source_failure_preserves_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "weibo" / "latest.json"
            weibo_feed.save_json(existing, {"ok": True, "items": ["previous"]})
            before = existing.read_bytes()
            with mock.patch.object(sys, "argv", ["weibo_feed", "--output-root", directory, "--allow-stale"]), \
                 mock.patch.object(weibo_feed, "collect_local", side_effect=RuntimeError("offline")):
                weibo_feed.main()
            self.assertEqual(existing.read_bytes(), before)

    def test_posts_join_existing_event_clustering_without_fake_platform_rank(self):
        now = datetime.now(CHINA)
        data = weibo_feed.normalize_payload([post(at=now), post("5000000000000002", "1638782947", at=now)], USERS, {}, now=now)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weibo_feed.save_json(root / "weibo" / "latest.json", data)
            items = load_weibo_items(root)
            self.assertEqual(len(items), 2)
            self.assertTrue(all(item.best_rank is None and item.kind == "weibo" for item in items))
            events = cluster_events(items)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].source_count, 2)
            self.assertEqual(events[0].category, "tech")

    def test_inline_snapshot_cannot_close_its_script_tag(self):
        raw = post(text="【测试】</script><script>alert(1)</script>")
        data = weibo_feed.normalize_payload([raw], USERS, {}, now=NOW)
        # Also defend against unexpected public snapshot values, independent of normalization.
        data["items"][0]["summary"] = "</script><script>alert(1)</script>"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weibo_feed.save_json(root / "weibo" / "latest.json", data)
            render_weibo_page(root)
            page = (root / "hotlists" / "weibo" / "index.html").read_text()
            self.assertNotIn("</script><script>alert(1)</script>", page)
            self.assertIn("\\u003c/script>", page)


if __name__ == "__main__":
    unittest.main()
