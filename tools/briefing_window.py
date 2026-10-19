"""One Beijing-time window contract for selection, refresh and delivery."""
from datetime import timedelta
import re
import time
from news_freshness import CHINA, china_time, parse_publication_time

WEEKEND_POLICY = "monday-weekend-calendar"


def delivery_window(now, batch):
    now = china_time(now)
    weekend = batch == "0700" and now.weekday() == 0
    start = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
             if weekend else now - timedelta(hours=12 if batch == "0700" else 6))
    return {"window_policy": WEEKEND_POLICY if weekend else "rolling",
            "window_start": start.isoformat(), "window_end": now.isoformat(),
            "lookback_hours": (now - start).total_seconds() / 3600}


def collection_start(now):
    """Expand only Monday's early collection; keep rate/page/time limits intact."""
    now = china_time(now)
    if now.weekday() == 0 and now.hour < 8:
        return parse_publication_time(delivery_window(now, "0700")["window_start"])
    return now - timedelta(hours=24)


def eligible_for_window(item, now, report):
    at = parse_publication_time(item.get("first_reported_at"))
    if report.get("window_policy") == WEEKEND_POLICY:
        start = parse_publication_time(report.get("window_start"))
        end = parse_publication_time(report.get("window_end"))
        return at is not None and start is not None and end is not None and start <= at <= min(now, end)
    # Preserve rolling expiry and old, immutable partially-delivered plans.
    return at is not None and now - timedelta(hours=report["lookback_hours"]) <= at <= now


def validate_window(report):
    end = parse_publication_time(report.get("window_end") or report.get("generated_at") or report.get("created_at"))
    batch_id = report.get("batch_id", "")
    if (not end or not re.fullmatch(r'\d{8}-(0700|1000|1300|1600)', batch_id)
            or batch_id != end.strftime("%Y%m%d") + "-" + batch_id[-4:]
            or ('generated_at' in report and end != parse_publication_time(report['generated_at']))):
        raise ValueError("Invalid report batch/window date")
    expected = delivery_window(end, batch_id[-4:])
    if report.get("lookback_hours") != expected["lookback_hours"]:
        raise ValueError("Report lookback does not match batch")
    # Monday must have an explicit calendar start; old rolling plans can resume.
    explicit = expected["window_policy"] == WEEKEND_POLICY or "window_policy" in report
    if explicit and any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Report window metadata does not match batch")
    return expected


def wait_for_monday_dispatch(batch, *, clock, sleep=time.sleep):
    """The existing 06:55 sender may prepare, but Monday must not send before 07:00."""
    while True:
        now = china_time(clock())
        if batch != '0700' or now.weekday() != 0:
            return
        remaining = (now.replace(hour=7, minute=0, second=0, microsecond=0) - now).total_seconds()
        if remaining <= 0:
            return
        if remaining > 10 * 60:
            raise ValueError('周一早批次尚未到准备时间，不启动长时间等待')
        sleep(min(30, remaining))
