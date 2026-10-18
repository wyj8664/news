"""Fail-closed public-access checks shared by Jin10 collection and rendering."""
from __future__ import annotations

import html
import re


RESTRICTED_KEYS = (
    "is_vip", "is_plus", "vip", "plus", "need_login", "need_pay", "is_pay",
    "pay_required", "login_required", "restricted", "is_locked", "paywall",
    "requires_login", "requires_payment", "is_paid", "is_charge", "is_private",
)
PLACEHOLDER = re.compile(
    r"(?:(?:VIP|PLUS|会员|付费).{0,20}(?:专享|解锁|可见|查看)|"
    r"(?:请|立即)?登录后(?:查看|阅读|解锁|可见)|解锁(?:VIP|PLUS|会员)|"
    r"解锁文章|付费(?:后)?(?:查看|阅读)|"
    r"开通.{0,8}(?:会员|VIP|PLUS).{0,8}(?:查看|阅读))", re.I
)


def restricted_value(value: object) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def public_jin10_record(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    for node in (raw, data):
        if any(restricted_value(node.get(key)) for key in RESTRICTED_KEYS):
            return False
        for field in ("access", "access_level", "visibility"):
            access = str(node.get(field) or "").strip().lower()
            if access not in {"", "0", "public", "open", "free", "anonymous"}:
                return False  # Explicit but unknown permissions are not assumed public.
    for value in (raw.get("title"), raw.get("summary"), data.get("title"), data.get("content")):
        text = html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip(" \n\t【】")
        if PLACEHOLDER.search(text):
            return False
    return True
