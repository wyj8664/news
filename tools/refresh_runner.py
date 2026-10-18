"""Fixed local refresh workflow. No client-supplied commands, URLs or credentials.

The daemon owns a process lock; children inherit its descriptor so recovery
cannot overlap an interrupted subprocess. Weibo remains owned by its scheduler.
"""
from __future__ import annotations

from datetime import datetime
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

from refresh_jobs import CHINA, SOURCE_TERMINAL, iso

ROOT = Path(__file__).resolve().parents[1]
WEIBO_URL = "http://127.0.0.1:8789/api/v1"
NATIVE = {"cls-telegraph": "财联社电报", "jin10-all": "金十数据", "jiemian-flash": "界面新闻·快报"}


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def source_roster(root=ROOT):
    """Read ONLY this project's simple enabled source IDs/names, not general YAML.

    Fail closed on unsupported roster syntax rather than silently omit sources.
    Actual crawler configuration is still parsed by upstream's YAML loader.
    """
    home = read_json(root / "config/home_sources.json")
    sections, section, enabled, current = {}, None, {}, None
    for line in (root / "config/config.yaml").read_text().splitlines():
        top = re.match(r"^(\w+):", line)
        if top:
            section = top[1]
        if section not in ("platforms", "rss"):
            continue
        switch = re.fullmatch(r"  enabled: (true|false)\s*", line)
        if switch:
            enabled[section] = switch[1] == "true"
        item = re.fullmatch(r'    - id: "([\w-]+)"\s*', line)
        if item:
            current = [item[1], item[1]]
            sections.setdefault(section, []).append(current)
        elif re.match(r"    - id:", line):
            raise ValueError("来源清单格式变更，请先更新后台读取规则。")
        name = re.fullmatch(r'      name: "([^"\n]+)"\s*', line)
        if name and current:
            current[1] = name[1]
    sources = []
    for group, flag in (("platforms", "trendradar"), ("rss", "rss")):
        if home.get(flag) and enabled.get(group):
            sources.extend((f"{group}:{key}", name) for key, name in sections.get(group, []))
    sources.extend((key, NATIVE[key]) for key in home.get("native_feeds", []) if key in NATIVE)
    if home.get("weibo_followed"):
        sources.append(("weibo-followed", "关注微博"))
    return sources


def capture_info(output, group, source_id):
    """Report source-level success, not the container's exit code as success."""
    folder = "news" if group == "platforms" else "rss"
    latest = None
    for path in sorted((output / folder).glob("????-??-??.db"), reverse=True):
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            if group == "platforms":
                row = db.execute("SELECT r.created_at,s.status FROM crawl_source_status s JOIN crawl_records r ON r.id=s.crawl_record_id WHERE s.platform_id=? ORDER BY r.id DESC LIMIT 1", (source_id,)).fetchone()
                count = db.execute("SELECT count(*) FROM news_items WHERE platform_id=?", (source_id,)).fetchone()[0]
            else:
                row = db.execute("SELECT r.created_at,s.status FROM rss_crawl_status s JOIN rss_crawl_records r ON r.id=s.crawl_record_id WHERE s.feed_id=? ORDER BY r.id DESC LIMIT 1", (source_id,)).fetchone()
                count = db.execute("SELECT count(*) FROM rss_items WHERE feed_id=?", (source_id,)).fetchone()[0]
            if row and latest is None:
                at = datetime.fromisoformat(row[0]).replace(tzinfo=CHINA).isoformat()
                latest = {"attempted_at": at, "success": row[1] == "success", "count": count}
            if group == "platforms":
                successful = db.execute("SELECT r.created_at FROM crawl_source_status s JOIN crawl_records r ON r.id=s.crawl_record_id WHERE s.platform_id=? AND s.status='success' ORDER BY r.id DESC LIMIT 1", (source_id,)).fetchone()
            else:
                successful = db.execute("SELECT r.created_at FROM rss_crawl_status s JOIN rss_crawl_records r ON r.id=s.crawl_record_id WHERE s.feed_id=? AND s.status='success' ORDER BY r.id DESC LIMIT 1", (source_id,)).fetchone()
        if successful:
            at = datetime.fromisoformat(successful[0]).replace(tzinfo=CHINA)
            return {**latest, "fetched_at": at.isoformat()}
    return latest or {}


class Runner:
    def __init__(self, store, lock_fd, root=ROOT):
        self.store, self.lock_fd, self.root = store, lock_fd, root
        self.output = root / "output"
        self.policy = read_json(root / "config/refresh_policy.json")

    def command(self, args, timeout=180):
        # Logs are private and never passed verbatim to the HTTP API.
        with (self.store.directory / "worker.log").open("ab") as log:
            subprocess.run(args, cwd=self.root, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                           check=True, pass_fds=(self.lock_fd,))

    def script(self, name, *args, timeout=180):
        self.command([sys.executable, str(self.root / "tools" / name), *args], timeout)

    def done(self, key, source_id):
        return next((s["state"] in SOURCE_TERMINAL for s in self.store.get(key)["sources"] if s["id"] == source_id), True)

    def publish(self, key, step):
        if self.store.internal_steps(key).get(step):
            return
        self.store.update(key, phase="publishing", message="正在按原文24小时窗口过滤、合并同一事件并生成分类。")
        stage = 'final' if step == 'final-published' else 'public'
        self.script("curated_events.py", "--output-root", "output", "--lookback-days", "2", "--current-pages-only",
                    '--refresh-job-id', key, '--refresh-stage', stage, timeout=300)
        page = (self.output / "index.html").read_text()
        generation = re.search(r'data-home-generation="([^"]+)"', page)
        if not generation:
            raise RuntimeError("首页未包含本轮生成标识")
        proof = None
        if stage == 'final':
            from news_freshness import write_json
            snapshot = read_json(self.output/'source-pool/home-events.json')
            if snapshot.get('refresh') != {'job_id':key,'stage':'final'}:
                raise RuntimeError('Final snapshot belongs to a different refresh task')
            archive = self.store.directory/'refresh-snapshots'/f'{key}.json'
            write_json(archive, snapshot)
            archive.chmod(0o600)
            proof = {'snapshot_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                     'snapshot_generated_at':snapshot['generated_at']}
        self.store.update(key, generation=generation[1], step=step, step_data=proof)

    def upstream(self, key, roster):
        sources = [(sid, name) for sid, name in roster if sid.startswith(("platforms:", "rss:")) and not self.done(key, sid)]
        if not sources:
            return
        cooldown = self.policy.get("public_source_cooldown_seconds", 600)
        before = {sid: capture_info(self.output, *sid.split(":", 1)) for sid, _ in sources}
        allowed = self.store.due("upstream-batch", cooldown)
        # Also respect a scheduled collection completed outside this job database.
        last_capture = max((datetime.fromisoformat(v["attempted_at"]).timestamp() for v in before.values() if v.get("attempted_at")), default=0)
        allowed = allowed and time.time() - last_capture >= cooldown
        if not allowed:
            attempt_next = self.store.next_allowed("upstream-batch", cooldown)
            allowed_at = max(last_capture + cooldown, datetime.fromisoformat(attempt_next).timestamp() if attempt_next else 0, time.time())
            for sid, _ in sources:
                info = before[sid]
                self.store.source(key, sid, "cached", "采集冷却中，沿用缓存；没有发起新请求。", fetched_at=info.get("fetched_at"), count=info.get("count"), next_allowed_at=iso(allowed_at))
            return
        self.store.attempted("upstream-batch")
        for sid, _ in sources:
            self.store.source(key, sid, "running", "公共热榜/RSS批次排队采集，来源间遵守原采集间隔。", fetched_at=before[sid].get("fetched_at"))
        try:
            self.command(["docker", "run", "--rm", "--name", "newshot-refresh-public", "--entrypoint", "python",
                          "-e", "TZ=Asia/Shanghai", "-e", "DOCKER_CONTAINER=true", "-e", "SCHEDULE_ENABLED=false",
                          "-e", "NOTIFICATION_ENABLED=false", "-e", "AI_ANALYSIS_ENABLED=false", "-e", "AI_TRANSLATION_ENABLED=false",
                          "-e", "STORAGE_TXT_ENABLED=true", "-e", "STORAGE_HTML_ENABLED=false",
                          "-v", f"{self.root}/config:/app/config:ro", "-v", f"{self.output}:/app/output",
                          "wantcat/trendradar:latest", "manage.py", "run"], timeout=360)
        except (OSError, subprocess.SubprocessError):
            # A timeout does not promise that Docker's daemon stopped the container.
            # Recovery waits on this exact owned container before another publish.
            self.wait_owned_container()
        for sid, _ in sources:
            info = capture_info(self.output, *sid.split(":", 1))
            changed = info.get("attempted_at") != before[sid].get("attempted_at")
            success = changed and info.get("success")
            # Failure attempt times are not content freshness times.
            effective = info
            self.store.source(key, sid, "updated" if success else "failed", "本轮采集成功。" if success else "本轮未成功采集，保留已有记录；不把失败时间当成内容更新时间。", fetched_at=effective.get("fetched_at"), count=effective.get("count"))

    def wait_owned_container(self):
        # A previous daemon may have exited while Docker was still writing.
        # Never kill a collector or touch unrelated containers during recovery.
        while True:
            try:
                result = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", "newshot-refresh-public"], capture_output=True, text=True, timeout=10)
                if result.returncode or result.stdout.strip() != "true":
                    return
            except (OSError, subprocess.SubprocessError):
                return
            time.sleep(5)

    def native(self, key, source_id):
        if self.done(key, source_id):
            return
        snapshot_path = self.output / "source-pool" / f"{source_id}.json"
        old = read_json(snapshot_path)
        cooldown = self.policy.get("public_source_cooldown_seconds", 600)
        if not self.store.due(source_id, cooldown):
            self.store.source(key, source_id, "cached", "来源冷却中，沿用缓存。", fetched_at=old.get("fetched_at"), count=len(old.get("items", [])), next_allowed_at=self.store.next_allowed(source_id, cooldown))
            return
        self.store.attempted(source_id)
        self.store.source(key, source_id, "running", "正在采集公开内容；受限、VIP和登录内容不访问。", fetched_at=old.get("fetched_at"))
        try:
            self.script("home_source_pool.py", "--output-root", "output", "--only", source_id, timeout=150)
            status = next(s for s in read_json(self.output / "source-pool/status.json").get("sources", []) if s["source_id"] == source_id)
            effective = read_json(snapshot_path)
            state = "failed" if not status.get("ok") else "cached" if status.get("cached") else "updated" if status.get("window_complete") else "partial"
            message = {"failed": "抓取失败，保留缓存。", "cached": "10分钟来源冷却中，沿用缓存。", "updated": "公开内容已更新，24小时窗口已覆盖。", "partial": "已更新可获取的公开内容，但未确认完整覆盖24小时。"}[state]
            self.store.source(key, source_id, state, message, fetched_at=effective.get("fetched_at"), count=len(effective.get("items", [])))
        except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
            self.store.source(key, source_id, "failed", "采集异常或超时，保留缓存。", fetched_at=old.get("fetched_at"), count=len(old.get("items", [])))

    def weibo_request(self, path, method="GET"):
        from weibo_feed import DEFAULT_ENV
        values = dict(line.split("=", 1) for line in DEFAULT_ENV.read_text().splitlines() if line and not line.startswith("#") and "=" in line)
        token = values.get("WEIBOX_API_TOKEN", "")
        if not token:
            raise RuntimeError("微博后台未配置")
        req = urllib.request.Request(WEIBO_URL + path, data=b"{}" if method == "POST" else None,
                                     method=method, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as response:
            return json.load(response)

    def export_weibo(self):
        self.script("weibo_feed.py", "--output-root", "output", "--allow-stale", timeout=100)

    def weibo(self, key):
        sid = "weibo-followed"
        if self.done(key, sid):
            return
        old = read_json(self.output / "weibo/latest.json")
        try:
            # The service returns cached if accounts are not due. No forced bypass.
            result = self.weibo_request("/refresh", "POST")
            state = result.get("state")
            if state == "paused":
                self.store.source(key, sid, state, result.get("message", "沿用微博缓存。"), fetched_at=old.get("fetched_at"), count=old.get("count"), next_allowed_at=result.get("next_allowed_at"))
                self.export_weibo()
                return
            if state not in {'cached','running','queued'}:
                raise RuntimeError('Unknown Weibo refresh response')
            baseline = result.get("round_started", 0)
            deadline = time.monotonic() + self.policy.get("weibo_wait_timeout_seconds", 1200)
            while time.monotonic() < deadline:
                status = self.weibo_request("/status")
                started = status.get("last_round_started", 0)
                if state == 'cached' and status.get('round_running'):
                    state, baseline = 'running', started
                observed = state == 'cached' or (started >= baseline if state == "running" else started > baseline)
                self.store.source(key, sid, "running", f"按微博后台限频排队：{status.get('progress_done', 0)}/{status.get('progress_total', 0)}账号；可关闭页面。", fetched_at=old.get("fetched_at"), count=old.get("count"))
                bad = bool(status.get('captcha_pending') or status.get('last_error') or status.get('enabled') is not True)
                if bad:
                    self.export_weibo()
                    latest = read_json(self.output / "weibo/latest.json")
                    self.store.source(key, sid, 'paused', '微博采集异常或触发保护，已停止本轮；本批飞书不放行。', fetched_at=latest.get('fetched_at'), count=latest.get('count'))
                    return
                if observed and status.get('round_running') is False:
                    policy = self.weibo_request('/refresh-policy')
                    complete = (status.get('progress_done') == status.get('progress_total')
                                and type(status.get('progress_total')) is int
                                and status['progress_total'] >= 0
                                and (state == 'cached' or (started > 0 and status.get('last_round_finished',0) >= started)))
                    safe_policy = (policy.get('enabled') is True and policy.get('account_count',0) > 0
                                   and policy.get('blocked_until_ms',0) <= time.time()*1000
                                   and (state != 'cached' or policy.get('due_count') == 0))
                    if not complete or not safe_policy:
                        raise RuntimeError('Weibo completion could not be verified')
                    self.export_weibo()
                    confirmed = self.weibo_request('/status')
                    if confirmed.get('round_running') or confirmed.get('last_round_started') != started:
                        state, baseline = 'running', confirmed.get('last_round_started',0)
                        time.sleep(self.policy.get('poll_seconds',5))
                        continue
                    if confirmed.get('captcha_pending') or confirmed.get('last_error') or confirmed.get('enabled') is not True:
                        raise RuntimeError('Weibo status changed during export')
                    latest = read_json(self.output/'weibo/latest.json')
                    mode = 'cached' if state == 'cached' else 'updated'
                    self.store.update(key,step='weibo-verified',step_data={
                        'complete':True,'mode':mode,'verified_at':iso(),
                        'round_started':started,'round_finished':confirmed.get('last_round_finished',0),
                        'progress_done':confirmed.get('progress_done'),'progress_total':confirmed.get('progress_total'),
                        'account_count':policy['account_count']})
                    self.store.source(key,sid,mode,
                        '已确认微博后台空闲且账号均未到期，按限频使用缓存。' if mode=='cached' else '已确认本轮微博全部到期账号完成，并导出最终缓存。',
                        fetched_at=latest.get('fetched_at'),count=latest.get('count'),next_allowed_at=result.get('next_allowed_at'))
                    return
                time.sleep(self.policy.get("poll_seconds", 5))
            self.export_weibo()
            latest = read_json(self.output / "weibo/latest.json")
            self.store.source(key, sid, "partial", "等待时间已到，已合并当前缓存；微博后台可能仍在排队，未重复启动。", fetched_at=latest.get("fetched_at"), count=latest.get("count"))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.store.source(key, sid, "failed", "微博后台暂不可用，保留旧缓存；未绕过权限或启动替代抓取。", fetched_at=old.get("fetched_at"), count=old.get("count"))

    def run(self, key):
        self.store.update(key, state="running", phase="public", message="第一阶段：依次更新公共来源，随后先发布一版领域分类。")
        try:
            roster = [(s["id"], s["name"]) for s in self.store.get(key)["sources"]]
            # Fixed workflow; native feeds are deliberately sequential.
            self.upstream(key, roster)
            for source_id, _ in roster:
                if source_id in NATIVE:
                    self.native(key, source_id)
            # Preserve the previous scheduled workflow's non-news quote panel.
            if not self.store.internal_steps(key).get("market-quotes") and (self.root / "tools/market_quotes.py").exists():
                try:
                    self.script("market_quotes.py", "--output-root", "output", "--watchlist", "config/market_watchlist.json", timeout=90)
                except (OSError, subprocess.SubprocessError):
                    pass  # quote panel keeps its own cache and freshness label
                self.store.update(key, step="market-quotes")
            if not self.store.internal_steps(key).get("cache-export"):
                try:
                    self.export_weibo()  # cached API only, does not trigger collection
                except (OSError, subprocess.SubprocessError):
                    pass
                self.store.update(key, step="cache-export")
            self.publish(key, "public-published")
            self.store.update(key, phase="weibo", message="公共来源分类已发布。第二阶段：按微博后台限频检查关注账号；未到期不强抓。")
            if any(sid == "weibo-followed" for sid, _ in roster):
                self.weibo(key)
            self.publish(key, "final-published")
            bad = any(s["state"] in ("partial", "failed", "paused") for s in self.store.get(key)["sources"])
            self.store.update(key, state="partial" if bad else "completed", phase="done", message="本轮已结束，分类已更新；部分来源未抓全、失败或暂停，详情见来源进度。" if bad else "本轮完成，分类已更新；冷却中的来源沿用各自缓存。")
        except Exception:
            for source in self.store.get(key)["sources"]:
                if source["state"] not in SOURCE_TERMINAL:
                    self.store.source(key, source["id"], "failed", "任务中断，未继续请求；原缓存保留。", fetched_at=source.get("fetched_at"), count=source.get("count"))
            self.store.update(key, state="failed", phase="done", message="后台任务未完成，已保留上一次可用页面和缓存，请检查本地后台日志。")
