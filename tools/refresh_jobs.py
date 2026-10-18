"""Private, durable single-flight jobs for the local classified homepage.

This database is deliberately outside output/: it is never served or deployed.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

CHINA = ZoneInfo("Asia/Shanghai")
TERMINAL = {"completed", "partial", "failed"}
SOURCE_TERMINAL = {"updated", "cached", "partial", "failed", "paused", "disabled"}
BATCH_SLOTS = {'0700': (6,45), '1000': (9,45), '1300': (12,45), '1600': (15,45)}


def batch_refresh_start(batch_id):
    if not re.fullmatch(r'\d{8}-(?:0700|1000|1300|1600)', str(batch_id)):
        raise ValueError('Invalid scheduled batch ID')
    day, batch = batch_id.split('-')
    hour, minute = BATCH_SLOTS[batch]
    return datetime.strptime(day, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=CHINA)


def refresh_batch_id(now=None):
    now = now or datetime.now(CHINA)
    now = now.astimezone(CHINA)
    for batch, (hour, minute) in BATCH_SLOTS.items():
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if 0 <= (now-start).total_seconds() < 31*60:
            return now.strftime('%Y%m%d') + '-' + batch
    raise ValueError('Outside scheduled refresh/recovery window')


def iso(stamp=None):
    return datetime.fromtimestamp(time.time() if stamp is None else stamp, CHINA).isoformat()


class JobStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "jobs.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, phase TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, finished REAL,
                    generation TEXT, message TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS sources (
                    job_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                    state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT, count INTEGER, next_allowed_at TEXT,
                    PRIMARY KEY(job_id,id)
                );
                CREATE TABLE IF NOT EXISTS cooldowns (
                    id TEXT PRIMARY KEY, attempted REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_refreshes (
                    batch_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), bound REAL NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, sources, *, cooldown=600, now=None, batch_id=None):
        now = time.time() if now is None else now
        if batch_id:
            batch_refresh_start(batch_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if batch_id:
                bound = db.execute('SELECT job_id FROM scheduled_refreshes WHERE batch_id=?', (batch_id,)).fetchone()
                if bound:
                    return bound['job_id'], False, 'scheduled'
            def bind(key):
                if batch_id:
                    db.execute('INSERT INTO scheduled_refreshes VALUES(?,?,?)', (batch_id, key, now))
                return key
            active = db.execute("SELECT id FROM jobs WHERE state IN ('queued','running') ORDER BY created LIMIT 1").fetchone()
            if active:
                return bind(active["id"]), False, "running"
            latest = db.execute("SELECT id,finished FROM jobs ORDER BY created DESC LIMIT 1").fetchone()
            if latest and latest["finished"] and now - latest["finished"] < cooldown:
                return bind(latest["id"]), False, "cooldown"
            key = str(uuid.uuid4())
            db.execute("INSERT INTO jobs(id,state,phase,created,updated,message) VALUES(?,?,?,?,?,?)",
                       (key, "queued", "queued", now, now, "任务已排队，可关闭页面后再来查看。"))
            db.executemany("INSERT INTO sources(job_id,id,name,state) VALUES(?,?,?,'pending')",
                           [(key, source_id, name) for source_id, name in sources])
            bind(key)
        return key, True, "created"

    def scheduled_job(self, batch_id):
        with self.connect() as db:
            row=db.execute('SELECT job_id FROM scheduled_refreshes WHERE batch_id=?',(batch_id,)).fetchone()
            return row['job_id'] if row else None

    def recover(self):
        # The worker obtains the process lock before invoking this method, so a
        # running row belongs to an interrupted process, never another worker.
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='queued',message=?,updated=? WHERE state='running'",
                       ("后台曾中断，正在检查已完成步骤并恢复；不会重复运行已完成来源。", time.time()))
            db.execute("UPDATE sources SET state='pending',message=? WHERE state='running' AND job_id IN (SELECT id FROM jobs WHERE state='queued')",
                       ("上次步骤中断，恢复时遵守来源冷却，不立即重抓。",))

    def active(self):
        with self.connect() as db:
            row = db.execute("SELECT id FROM jobs WHERE state IN ('queued','running') ORDER BY created LIMIT 1").fetchone()
            return row["id"] if row else None

    def latest(self):
        with self.connect() as db:
            row = db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 1").fetchone()
            return self.get(row["id"]) if row else None

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
            if not row:
                return None
            sources = [dict(source) for source in db.execute("SELECT id,name,state,message,fetched_at,count,next_allowed_at FROM sources WHERE job_id=? ORDER BY rowid", (key,))]
            return {"id": row["id"], "state": row["state"], "phase": row["phase"],
                    "created_at": iso(row["created"]), "updated_at": iso(row["updated"]),
                    "finished_at": iso(row["finished"]) if row["finished"] else None,
                    "message": row["message"], "published_generation": row["generation"],
                    "sources": sources, "done": sum(source["state"] in SOURCE_TERMINAL for source in sources),
                    "total": len(sources), "terminal": row["state"] in TERMINAL}

    def internal_steps(self, key):
        with self.connect() as db:
            return json.loads(db.execute("SELECT steps FROM jobs WHERE id=?", (key,)).fetchone()[0])

    def update(self, key, *, state=None, phase=None, message=None, generation=None, step=None, step_data=None):
        with self.connect() as db:
            fields, values = ["updated=?"], [time.time()]
            for name, value in (("state", state), ("phase", phase), ("message", message), ("generation", generation)):
                if value is not None:
                    fields.append(name + "=?")
                    values.append(value)
            if state in TERMINAL:
                fields.append("finished=?")
                values.append(time.time())
            if step:
                steps = json.loads(db.execute("SELECT steps FROM jobs WHERE id=?", (key,)).fetchone()[0])
                steps[step] = step_data if step_data is not None else True
                fields.append("steps=?")
                values.append(json.dumps(steps))
            db.execute("UPDATE jobs SET " + ",".join(fields) + " WHERE id=?", (*values, key))

    def source(self, key, source_id, state, message, *, fetched_at=None, count=None, next_allowed_at=None):
        with self.connect() as db:
            db.execute("UPDATE sources SET state=?,message=?,fetched_at=?,count=?,next_allowed_at=? WHERE job_id=? AND id=?",
                       (state, message, fetched_at, count, next_allowed_at, key, source_id))
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), key))

    def due(self, source_id, seconds=600):
        with self.connect() as db:
            row = db.execute("SELECT attempted FROM cooldowns WHERE id=?", (source_id,)).fetchone()
            return not row or time.time() - row[0] >= seconds

    def attempted(self, source_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cooldowns VALUES(?,?)", (source_id, time.time()))

    def next_allowed(self, source_id, seconds=600):
        with self.connect() as db:
            row = db.execute("SELECT attempted FROM cooldowns WHERE id=?", (source_id,)).fetchone()
            return iso(row[0] + seconds) if row else None
