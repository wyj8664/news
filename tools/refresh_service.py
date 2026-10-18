#!/usr/bin/env python3
"""Loopback-only durable refresh queue, independent of browser lifetime."""
from __future__ import annotations

import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from refresh_jobs import JobStore, refresh_batch_id
from refresh_runner import ROOT, Runner, read_json, source_roster

ORIGINS = {"http://127.0.0.1:8799", "http://localhost:8799"}


class Service:
    def __init__(self, root=ROOT):
        self.root = root
        self.store = JobStore(root / ".local-refresh")
        self.nonce = secrets.token_urlsafe(32)
        self.wake = threading.Event()
        self.policy = read_json(root / "config/refresh_policy.json")

    def enqueue(self, batch_id=None):
        result = self.store.enqueue(source_roster(self.root), cooldown=self.policy.get("job_cooldown_seconds", 600), batch_id=batch_id)
        self.wake.set()
        return result

    def work(self):
        # Another daemon or its orphan subprocess may still own this lock.
        with (self.store.directory / "runner.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            runner = Runner(self.store, lock.fileno(), self.root)
            runner.wait_owned_container()
            self.store.recover()
            while True:
                key = self.store.active()
                if key:
                    runner.wait_owned_container()
                    runner.run(key)
                else:
                    self.wake.wait(3)
                    self.wake.clear()


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass  # no cookies/tokens or request bodies in access logs

        def reply(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def guard(self, mutation=False):
            # Host validation also prevents DNS-rebinding to the loopback server.
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed_hosts:
                self.reply(403, {"ok": False, "error": "不允许的访问地址。"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in ORIGINS:
                self.reply(403, {"ok": False, "error": "不允许跨站访问更新后台。"})
                return False
            if mutation:
                if origin not in ORIGINS or not secrets.compare_digest(self.headers.get("X-Refresh-Nonce", ""), service.nonce):
                    self.reply(403, {"ok": False, "error": "更新凭证失效，请刷新页面后重试。"})
                    return False
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.reply(415, {"ok": False, "error": "只接受JSON请求。"})
                    return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            if urlsplit(self.path).path != "/status":
                return self.reply(404, {"ok": False})
            self.reply(200, {"ok": True, "available": True, "nonce": service.nonce, "job": service.store.latest(), "cooldown_seconds": service.policy.get("job_cooldown_seconds", 600)})

        def do_POST(self):
            if not self.guard(mutation=True):
                return
            if self.path != "/start":
                return self.reply(404, {"ok": False})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 100:
                    raise ValueError()
                if json.loads(self.rfile.read(size)) != {}:
                    raise ValueError()
                key, created, reason = service.enqueue()
            except (ValueError, OSError):
                return self.reply(400, {"ok": False, "error": "请求或来源配置无效。"})
            self.reply(202 if created else 200, {"ok": True, "created": created, "reason": reason, "job": service.store.get(key)})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enqueue", action="store_true", help="Submit from the existing weekday scheduler; does not collect in this CLI")
    parser.add_argument("--scheduled", action="store_true", help="Bind this scheduled refresh to its Feishu batch")
    args = parser.parse_args()
    service = Service()
    if args.enqueue:
        key, created, reason = service.enqueue(refresh_batch_id() if args.scheduled else None)
        print(json.dumps({"id": key, "created": created, "reason": reason}))
        return
    server = ThreadingHTTPServer(("127.0.0.1", 8801), handler_for(service))
    server.timeout = 10
    threading.Thread(target=service.work, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
