from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from refresh_jobs import JobStore
from refresh_runner import Runner, capture_info, source_roster
from refresh_service import Service, handler_for
from http.server import ThreadingHTTPServer


class DurableJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = JobStore(self.root / ".private")

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_clicks_and_scheduled_submission_are_single_flight(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.enqueue([("a", "来源A")]), range(12)))
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)
        key = results[0][0]
        self.store.update(key, state="completed")
        self.assertEqual(self.store.enqueue([])[2], "cooldown")
        self.assertTrue(self.store.enqueue([], cooldown=0)[1])

    def test_restart_preserves_finished_steps_cache_and_cooldowns(self):
        key, *_ = self.store.enqueue([("a", "A"), ("b", "B")])
        self.store.update(key, state="running", generation="2026-10-18T10:00:00+08:00", step="public-published")
        self.store.source(key, "a", "updated", "done", fetched_at="old-actual-time", count=3)
        self.store.source(key, "b", "running", "pending")
        self.store.attempted("b")
        reopened = JobStore(self.root / ".private")
        reopened.recover()
        job = reopened.get(key)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["sources"][0]["fetched_at"], "old-actual-time")
        self.assertEqual(job["sources"][0]["state"], "updated")
        self.assertEqual(job["sources"][1]["state"], "pending")
        self.assertTrue(reopened.internal_steps(key)["public-published"])
        self.assertFalse(reopened.due("b", 600))

    def test_workflow_publishes_public_before_weibo_and_finishes_partial(self):
        key, *_ = self.store.enqueue([("cls-telegraph", "CLS"), ("weibo-followed", "微博")])
        runner = Runner(self.store, 0, self.root)
        calls = []
        def native(key, sid):
            calls.append(sid)
            self.store.source(key, sid, "partial", "not complete")
        def weibo(key):
            calls.append("weibo")
            self.store.source(key, "weibo-followed", "cached", "not due")
        with mock.patch.object(runner, "upstream"), mock.patch.object(runner, "native", side_effect=native), \
             mock.patch.object(runner, "export_weibo"), mock.patch.object(runner, "weibo", side_effect=weibo), \
             mock.patch.object(runner, "publish", side_effect=lambda key, step: calls.append(step)):
            runner.run(key)
        self.assertEqual(calls, ["cls-telegraph", "public-published", "weibo", "final-published"])
        self.assertEqual(self.store.get(key)["state"], "partial")

    def test_failure_does_not_replace_cache_or_report_success(self):
        key, *_ = self.store.enqueue([("jin10-all", "金十")])
        pool = self.root / "output/source-pool"
        pool.mkdir(parents=True)
        snapshot = {"fetched_at": "2026-10-17T08:00:00+08:00", "items": [{"id": 1}]}
        (pool / "jin10-all.json").write_text(json.dumps(snapshot))
        runner = Runner(self.store, 0, self.root)
        with mock.patch.object(runner, "script", side_effect=OSError("offline")):
            runner.native(key, "jin10-all")
        source = self.store.get(key)["sources"][0]
        self.assertEqual(source["state"], "failed")
        self.assertEqual(source["fetched_at"], snapshot["fetched_at"])
        self.assertEqual(json.loads((pool / "jin10-all.json").read_text()), snapshot)

    def test_failed_attempt_not_reported_as_successful_capture_time(self):
        directory = self.root / "rss"
        directory.mkdir()
        with sqlite3.connect(directory / "2026-10-18.db") as db:
            db.executescript("CREATE TABLE rss_crawl_records(id,created_at); CREATE TABLE rss_crawl_status(crawl_record_id,feed_id,status); CREATE TABLE rss_items(feed_id);")
            db.executemany("INSERT INTO rss_crawl_records VALUES(?,?)", [(1,"2026-10-18 08:00:00"),(2,"2026-10-18 09:00:00")])
            db.executemany("INSERT INTO rss_crawl_status VALUES(?,?,?)", [(1,"sina","success"),(2,"sina","failed")])
        db.close()
        info = capture_info(self.root, "rss", "sina")
        self.assertFalse(info["success"])
        self.assertIn("08:00:00", info["fetched_at"])
        self.assertIn("09:00:00", info["attempted_at"])

    def test_configured_roster_matches_enabled_news_sources(self):
        ids = [sid for sid, _ in source_roster()]
        self.assertEqual(len(ids), 16)
        self.assertIn("rss:sina-finance", ids)
        self.assertIn("jiemian-flash", ids)
        self.assertNotIn("wechat", ids)

    def test_http_host_origin_nonce_and_no_arbitrary_payload(self):
        service = Service(self.root)
        service.store = self.store
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, headers=None, data=None):
            req = urllib.request.Request(base + path, headers=headers or {}, data=data)
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status, json.load(response), response.headers
            except urllib.error.HTTPError as response:
                with response:
                    return response.code, json.load(response), response.headers
        try:
            code, status, headers = request("/status")
            self.assertEqual(code, 200)
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
            good = {"Origin":"http://127.0.0.1:8799", "Content-Type":"application/json", "X-Refresh-Nonce":status["nonce"]}
            self.assertEqual(request("/start", {**good, "Origin":"https://evil.example"}, b"{}")[0], 403)
            self.assertEqual(request("/start", {**good, "X-Refresh-Nonce":"bad"}, b"{}")[0], 403)
            self.assertEqual(request("/status", {"Host":"evil.example"})[0], 403)
            self.assertEqual(request("/start", good, b'{"command":"oops"}')[0], 400)
            with mock.patch("refresh_service.source_roster", return_value=[("a","A")]):
                self.assertEqual(request("/start", good, b"{}")[0], 202)
                self.assertFalse(request("/start", good, b"{}")[1]["created"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
