"""Versioned progress-time ranking; no I/O, collection or persistent identities."""
from __future__ import annotations

import math
from news_freshness import china_time, parse_publication_time

POLICY = {"version": "progress-decay-v1", "grace_hours": 1.0,
          "half_life_hours": 6.0, "floor": 0.5}


def epoch_millis(value):
    value = china_time(value)
    return int(value.timestamp()) * 1000 + value.microsecond // 1000


def decay_factor(age_hours, policy=None):
    policy = policy or POLICY
    age = max(0.0, float(age_hours) - policy['grace_hours'])
    return policy['floor'] + (1 - policy['floor']) * 2 ** (-age / policy['half_life_hours'])


def rank_progress(importance, first_reported_at, now):
    first = parse_publication_time(first_reported_at)
    importance = float(importance)
    if not first or not math.isfinite(importance):
        raise ValueError('Missing verified progress time or finite importance')
    # Browser Date uses milliseconds; do not let Python's extra microseconds
    # produce a different score/order from the same published snapshot.
    age = max(0.0, (epoch_millis(now) - epoch_millis(first)) / 3600000)
    factor = decay_factor(age)
    return {'importance_score': max(0.0, importance), 'first_reported_at': first.isoformat(),
            'decay_factor': factor, 'score': round(max(0.0, importance) * factor, 8)}


def rank_snapshot_progress(event, progress, now):
    # Never multiply an already-decayed score again. Old immutable snapshots
    # retain their original importance score and are supported during rollout.
    importance = progress.get('importance_score', event.get('importance_score', event['score']))
    return rank_progress(importance, progress['first_reported_at'], now)


def card_rank(progresses):
    return max((node['score'] for node in progresses), default=0.0)


def sort_key(score, first_reported_at, stable_id):
    return (-score, -epoch_millis(parse_publication_time(first_reported_at)), stable_id)
