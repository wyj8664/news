#!/usr/bin/env python3
"""Generate curated news events, layered daily reports, and event timelines."""

from __future__ import annotations

import argparse
import html
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


SOURCE_PROFILES = {
    "thepaper": ("权威媒体", "综合新闻", 16, 82),
    "ifeng": ("权威媒体", "综合新闻", 12, 72),
    "wallstreetcn-hot": ("财经专业源", "财经市场", 17, 84),
    "cls-hot": ("财经专业源", "财经市场", 16, 82),
    "sina-finance": ("财经专业源", "财经市场", 14, 76),
    "xueqiu-hotstock": ("市场热榜", "财经市场", 9, 62),
    "baidu": ("综合热榜", "综合热榜", 10, 58),
    "toutiao": ("综合热榜", "综合热榜", 10, 58),
    "weibo": ("社交热榜", "社交网络", 8, 46),
    "zhihu": ("社区讨论", "社区问答", 8, 54),
    "bilibili-hot-search": ("娱乐社区", "视频社区", 6, 44),
    "douyin": ("娱乐社区", "短视频", 6, 42),
}

DOMAIN_PATTERNS = {
    "policy": ("政策", "国务院", "外交部", "商务部", "央行", "证监会", "财政部", "国家", "中共中央", "监管", "法律", "条例", "办法", "发布会"),
    "international": ("美国", "特朗普", "白宫", "关税", "欧盟", "俄罗斯", "乌克兰", "伊朗", "以色列", "中东", "日本", "韩国", "印度", "访华", "制裁", "谈判", "停火"),
    "military": ("战争", "军事", "袭击", "导弹", "核", "浓缩铀", "停火", "军方", "航母", "防务"),
    "finance": ("黄金", "白银", "原油", "美元", "人民币", "美股", "A股", "港股", "股市", "债券", "利率", "降息", "通胀", "央行", "关税", "英伟达", "英特尔", "美光", "财报"),
    "tech": ("AI", "人工智能", "芯片", "半导体", "机器人", "模型", "OpenAI", "英伟达", "苹果", "特斯拉", "算力", "数据中心"),
    "society": ("事故", "通报", "调查", "地震", "火灾", "爆炸", "医院", "学校", "食品", "公共安全", "死亡", "救援", "儿童"),
    "entertainment": ("电影", "票房", "综艺", "明星", "演唱会", "剧集", "官宣", "热映", "定档"),
    "sports": ("世界杯", "比赛", "夺冠", "联赛", "球队", "球员", "体育"),
}

DOMAIN_LABELS = {
    "policy": "时政政策",
    "international": "国际关系",
    "military": "军事安全",
    "finance": "财经市场",
    "tech": "科技产业",
    "society": "社会民生",
    "entertainment": "文娱",
    "sports": "体育",
}

CATEGORY_LABELS = {
    "policy": "时政",
    "international": "国际",
    "military": "军事",
    "finance": "财经",
    "tech": "科技",
    "society": "社会",
    "entertainment": "文娱",
    "sports": "体育",
    "general": "综合",
}

SOURCE_CATEGORY_HINTS = {
    "wallstreetcn-hot": "finance",
    "cls-hot": "finance",
    "sina-finance": "finance",
    "xueqiu-hotstock": "finance",
}

CATEGORY_RULES = {
    "finance": (
        (r"CPI|通胀|降息|利率|美联储|美元|人民币|汇率", 18),
        (r"央行|证监会|交易所|货币政策|适度宽松|降准|流动性|IPO", 18),
        (r"美股|A股|港股|股市|沪指|纳指|标普|道指|中概股|ETF|期货|债券|基金|财报|营收|利润", 18),
        (r"黄金|白银|贵金属|原油|布油|油价|大宗商品", 20),
        (r"关税|经贸|贸易|出口|进口|供应链|制裁|反制", 24),
        (r"销量|汽车销量|车企|新能源车|油车|电动车", 12),
        (r"芯片股|半导体ETF|CPO|存储芯片", 12),
    ),
    "military": (
        (r"战争|军事|军方|袭击|空袭|导弹|航母|防务|武装|交火", 22),
        (r"核武|核设施|核谈判|核问题|核计划|浓缩铀|霍尔木兹|停火|军事行动", 18),
        (r"伊朗|以色列|美伊", 8),
    ),
    "international": (
        (r"美国|特朗普|白宫|拜登|欧盟|俄罗斯|乌克兰|伊朗|以色列|日本|韩国|印度|英国|法国|德国", 12),
        (r"中美|访华|来华|外交|外长|总统|首相|大使|会晤|谈判|联合国", 16),
        (r"台湾问题|南海|国际观察", 12),
    ),
    "policy": (
        (r"国务院|中共中央|全国人大|最高法|最高检|央行|证监会|财政部|商务部|发改委|国家统计局|网信办", 18),
        (r"政策|监管|法律|条例|规划|审议|挂牌督办", 16),
        (r"地方政府|市委|省委|问责", 10),
    ),
    "tech": (
        (r"AI|人工智能|大模型|模型|OpenAI|DeepSeek|机器人|算力|数据中心", 18),
        (r"芯片|半导体|英伟达|英特尔|AMD|高通|美光|苹果|华为|小米|特斯拉|宇树", 16),
        (r"发射|飞船|航天|天舟|卫星|火箭|探测器", 12),
    ),
    "society": (
        (r"事故|污染|中毒|救治|调查|通报|火灾|爆炸|地震|死亡|救援|医院|学校|儿童|偷拍|开除|违法|犯罪|谣言|被罚|退货|纠纷", 20),
        (r"新郎|新娘|婚礼|婚宴|结婚|走红毯|内裤|短裤|皮鞋|公共场合", 34),
        (r"新物种|物种|两头蛇|蛇类|普通蛇|野生动物|动植物|昆虫|灭绝|生态|自然保护|生物多样性|科普|科学发现|研究发现", 30),
        (r"女子|男子|女生|男生|老人|孩子|宝宝|病逝|失踪|法院|判决|维权|真伪|招商|拿地", 16),
        (r"存款|医保|房贷|就业|工资|高考|教育|食品|农田|公共安全|民生|消费者|生态|环境", 14),
        (r"汶川|可乐男孩|母亲|老人", 8),
    ),
    "entertainment": (
        (r"《[^》]+》.*(大结局|开播|播出|定档|上映|票房|选角|主演|导演|演员|剧情|剧集|电视剧|电影)", 34),
        (r"低智商犯罪|大结局|单更|选角|追剧|剧透|剧情|番外|收官", 30),
        (r"电影|票房|综艺|明星|演员|歌手|演唱会|剧集|电视剧|热映|定档|官宣|红毯|戛纳|电影节|白鹿|巩俐", 18),
    ),
    "sports": (
        (r"世界杯|比赛|夺冠|联赛|球队|球员|体育|国际足联|足球|篮球|NBA|CBA|中超|欧冠|英超|世预赛|网球|乒乓|羽毛球|电竞|AG超玩会|狼队|皇马", 18),
    ),
}

TRACK_CATEGORIES = {
    "trump-china-visit": "international",
    "tariff-war": "finance",
    "gold-trend": "finance",
    "us-iran": "military",
}

CATEGORY_ORDER = ("policy", "international", "military", "finance", "tech", "society", "entertainment", "sports", "general")
VISIBLE_CATEGORY_ORDER = ("policy", "international", "military", "finance", "tech", "society", "entertainment", "sports")

IMPORTANT_TERMS = (
    "国务院", "外交部", "商务部", "央行", "证监会", "财政部", "美国", "特朗普", "关税", "访华",
    "伊朗", "以色列", "俄罗斯", "乌克兰", "停火", "战争", "制裁", "谈判", "黄金", "白银",
    "原油", "美元", "人民币", "美股", "A股", "港股", "利率", "降息", "通胀", "AI", "芯片",
    "半导体", "英伟达", "事故", "调查", "通报", "地震", "爆炸", "死亡",
)

WATER_TERMS = (
    "热映", "票房破", "定档", "开播", "路透", "晒照", "穿搭", "红毯", "机场", "花絮", "综艺",
    "小贴士", "赏花", "打卡", "文旅", "美食", "养生", "每坐30分钟", "小程序", "网友热议",
    "泪目", "太甜", "梦幻联动", "离谱", "尴尬", "神回复", "微信状态", "访客记录", "客服回应",
)

NOVELTY_TERMS = ("首次", "突破", "超预期", "罕见", "突然", "反转", "重磅", "宣布", "批准", "恢复", "升级", "创历史")
FOLLOWUP_TERMS = ("将", "拟", "计划", "考虑", "谈判", "会晤", "调查", "通报", "审议", "制裁", "停火", "协议", "走势", "价格", "访华", "裁决")
CONTRAST_TERMS = ("反转", "否认", "回应", "拒绝", "分歧", "僵局", "超预期", "暴涨", "暴跌", "无能狂怒", "翻车", "岌岌可危")
PROXIMITY_TERMS = ("中国", "中方", "国内", "A股", "港股", "人民币", "央行", "证监会", "商务部", "普通人", "就业", "房贷", "医保")

TRACK_DEFINITIONS = [
    {
        "id": "trump-china-visit",
        "name": "特朗普访华",
        "patterns": ("特朗普|美国总统", "访华|中国之行|来华|对中国进行国事访问|中美元首"),
        "watch": "看会谈议题、随访企业名单、经贸/科技表述，以及双方是否形成可执行成果。",
    },
    {
        "id": "tariff-war",
        "name": "关税战与中美经贸",
        "patterns": ("关税|经贸磋商|贸易战|贸易谈判", "美国|中美|中国|特朗普"),
        "watch": "看关税是否阶段性下调、豁免范围是否扩大，以及产业链和市场是否重新定价。",
    },
    {
        "id": "gold-trend",
        "name": "黄金与贵金属走势",
        "patterns": ("黄金|白银|贵金属",),
        "watch": "看美元利率、地缘冲突、央行购金和避险情绪是否共振。",
    },
    {
        "id": "us-iran",
        "name": "美伊/伊朗局势",
        "patterns": ("伊朗|美伊|浓缩铀|霍尔木兹", "美国|特朗普|以色列|停火|战争|军事"),
        "watch": "看浓缩铀处置、停火谈判、军事行动和原油价格是否出现升级信号。",
    },
]

DIGEST_TERMS = ("【早报】", "早报", "晚报", "早餐", "FM-Radio", "今夜看点", "环球市场", "8点1氪", "邦早报")


@dataclass
class RawItem:
    date: str
    title: str
    url: str
    source_id: str
    source_name: str
    source_tier: str
    source_type: str
    source_weight: int
    source_trust: int
    kind: str
    first_dt: datetime
    last_dt: datetime
    count: int = 1
    best_rank: int | None = None
    worst_rank: int | None = None
    summary: str = ""
    score: float = 0.0


@dataclass
class Event:
    key: str
    title: str = ""
    url: str = ""
    items: list[RawItem] = field(default_factory=list)
    source_ids: set[str] = field(default_factory=set)
    source_types: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    category: str = "general"
    dimensions: dict[str, int] = field(default_factory=dict)
    score: float = 0.0
    water_score: int = 0
    reasons: list[str] = field(default_factory=list)
    next_watch: str = ""
    status: str = ""
    trackable: bool = False

    def add(self, item: RawItem) -> None:
        self.items.append(item)
        self.source_ids.add(item.source_id)
        self.source_types.add(item.source_type)
        self.dates.add(item.date)
        self.domains.update(classify_domains(item.title))
        if not self.title or display_score(item) > max((display_score(i) for i in self.items[:-1]), default=-1):
            self.title = item.title
            self.url = item.url

    @property
    def first_dt(self) -> datetime:
        return min(item.first_dt for item in self.items)

    @property
    def last_dt(self) -> datetime:
        return max(item.last_dt for item in self.items)

    @property
    def best_rank(self) -> int | None:
        ranks = [item.best_rank for item in self.items if item.best_rank is not None]
        return min(ranks) if ranks else None

    @property
    def count(self) -> int:
        return sum(max(1, item.count) for item in self.items)

    @property
    def source_count(self) -> int:
        return len(self.source_ids)


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[#【】\[\]（）()《》“”\"'：:，,。.!！?？、\s\-_/|]+", "", title)
    title = re.sub(r"(热搜|最新|突发|刚刚|详情|全文)$", "", title)
    return title


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) >= 8 and short in long:
        return True
    if min(len(a), len(b)) < 10:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.76


def term_hits(text: str, terms: Iterable[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def classify_domains(text: str) -> set[str]:
    domains = set()
    for domain, terms in DOMAIN_PATTERNS.items():
        if term_hits(text, terms):
            domains.add(domain)
    if not domains:
        domains.add("general")
    return domains


def domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, "综合")


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, "综合")


def category_scores(text: str) -> dict[str, int]:
    scores = {category: 0 for category in CATEGORY_LABELS}
    for category, rules in CATEGORY_RULES.items():
        for pattern, weight in rules:
            if re.search(pattern, text, re.I):
                scores[category] += weight
    return scores


def primary_category_for_text(text: str) -> str:
    scores = category_scores(text)
    best = max(CATEGORY_ORDER, key=lambda category: (scores.get(category, 0), -CATEGORY_ORDER.index(category)))
    return best if scores.get(best, 0) > 0 else "general"


def primary_category_for_event(event: Event) -> str:
    title_scores = category_scores(event.title)
    title_best = max(CATEGORY_ORDER, key=lambda category: (title_scores.get(category, 0), -CATEGORY_ORDER.index(category)))
    if title_scores.get(title_best, 0) >= 28:
        return title_best

    totals = {category: 0.0 for category in CATEGORY_LABELS}
    source_hints = {category: 0 for category in CATEGORY_LABELS}
    seen: set[str] = set()
    for item in sorted(event.items, key=lambda it: it.score, reverse=True)[:16]:
        key = normalize_title(item.title)
        if key in seen:
            continue
        seen.add(key)
        item_scores = category_scores(item.title)
        weight = 1.0 + min(2.2, max(0.0, item.score) / 60)
        for category, score in item_scores.items():
            totals[category] += score * weight
        hint = SOURCE_CATEGORY_HINTS.get(item.source_id)
        if hint:
            source_hints[hint] += 1

    best = max(CATEGORY_ORDER, key=lambda category: (totals.get(category, 0), -CATEGORY_ORDER.index(category)))
    if totals.get(best, 0) > 0:
        return best
    hint_best = max(CATEGORY_ORDER, key=lambda category: (source_hints.get(category, 0), -CATEGORY_ORDER.index(category)))
    return hint_best if source_hints.get(hint_best, 0) > 0 else "general"


def profile_for(source_id: str, fallback_name: str) -> tuple[str, str, int, int]:
    return SOURCE_PROFILES.get(source_id, ("普通来源", fallback_name or "综合来源", 8, 50))


def parse_time(date: str, value: str) -> datetime:
    value = (value or "00:00").replace("-", ":")
    if re.fullmatch(r"\d{2}:\d{2}", value):
        raw = f"{date} {value}"
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.strptime(date, "%Y-%m-%d")


def parse_iso_like(value: str, fallback_date: str, fallback_time: str) -> datetime:
    if value:
        raw = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=None)
        except ValueError:
            pass
    return parse_time(fallback_date, fallback_time)


def parse_rank(value: object) -> int | None:
    try:
        rank = int(value)
        return rank if rank > 0 else None
    except (TypeError, ValueError):
        return None


def water_score(text: str) -> int:
    return min(24, len(term_hits(text, WATER_TERMS)) * 5)


def important_score(text: str) -> int:
    return min(36, len(term_hits(text, IMPORTANT_TERMS)) * 4)


def item_score(title: str, source_weight: int, count: int, best_rank: int | None, source_trust: int) -> float:
    rank_score = 0 if best_rank is None else 56 / ((best_rank + 2) ** 0.72)
    persistence = min(18, math.log1p(count) * 5)
    trust = source_trust / 10
    digest_penalty = 20 if is_digest_title(title) else 0
    return source_weight + rank_score + persistence + trust + important_score(title) - water_score(title) * 0.8 - digest_penalty


def is_digest_title(title: str) -> bool:
    return bool(term_hits(title, DIGEST_TERMS))


def display_score(item: RawItem) -> float:
    return item.score + item.source_trust / 18 - (28 if is_digest_title(item.title) else 0) - water_score(item.title)


def connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def recent_db_dates(output_root: Path, lookback_days: int) -> list[str]:
    dates = set()
    for folder in (output_root / "news", output_root / "rss"):
        if folder.exists():
            for path in folder.glob("*.db"):
                dates.add(path.stem)
    return sorted(dates)[-lookback_days:]


def load_hotlist_items(output_root: Path, date: str) -> list[RawItem]:
    db_path = output_root / "news" / f"{date}.db"
    conn = connect(db_path)
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT
            n.id,
            n.title,
            n.platform_id,
            COALESCE(p.name, n.platform_id) AS source_name,
            n.rank,
            n.url,
            n.first_crawl_time,
            n.last_crawl_time,
            n.crawl_count,
            MIN(r.rank) AS best_rank,
            MAX(r.rank) AS worst_rank
        FROM news_items n
        LEFT JOIN platforms p ON p.id = n.platform_id
        LEFT JOIN rank_history r ON r.news_item_id = n.id
        GROUP BY n.id
        """
    ).fetchall()
    conn.close()

    items: list[RawItem] = []
    for row in rows:
        source_id = row["platform_id"]
        tier, source_type, source_weight, source_trust = profile_for(source_id, row["source_name"])
        best_rank = parse_rank(row["best_rank"]) or parse_rank(row["rank"])
        worst_rank = parse_rank(row["worst_rank"]) or parse_rank(row["rank"])
        count = int(row["crawl_count"] or 1)
        first_dt = parse_time(date, row["first_crawl_time"] or "")
        last_dt = parse_time(date, row["last_crawl_time"] or row["first_crawl_time"] or "")
        title = clean_text(row["title"])
        score = item_score(title, source_weight, count, best_rank, source_trust)
        items.append(
            RawItem(
                date=date,
                title=title,
                url=row["url"] or "",
                source_id=source_id,
                source_name=row["source_name"] or source_id,
                source_tier=tier,
                source_type=source_type,
                source_weight=source_weight,
                source_trust=source_trust,
                kind="hotlist",
                first_dt=first_dt,
                last_dt=last_dt,
                count=count,
                best_rank=best_rank,
                worst_rank=worst_rank,
                score=score,
            )
        )
    return items


def load_rss_items(output_root: Path, date: str) -> list[RawItem]:
    db_path = output_root / "rss" / f"{date}.db"
    conn = connect(db_path)
    if conn is None:
        return []
    rows = conn.execute(
        """
        SELECT
            r.title,
            r.feed_id,
            COALESCE(f.name, r.feed_id) AS source_name,
            r.url,
            r.published_at,
            r.summary,
            r.first_crawl_time,
            r.last_crawl_time,
            r.crawl_count
        FROM rss_items r
        LEFT JOIN rss_feeds f ON f.id = r.feed_id
        """
    ).fetchall()
    conn.close()

    items: list[RawItem] = []
    for row in rows:
        source_id = row["feed_id"]
        tier, source_type, source_weight, source_trust = profile_for(source_id, row["source_name"])
        count = int(row["crawl_count"] or 1)
        title = clean_text(row["title"])
        first_dt = parse_iso_like(row["published_at"] or "", date, row["first_crawl_time"] or "")
        last_dt = parse_time(date, row["last_crawl_time"] or row["first_crawl_time"] or "")
        score = item_score(title, source_weight, count, None, source_trust) + 8
        items.append(
            RawItem(
                date=date,
                title=title,
                url=row["url"] or "",
                source_id=source_id,
                source_name=row["source_name"] or source_id,
                source_tier=tier,
                source_type=source_type,
                source_weight=source_weight,
                source_trust=source_trust,
                kind="rss",
                first_dt=first_dt,
                last_dt=last_dt,
                count=count,
                summary=clean_text(row["summary"] or "")[:180],
                score=score,
            )
        )
    return items


def load_items(output_root: Path, dates: list[str]) -> list[RawItem]:
    items: list[RawItem] = []
    for date in dates:
        items.extend(load_hotlist_items(output_root, date))
        items.extend(load_rss_items(output_root, date))
    return items


def match_topic(text: str) -> str | None:
    for definition in TRACK_DEFINITIONS:
        matched = True
        for group in definition["patterns"]:
            if not re.search(group, text, re.I):
                matched = False
                break
        if matched:
            return definition["id"]
    return None


def key_for(item: RawItem) -> str:
    topic = match_topic(item.title)
    if topic:
        return topic
    return normalize_title(item.title)


def cluster_events(items: Iterable[RawItem]) -> list[Event]:
    clusters: list[Event] = []
    keys: list[str] = []
    bucket_to_indices: dict[str, list[int]] = {}
    topic_ids = {definition["id"] for definition in TRACK_DEFINITIONS}
    for item in sorted(items, key=lambda it: it.score, reverse=True):
        key = key_for(item)
        bucket = bucket_key(item, key)
        chosen = None
        candidates = bucket_to_indices.get(bucket, [])
        for idx in candidates:
            existing_key = keys[idx]
            if key == existing_key:
                chosen = idx
                break
            if key not in topic_ids and existing_key not in topic_ids:
                if similar(key, existing_key):
                    chosen = idx
                    break
        if chosen is None and key in topic_ids:
            for idx, existing_key in enumerate(keys):
                if key == existing_key:
                    chosen = idx
                    break
        if chosen is None:
            event = Event(key=key)
            event.add(item)
            clusters.append(event)
            keys.append(key)
            bucket_to_indices.setdefault(bucket, []).append(len(clusters) - 1)
        else:
            clusters[chosen].add(item)
            if len(key) > len(keys[chosen]) and not match_topic(item.title):
                keys[chosen] = key
    for event in clusters:
        finalize_event(event)
    return sorted(clusters, key=lambda event: event.score, reverse=True)


def bucket_key(item: RawItem, key: str) -> str:
    topic = match_topic(item.title)
    if topic:
        return f"topic:{topic}"
    hits = term_hits(item.title, IMPORTANT_TERMS)
    if hits:
        return f"term:{hits[0].lower()}"
    domains = sorted(classify_domains(item.title))
    if domains:
        return f"domain:{domains[0]}"
    return f"text:{key[:8]}"


def candidate_item(item: RawItem) -> bool:
    if is_digest_title(item.title) and important_score(item.title) < 20:
        return False
    if water_score(item.title) >= 14 and important_score(item.title) < 8:
        return False
    if item.score >= 30:
        return True
    if item.best_rank is not None and item.best_rank <= 12 and water_score(item.title) < 10:
        return True
    if important_score(item.title) >= 8:
        return True
    return False


def dimension_score(event: Event) -> dict[str, int]:
    text = " ".join(item.title for item in event.items[:12])
    top_rank = event.best_rank or 99
    source_bonus = min(22, event.source_count * 5 + len(event.source_types) * 4)
    day_bonus = min(18, len(event.dates) * 6)
    important = important_score(text)
    follow_terms = len(term_hits(text, FOLLOWUP_TERMS)) * 6
    novelty_terms = len(term_hits(text, NOVELTY_TERMS)) * 9
    contrast_terms = len(term_hits(text, CONTRAST_TERMS)) * 8
    proximity_terms = len(term_hits(text, PROXIMITY_TERMS)) * 8
    trust = int(sum(item.source_trust for item in event.items) / max(1, len(event.items)))
    rank_boost = 18 if top_rank <= 3 else 12 if top_rank <= 10 else 5 if top_rank <= 30 else 0
    impact = min(100, 24 + important + source_bonus + rank_boost)
    credibility = min(100, trust + source_bonus)
    novelty = min(100, 26 + novelty_terms + contrast_terms + (10 if max(item.date for item in event.items) == event.last_dt.strftime("%Y-%m-%d") else 0))
    followup = min(100, 18 + follow_terms + day_bonus + (18 if match_topic(event.title) else 0))
    proximity = min(100, 20 + proximity_terms + (12 if "finance" in event.domains else 0) + (10 if "policy" in event.domains else 0))
    return {
        "影响范围": impact,
        "可信度": credibility,
        "新颖性": novelty,
        "后续变量": followup,
        "接近性": proximity,
    }


def finalize_event(event: Event) -> None:
    event.water_score = max(water_score(item.title) for item in event.items)
    event.category = primary_category_for_event(event)
    event.dimensions = dimension_score(event)
    base = max(item.score for item in event.items)
    multi_source = min(26, (event.source_count - 1) * 9 + (len(event.source_types) - 1) * 5)
    persistence = min(26, math.log1p(event.count) * 5 + len(event.dates) * 5)
    dimension_part = (
        event.dimensions["影响范围"] * 0.28
        + event.dimensions["可信度"] * 0.18
        + event.dimensions["新颖性"] * 0.16
        + event.dimensions["后续变量"] * 0.24
        + event.dimensions["接近性"] * 0.14
    )
    event.score = round(base + multi_source + persistence + dimension_part * 0.72 - event.water_score * 1.7, 1)
    event.reasons = reason_chips(event)
    event.next_watch = next_watch(event)
    event.status = trend_status(event)
    event.trackable = is_trackable(event)


def reason_chips(event: Event) -> list[str]:
    reasons: list[str] = []
    if event.source_count >= 3:
        reasons.append("多源确认")
    elif event.source_count >= 2:
        reasons.append("双源确认")
    if any(item.source_tier in {"权威媒体", "财经专业源"} for item in event.items):
        reasons.append("高可信来源")
    if event.best_rank and event.best_rank <= 5:
        reasons.append("高位热榜")
    if len(event.dates) >= 2:
        reasons.append("持续发酵")
    if event.dimensions.get("后续变量", 0) >= 65:
        reasons.append("后续变量强")
    if event.category in {"finance", "international", "military"}:
        reasons.append(f"{category_label(event.category)}相关")
    if event.dimensions.get("接近性", 0) >= 62:
        reasons.append("与中国/资产相关")
    if not reasons:
        reasons.append("综合评分入选")
    return reasons[:5]


def next_watch(event: Event) -> str:
    matched = match_topic(" ".join(item.title for item in event.items))
    for definition in TRACK_DEFINITIONS:
        if definition["id"] == matched:
            return definition["watch"]
    text = " ".join(item.title for item in event.items[:10])
    if term_hits(text, ("调查", "通报", "事故", "死亡")):
        return "看官方调查结论、责任划分、伤亡/处置数据是否更新。"
    if term_hits(text, ("关税", "谈判", "制裁", "协议")):
        return "看下一轮谈判口径、执行清单和市场是否重新定价。"
    if term_hits(text, ("黄金", "白银", "原油", "美股", "A股", "港股")):
        return "看价格是否突破关键区间，以及利率、美元和风险偏好是否同步变化。"
    if term_hits(text, ("发布", "模型", "芯片", "AI", "财报")):
        return "看产品能力、商业化节奏、供应链和竞品反应。"
    return "看是否出现新增事实、权威回应或跨来源复现。"


def trend_status(event: Event) -> str:
    date_counts: dict[str, int] = {}
    for item in event.items:
        date_counts[item.date] = date_counts.get(item.date, 0) + item.count
    dates = sorted(date_counts)
    if len(dates) >= 2 and date_counts[dates[-1]] > date_counts[dates[-2]]:
        return "升温"
    if len(event.dates) >= 3:
        return "持续"
    if event.source_count >= 3:
        return "扩散"
    return "新近"


def is_trackable(event: Event) -> bool:
    topic = event_topic(event)
    if topic:
        return event.score >= 68 and event.water_score < 16
    serious_domain = event.category in {"policy", "international", "military", "finance", "tech", "society"}
    if event.category in {"entertainment", "sports"}:
        return False
    continuity = len(event.dates) >= 2 and event.source_count >= 2 and event.dimensions.get("后续变量", 0) >= 66
    impact = event.dimensions.get("影响范围", 0) >= 58
    not_water = event.water_score < 14 or important_score(event.title) >= 12
    return serious_domain and continuity and impact and not_water and event.score >= 88


def selected_events(events: list[Event], date: str | None = None, limit: int = 50) -> list[Event]:
    chosen = []
    for event in events:
        if date and date not in event.dates:
            continue
        if event.water_score >= 14 and important_score(event.title) < 12:
            continue
        serious = event.category in {"policy", "international", "military", "finance", "tech", "society"}
        if not serious and event.score < 90:
            continue
        if event.category == "general" and (event.score < 120 or event.source_count < 4):
            continue
        if event.score < 58 and not event.trackable:
            continue
        chosen.append(event)
    return sorted(chosen, key=lambda event: event.score, reverse=True)[:limit]


def representative_items(event: Event, limit: int = 8) -> list[RawItem]:
    seen: set[str] = set()
    items: list[RawItem] = []
    for item in sorted(event.items, key=lambda it: (it.last_dt, it.score), reverse=True):
        key = normalize_title(item.title)
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def e(value: object) -> str:
    return html.escape(str(value or ""))


def layout(title: str, body: str, active: str = "home") -> str:
    nav = [
        ("home", "/", "领域分类"),
        ("daily", "/daily/", "日报"),
        ("timeline", "/timeline/", "热点脉络"),
        ("raw", "/hotlists/", "来源热榜"),
    ]
    nav_html = "".join(
        f'<a class="{ "active" if key == active else "" }" href="{href}">{label}</a>'
        for key, href, label in nav
    )
    back_html = '<a class="back-link" href="/" onclick="history.back(); return false;">返回</a>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #fff;
      --text: #172033;
      --muted: #627086;
      --line: #e3e8f0;
      --blue: #185abc;
      --red: #d43c33;
      --green: #178260;
      --amber: #9a6400;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    }}
    a {{ color: var(--blue); text-decoration: none; }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 20px 18px 56px; }}
    .topbar {{
      position: sticky; top: 0; z-index: 30;
      display: flex; align-items: center; justify-content: space-between; gap: 18px;
      padding: 14px 0 16px; border-bottom: 1px solid var(--line);
      background: rgba(245, 247, 251, .96); backdrop-filter: blur(12px);
    }}
    .top-left {{ display: flex; align-items: center; min-width: 0; }}
    .back-link {{
      display: inline-flex; align-items: center; justify-content: center; min-height: 34px;
      padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px;
      background: #fff; color: #435066; font-size: 13px; font-weight: 750;
      white-space: nowrap;
    }}
    .brand {{ font-size: 22px; font-weight: 850; letter-spacing: 0; color: #20293a; }}
    .brand span {{ color: #0d8fc8; }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    nav a {{
      padding: 9px 12px; border: 1px solid var(--line); border-radius: 6px;
      background: #fff; color: #435066; font-size: 14px; font-weight: 650;
    }}
    nav a.active {{ color: #fff; background: #20293a; border-color: #20293a; }}
    .page-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin: 24px 0 18px; }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.2; letter-spacing: 0; }}
    .sub {{ margin-top: 8px; color: var(--muted); line-height: 1.7; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 18px; align-items: start; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .event-card {{ padding: 16px 18px; margin-bottom: 12px; }}
    .event-top {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }}
    .score {{ min-width: 56px; text-align: center; padding: 5px 8px; border-radius: 6px; background: #eef4ff; color: var(--blue); font-weight: 800; }}
    .status {{ padding: 4px 8px; border-radius: 999px; background: #fff2df; color: var(--amber); font-size: 12px; font-weight: 750; }}
    h2 {{ margin: 0; font-size: 19px; line-height: 1.45; letter-spacing: 0; }}
    .meta {{ margin-top: 8px; color: var(--muted); font-size: 13px; line-height: 1.6; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
    .chip {{ display: inline-flex; align-items: center; min-height: 23px; padding: 3px 8px; border-radius: 999px; background: #f1f4f8; color: #48566b; font-size: 12px; font-weight: 650; }}
    .chip.reason {{ background: #ecf8f3; color: var(--green); }}
    .chip.domain {{ background: #fff1ef; color: var(--red); }}
    .watch {{ margin-top: 12px; padding: 10px 12px; border-left: 3px solid var(--blue); background: #f6f9ff; color: #3b4b65; font-size: 13px; line-height: 1.65; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; color: var(--blue); font-size: 13px; font-weight: 700; }}
    .related {{ margin: 8px 0 0; padding: 0; list-style: none; }}
    .related li {{ padding: 8px 0; border-top: 1px solid var(--line); font-size: 13px; line-height: 1.55; color: #3c485c; }}
    .related span {{ color: var(--muted); }}
    .side {{ padding: 16px; position: sticky; top: 14px; }}
    .side-stack {{ padding: 0; background: transparent; border: 0; display: grid; gap: 14px; }}
    .side-info {{ padding: 16px; }}
    .side h3 {{ margin: 0 0 10px; font-size: 15px; }}
    .source-row {{ display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); font-size: 13px; }}
    .controls {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .control-btn {{ border: 1px solid var(--line); background: #fff; color: #42506a; border-radius: 6px; padding: 8px 10px; cursor: pointer; font-weight: 700; }}
    .control-btn.active {{ background: #20293a; color: #fff; border-color: #20293a; }}
    .category-page-head {{ display: block; }}
    .category-page-head .controls {{ margin-top: 14px; }}
    .category-controls {{ display: grid; grid-template-columns: repeat(9, minmax(0, 1fr)); gap: 8px; width: 100%; }}
    .category-controls .control-btn {{ min-width: 0; white-space: nowrap; }}
    .market-board {{ margin: 0; }}
    .market-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 12px; }}
    .market-head h2 {{ margin: 0; font-size: 18px; line-height: 1.35; }}
    .market-note {{ margin-top: 7px; color: var(--muted); font-size: 12px; line-height: 1.6; }}
    .market-refresh {{
      flex: 0 0 auto; min-height: 32px; padding: 6px 10px; border: 1px solid var(--line);
      border-radius: 6px; background: #fff; color: #42506a; font-weight: 750; cursor: pointer;
    }}
    .market-grid {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
    .market-card {{
      display: grid; grid-template-columns: minmax(0, 1fr) 160px; gap: 14px;
      min-height: 118px; padding: 13px 14px; border: 1px solid var(--line);
      border-radius: 8px; background: #fbfcff; color: inherit; text-decoration: none;
    }}
    .market-card:hover {{ border-color: #c9d5e6; background: #fff; }}
    .market-card:focus-visible {{ outline: 2px solid rgba(24, 90, 188, .35); outline-offset: 2px; }}
    .market-name {{ display: flex; align-items: baseline; gap: 8px; min-width: 0; }}
    .market-name strong {{ font-size: 15px; line-height: 1.35; }}
    .market-name span {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
    .market-price {{ margin-top: 9px; font-size: 28px; line-height: 1.1; font-weight: 850; font-variant-numeric: tabular-nums; letter-spacing: 0; }}
    .market-change {{ margin-top: 7px; font-size: 13px; font-weight: 800; font-variant-numeric: tabular-nums; }}
    .market-change.up {{ color: var(--red); }}
    .market-change.down {{ color: var(--green); }}
    .market-change.flat {{ color: var(--muted); }}
    .market-card-meta {{ margin-top: 7px; color: var(--muted); font-size: 12px; line-height: 1.55; }}
    .market-spark {{ width: 160px; height: 74px; align-self: center; overflow: visible; }}
    .market-axis {{ stroke: #e2e8f0; stroke-width: 1; }}
    .market-line {{ fill: none; stroke: #185abc; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }}
    .market-area {{ fill: rgba(24, 90, 188, .08); }}
    .market-empty {{ display: grid; place-items: center; height: 74px; color: var(--muted); font-size: 12px; }}
    .dim {{ display: grid; grid-template-columns: 76px 1fr 34px; gap: 8px; align-items: center; margin-top: 7px; font-size: 12px; color: var(--muted); }}
    .bar {{ height: 6px; border-radius: 999px; background: #e9edf4; overflow: hidden; }}
    .bar i {{ display: block; height: 100%; background: #5067a8; }}
    .section-title {{ margin: 28px 0 12px; font-size: 20px; }}
    .timeline {{ margin-top: 18px; padding: 2px 0 4px; }}
    .timeline-date {{ margin: 18px 0 8px; color: #627086; font-size: 14px; font-weight: 700; }}
    .timeline-node {{ display: grid; grid-template-columns: 72px 22px minmax(0, 1fr); gap: 7px; align-items: stretch; }}
    .timeline-time {{ padding-top: 12px; color: #142033; font-size: 17px; font-weight: 850; font-variant-numeric: tabular-nums; }}
    .timeline-rail {{ position: relative; min-height: 100%; }}
    .timeline-rail::before {{ content: ""; position: absolute; top: 0; bottom: -12px; left: 10px; width: 2px; background: #d9e0ea; }}
    .timeline-node:last-child .timeline-rail::before {{ bottom: 18px; }}
    .timeline-dot {{ position: absolute; top: 18px; left: 6px; width: 10px; height: 10px; border-radius: 999px; background: #86dbc4; border: 2px solid #f5f7fb; z-index: 1; }}
    .timeline-card {{ min-width: 0; margin-bottom: 12px; padding: 13px 15px; background: #fff; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 1px 2px rgba(17, 24, 39, .04); }}
    .timeline-card-top {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }}
    .timeline-source {{ color: #65748a; font-size: 12px; }}
    .timeline-score {{ min-width: 34px; text-align: center; padding: 3px 8px; border: 1px solid #bfe4fb; border-radius: 999px; background: #eefaff; color: #0a86bd; font-size: 12px; font-weight: 800; }}
    .timeline-card h3 {{ margin: 0; font-size: 16px; line-height: 1.5; letter-spacing: 0; }}
    .timeline-card .meta {{ margin-top: 8px; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .daily-section {{ margin-top: 18px; }}
    .category-summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .summary-cell {{ padding: 12px; background: #fff; border: 1px solid var(--line); border-radius: 8px; }}
    .summary-cell strong {{ display: block; font-size: 21px; line-height: 1.1; }}
    .summary-cell span {{ display: block; margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .category-section {{ margin-bottom: 22px; }}
    .category-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 0 0 8px; }}
    .category-head h2 {{ font-size: 20px; }}
    .category-count {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
    .classified-list {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .classified-row {{ display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 12px; padding: 12px 14px; border-top: 1px solid var(--line); }}
    .classified-row:first-child {{ border-top: 0; }}
    .row-rank {{ width: 34px; height: 28px; border-radius: 6px; background: #eef4ff; color: var(--blue); display: grid; place-items: center; font-size: 13px; font-weight: 850; }}
    .classified-row h3 {{ margin: 0; font-size: 16px; line-height: 1.45; letter-spacing: 0; }}
    .row-meta {{ margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.55; }}
    .row-tags {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }}
    .mini-chip {{ display: inline-flex; align-items: center; min-height: 20px; padding: 2px 7px; border-radius: 999px; background: #f1f4f8; color: #536177; font-size: 12px; font-weight: 650; }}
    .mini-chip.hot {{ background: #fff1ef; color: var(--red); }}
    .source-controls {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; width: 100%; margin-top: 14px; }}
    .source-section {{ margin-bottom: 22px; }}
    .source-item-list {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .source-item {{ display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 12px; padding: 12px 14px; border-top: 1px solid var(--line); }}
    .source-item:first-child {{ border-top: 0; }}
    .source-rank {{ width: 34px; height: 28px; border-radius: 6px; background: #eef4ff; color: var(--blue); display: grid; place-items: center; font-size: 13px; font-weight: 850; }}
    .source-item h3 {{ margin: 0; font-size: 16px; line-height: 1.45; letter-spacing: 0; }}
    .source-item-meta {{ margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.55; }}
    @media (max-width: 860px) {{
      .topbar, .page-head {{ align-items: flex-start; flex-direction: column; }}
      .topbar {{ position: static; }}
      nav {{ width: 100%; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px; }}
      nav a {{ white-space: nowrap; }}
      .grid, .two-col {{ grid-template-columns: 1fr; }}
      .side {{ position: static; }}
      .timeline-node {{ grid-template-columns: 54px 18px minmax(0, 1fr); gap: 6px; }}
      .timeline-time {{ font-size: 14px; }}
      .timeline-card {{ padding: 12px; }}
      .category-summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .category-controls {{ display: flex; flex-wrap: nowrap; overflow-x: auto; padding-bottom: 2px; }}
      .category-controls .control-btn {{ flex: 0 0 auto; }}
      .source-controls {{ display: flex; flex-wrap: nowrap; overflow-x: auto; padding-bottom: 2px; }}
      .source-controls .control-btn {{ flex: 0 0 auto; }}
      .market-card {{ grid-template-columns: 1fr; }}
      .market-spark {{ width: 100%; }}
      .classified-row {{ grid-template-columns: 40px minmax(0, 1fr); }}
      .source-item {{ grid-template-columns: 40px minmax(0, 1fr); }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="top-left"><a class="brand" href="/">NEWS<span>HOT</span></a></div>
      <nav>{back_html}{nav_html}</nav>
    </div>
    {body}
  </div>
</body>
</html>
"""


def event_card(event: Event, rank: int | None = None, compact: bool = False) -> str:
    category = event.category or "general"
    category_html = f'<span class="chip domain">{e(category_label(category))}</span>'
    reasons_html = "".join(f'<span class="chip reason">{e(reason)}</span>' for reason in event.reasons)
    source_types = " / ".join(sorted(event.source_types))
    time_span = f"{event.first_dt.strftime('%m-%d %H:%M')} 到 {event.last_dt.strftime('%m-%d %H:%M')}"
    title = e(event.title)
    title_html = f'<a href="{e(event.url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if event.url else title
    related_items = representative_items(event, 6 if compact else 10)
    related = "".join(
        f'<li><a href="{e(item.url)}" target="_blank" rel="noopener noreferrer">{e(item.title)}</a>'
        f'<br><span>{e(item.source_name)} · {item.last_dt.strftime("%m-%d %H:%M")}'
        f'{(" · 最高第" + str(item.best_rank)) if item.best_rank else ""}</span></li>'
        for item in related_items
    )
    dims = "".join(
        f'<div class="dim"><span>{e(name)}</span><div class="bar"><i style="width:{value}%"></i></div><b>{value}</b></div>'
        for name, value in event.dimensions.items()
    )
    rank_html = f'<span class="score">#{rank}</span>' if rank else f'<span class="score">{event.score:.0f}</span>'
    watch = "" if compact else f'<div class="watch">后续看什么：{e(event.next_watch)}</div>'
    return f"""
    <article class="event-card panel" data-score="{event.score:.1f}" data-category="{e(category)}">
      <div class="event-top"><div>{rank_html}</div><span class="status">{e(event.status)}</span></div>
      <h2>{title_html}</h2>
      <div class="meta">来自 {event.source_count} 个来源、{len(event.source_types)} 类信源 · {e(source_types)} · {time_span} · 累计 {event.count} 次出现</div>
      <div class="chips">{category_html}{reasons_html}</div>
      {watch}
      <details>
        <summary>展开相关报道和评分维度</summary>
        <div>{dims}</div>
        <ul class="related">{related}</ul>
      </details>
    </article>
    """


def home_event_row(event: Event, rank: int) -> str:
    title = e(event.title)
    title_html = f'<a href="{e(event.url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if event.url else title
    source_names = sorted({item.source_name for item in event.items})
    source_preview = " / ".join(source_names[:4]) + (" 等" if len(source_names) > 4 else "")
    first = event.first_dt.strftime("%H:%M")
    last = event.last_dt.strftime("%H:%M")
    rank_text = f"最高第 {event.best_rank}" if event.best_rank else "RSS"
    related_items = representative_items(event, 6)
    related = "".join(
        f'<li><a href="{e(item.url)}" target="_blank" rel="noopener noreferrer">{e(item.title)}</a>'
        f'<br><span>{e(item.source_name)} · {item.last_dt.strftime("%H:%M")}'
        f'{(" · 最高第" + str(item.best_rank)) if item.best_rank else ""}</span></li>'
        for item in related_items
    )
    details = (
        f'<details><summary>相关报道</summary><ul class="related">{related}</ul></details>'
        if len(related_items) > 1
        else ""
    )
    tags = []
    if event.category != "general":
        tags.append(f'<span class="mini-chip hot">{e(category_label(event.category))}</span>')
    tags.extend([
        f'<span class="mini-chip">{e(rank_text)}</span>',
        f'<span class="mini-chip">{event.source_count} 源</span>',
    ])
    if event.status:
        tags.append(f'<span class="mini-chip">{e(event.status)}</span>')
    return f"""
    <article class="classified-row" data-category="{e(event.category)}" data-score="{event.score:.1f}">
      <div class="row-rank">#{rank}</div>
      <div>
        <h3>{title_html}</h3>
        <div class="row-meta">{e(source_preview)} · {first} - {last} · 累计 {event.count} 次出现</div>
        <div class="row-tags">{''.join(tags)}</div>
        {details}
      </div>
    </article>
    """


def market_watch_html() -> str:
    return """
    <section class="market-board" id="market-board">
      <div class="market-head">
        <div>
          <h2>自选行情</h2>
          <div class="market-note" id="market-note">平安银行、现货黄金；约 60 秒刷新一次，行情仅作观察。</div>
        </div>
        <button class="market-refresh" id="market-refresh" type="button">刷新</button>
      </div>
      <div class="market-grid" id="market-grid">
        <a class="market-card" data-quote-card="pingan_bank" href="https://gu.qq.com/sz000001/gp" target="_blank" rel="noopener noreferrer">
          <div>
            <div class="market-name"><strong>平安银行</strong><span>sz000001</span></div>
            <div class="market-price">--</div>
            <div class="market-change flat">等待行情</div>
            <div class="market-card-meta">深交所 · --</div>
          </div>
          <svg class="market-spark" viewBox="0 0 160 74" role="img" aria-label="平安银行价格波动">
            <line class="market-axis" x1="4" y1="56" x2="156" y2="56"></line>
            <path class="market-area"></path>
            <path class="market-line"></path>
          </svg>
        </a>
        <a class="market-card" data-quote-card="gold_spot" href="https://gu.qq.com/hf/hf_XAU" target="_blank" rel="noopener noreferrer">
          <div>
            <div class="market-name"><strong>现货黄金</strong><span>hf_XAU</span></div>
            <div class="market-price">--</div>
            <div class="market-change flat">等待行情</div>
            <div class="market-card-meta">贵金属 · --</div>
          </div>
          <svg class="market-spark" viewBox="0 0 160 74" role="img" aria-label="现货黄金价格波动">
            <line class="market-axis" x1="4" y1="56" x2="156" y2="56"></line>
            <path class="market-area"></path>
            <path class="market-line"></path>
          </svg>
        </a>
      </div>
    </section>
    <script>
      const marketCards = Array.from(document.querySelectorAll('[data-quote-card]'));
      const marketNote = document.getElementById('market-note');
      const marketRefresh = document.getElementById('market-refresh');

      function marketNumber(value, digits = 2) {
        const number = Number(value);
        if (!Number.isFinite(number)) return '--';
        return number.toFixed(digits);
      }

      function marketSign(value) {
        const number = Number(value);
        if (!Number.isFinite(number) || Math.abs(number) < 0.0001) return '';
        return number > 0 ? '+' : '';
      }

      function marketClass(value) {
        const number = Number(value);
        if (!Number.isFinite(number) || Math.abs(number) < 0.0001) return 'flat';
        return number > 0 ? 'up' : 'down';
      }

      function marketTime(value) {
        if (!value) return '--';
        const text = String(value);
        return text.length > 11 ? text.slice(5, 16) : text;
      }

      function drawSparkline(svg, history) {
        const line = svg.querySelector('.market-line');
        const area = svg.querySelector('.market-area');
        const points = (history || [])
          .map(point => Number(point.price))
          .filter(value => Number.isFinite(value));
        if (points.length < 2) {
          line.setAttribute('d', 'M4 37 L156 37');
          area.setAttribute('d', '');
          return;
        }
        const min = Math.min(...points);
        const max = Math.max(...points);
        const range = max - min || 1;
        const step = 152 / (points.length - 1);
        const coords = points.map((value, index) => {
          const x = 4 + index * step;
          const y = 60 - ((value - min) / range) * 46;
          return [x, y];
        });
        const path = coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
        const areaPath = `${path} L156 64 L4 64 Z`;
        line.setAttribute('d', path);
        area.setAttribute('d', areaPath);
      }

      function renderMarketItem(item) {
        const card = document.querySelector(`[data-quote-card="${item.id}"]`);
        if (!card) return;
        const price = Number(item.price);
        const change = Number(item.change);
        const percent = Number(item.percent);
        const changeClass = marketClass(change || percent);
        card.querySelector('.market-name strong').textContent = item.name || item.id;
        card.querySelector('.market-name span').textContent = item.symbol || '';
        if (item.url) {
          card.href = item.url;
          card.setAttribute('aria-label', `打开${item.name || item.id}行情来源`);
        }
        card.querySelector('.market-price').textContent = `${marketNumber(price)}${item.unit ? ' ' + item.unit : ''}`;
        const changeText = `${marketSign(change)}${marketNumber(change)} / ${marketSign(percent)}${marketNumber(percent)}%`;
        const changeEl = card.querySelector('.market-change');
        changeEl.className = `market-change ${changeClass}`;
        changeEl.textContent = changeText;
        card.querySelector('.market-card-meta').textContent = `${item.market || '行情'} · ${marketTime(item.time)} · ${item.source || '行情源'}`;
        drawSparkline(card.querySelector('.market-spark'), item.history);
      }

      async function loadMarketQuotes() {
        try {
          const response = await fetch(`/markets/quotes.json?ts=${Date.now()}`, { cache: 'no-store' });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = await response.json();
          (payload.items || []).forEach(renderMarketItem);
          const generatedAt = payload.generated_at ? payload.generated_at.replace('T', ' ').slice(5, 16) : '--';
          const errors = payload.errors && payload.errors.length ? `；部分行情暂不可用：${payload.errors.join('、')}` : '';
          marketNote.textContent = `自选行情 · ${payload.source || '行情源'} · 更新于 ${generatedAt}${errors}`;
        } catch (error) {
          marketNote.textContent = `自选行情暂未拿到数据，后台生成后会自动显示。`;
        }
      }

      marketRefresh.addEventListener('click', loadMarketQuotes);
      loadMarketQuotes();
      window.setInterval(loadMarketQuotes, 60000);
    </script>
    """


def render_home(events: list[Event], latest_date: str, output_root: Path, top: int) -> None:
    today_events = [event for event in events if latest_date in event.dates]
    today_events = sorted(today_events, key=lambda event: event.score, reverse=True)
    category_groups: dict[str, list[Event]] = {category: [] for category in CATEGORY_ORDER}
    for event in today_events:
        category_groups.setdefault(event.category or "general", []).append(event)

    total_raw_items = sum(len(event.items) for event in today_events)
    total_sources = len({item.source_id for event in today_events for item in event.items})
    source_counts: dict[str, int] = {}
    for event in today_events:
        for item in event.items:
            source_counts[item.source_name] = source_counts.get(item.source_name, 0) + 1

    controls = [
        f'<button class="control-btn active" data-category="all">全部 <span>{len(today_events)}</span></button>'
    ]
    for category in VISIBLE_CATEGORY_ORDER:
        count = len(category_groups.get(category, []))
        controls.append(
            f'<button class="control-btn" data-category="{e(category)}">{e(category_label(category))} <span>{count}</span></button>'
        )

    sections = []
    all_rows = "\n".join(home_event_row(event, idx + 1) for idx, event in enumerate(today_events))
    sections.append(
        f"""
        <section class="category-section" data-category-section="all">
          <div class="category-head">
            <h2>全部</h2>
            <span class="category-count">{len(today_events)} 个去重事件，包含未归入具体领域的内容</span>
          </div>
          <div class="classified-list">{all_rows}</div>
        </section>
        """
    )
    for category in VISIBLE_CATEGORY_ORDER:
        group = category_groups.get(category, [])
        if not group:
            continue
        rows = "\n".join(home_event_row(event, idx + 1) for idx, event in enumerate(group))
        sections.append(
            f"""
            <section class="category-section" data-category-section="{e(category)}">
              <div class="category-head">
                <h2>{e(category_label(category))}</h2>
                <span class="category-count">{len(group)} 个去重事件</span>
              </div>
              <div class="classified-list">{rows}</div>
            </section>
            """
        )

    sources_html = "".join(
        f'<div class="source-row"><span>{e(name)}</span><strong>{count}</strong></div>'
        for name, count in sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:16]
    )
    body = f"""
    <div class="page-head category-page-head">
      <div>
        <h1>今日领域分类</h1>
        <div class="sub">{e(latest_date)} · “全部”包含所有去重事件；领域按钮只展示已明确归类的内容，不再单独设置“综合”。</div>
      </div>
      <div class="controls category-controls" id="category-controls">
        {''.join(controls)}
      </div>
    </div>
    <div class="category-summary">
      <div class="summary-cell"><strong>{len(today_events)}</strong><span>去重事件</span></div>
      <div class="summary-cell"><strong>{total_raw_items}</strong><span>热榜原始条目</span></div>
      <div class="summary-cell"><strong>{total_sources}</strong><span>来源标签</span></div>
      <div class="summary-cell"><strong>{sum(1 for category in VISIBLE_CATEGORY_ORDER if category_groups.get(category))}</strong><span>有内容的领域</span></div>
    </div>
    <div class="grid">
      <main id="event-list">{''.join(sections)}</main>
      <aside class="side side-stack">
        {market_watch_html()}
        <section class="panel side-info">
          <h3>分类口径</h3>
          <div class="sub">每条内容只进一个主分类。未能明确判断领域的内容不单独成类，只保留在“全部”中。</div>
          <h3 class="section-title">来源数量</h3>
          {sources_html}
        </section>
      </aside>
    </div>
    <script>
      const list = document.getElementById('event-list');
      const buttons = Array.from(document.querySelectorAll('.control-btn'));
      function applyCategory(name) {{
        const sections = Array.from(list.querySelectorAll('.category-section'));
        sections.forEach(section => {{
          const visible = section.dataset.categorySection === name;
          section.style.display = visible ? '' : 'none';
        }});
        buttons.forEach(btn => btn.classList.toggle('active', btn.dataset.category === name));
        localStorage.setItem('newshot-category', name);
      }}
      buttons.forEach(btn => btn.addEventListener('click', () => applyCategory(btn.dataset.category)));
      const initialCategory = localStorage.getItem('newshot-category') || 'all';
      applyCategory(buttons.some(btn => btn.dataset.category === initialCategory) ? initialCategory : 'all');
    </script>
    """
    page = layout("今日领域分类", body, "home")
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "index.html").write_text(page, encoding="utf-8")
    curated_dir = output_root / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    (curated_dir / "index.html").write_text(page, encoding="utf-8")


def archive_entries(events: list[Event], dates: list[str]) -> list[tuple[str, str]]:
    entries = []
    for date in sorted(dates, reverse=True):
        chosen = selected_events(events, date, 1)
        entries.append((date, chosen[0].title if chosen else "日报生成中"))
    return entries


def source_item_row(item: RawItem, rank: int) -> str:
    title = e(item.title)
    title_html = f'<a href="{e(item.url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if item.url else title
    category = primary_category_for_text(item.title)
    if category == "general":
        category = SOURCE_CATEGORY_HINTS.get(item.source_id, "general")
    rank_text = f"热榜第 {item.best_rank}" if item.best_rank else "RSS"
    time_span = f"{item.first_dt.strftime('%H:%M')} - {item.last_dt.strftime('%H:%M')}"
    tags = []
    if category != "general":
        tags.append(f'<span class="mini-chip hot">{e(category_label(category))}</span>')
    tags.extend(
        [
            f'<span class="mini-chip">{e(rank_text)}</span>',
            f'<span class="mini-chip">{item.count} 次</span>',
        ]
    )
    return f"""
    <article class="source-item">
      <div class="source-rank">#{rank}</div>
      <div>
        <h3>{title_html}</h3>
        <div class="source-item-meta">{e(item.source_name)} · {time_span}</div>
        <div class="row-tags">{''.join(tags)}</div>
      </div>
    </article>
    """


def render_source_hotlists(output_root: Path, latest_date: str) -> None:
    items = [item for item in load_hotlist_items(output_root, latest_date) if item.title]
    items = sorted(items, key=lambda item: (item.score, item.count, item.last_dt), reverse=True)
    by_source: dict[str, list[RawItem]] = {}
    source_names: dict[str, str] = {}
    for item in items:
        by_source.setdefault(item.source_id, []).append(item)
        source_names[item.source_id] = item.source_name

    source_order = sorted(by_source, key=lambda source_id: (-len(by_source[source_id]), source_names[source_id]))
    controls = [f'<button class="control-btn active" data-source="all">全部 <span>{len(items)}</span></button>']
    for source_id in source_order:
        controls.append(
            f'<button class="control-btn" data-source="{e(source_id)}">{e(source_names[source_id])} <span>{len(by_source[source_id])}</span></button>'
        )

    all_rows = "\n".join(source_item_row(item, idx + 1) for idx, item in enumerate(items))
    sections = [
        f"""
        <section class="source-section" data-source-section="all">
          <div class="category-head">
            <h2>全部来源</h2>
            <span class="category-count">{len(items)} 条热榜条目</span>
          </div>
          <div class="source-item-list">{all_rows}</div>
        </section>
        """
    ]
    for source_id in source_order:
        source_items = by_source[source_id]
        rows = "\n".join(source_item_row(item, idx + 1) for idx, item in enumerate(source_items))
        sections.append(
            f"""
            <section class="source-section" data-source-section="{e(source_id)}">
              <div class="category-head">
                <h2>{e(source_names[source_id])}</h2>
                <span class="category-count">{len(source_items)} 条热榜条目</span>
              </div>
              <div class="source-item-list">{rows}</div>
            </section>
            """
        )

    source_rows = "".join(
        f'<div class="source-row"><span>{e(source_names[source_id])}</span><strong>{len(by_source[source_id])}</strong></div>'
        for source_id in source_order
    )
    first_dt = min((item.first_dt for item in items), default=None)
    last_dt = max((item.last_dt for item in items), default=None)
    time_range = f"{first_dt.strftime('%H:%M')} - {last_dt.strftime('%H:%M')}" if first_dt and last_dt else "-"
    body = f"""
    <!-- NEWSHOT_SOURCE_PAGE -->
    <div class="page-head category-page-head">
      <div>
        <h1>来源热榜</h1>
        <div class="sub">{e(latest_date)} · 按来源查看原始热榜条目，保留各平台排名和出现次数。</div>
      </div>
      <div class="controls source-controls" id="source-controls">
        {''.join(controls)}
      </div>
    </div>
    <div class="category-summary">
      <div class="summary-cell"><strong>{len(items)}</strong><span>热榜条目</span></div>
      <div class="summary-cell"><strong>{len(source_order)}</strong><span>来源标签</span></div>
      <div class="summary-cell"><strong>{time_range}</strong><span>采集时间</span></div>
      <div class="summary-cell"><strong>{sum(1 for item in items if item.best_rank and item.best_rank <= 5)}</strong><span>高位热榜</span></div>
    </div>
    <div class="grid">
      <main id="source-list">{''.join(sections)}</main>
      <aside class="side panel">
        <h3>来源数量</h3>
        <div class="sub">这里展示的是原始来源榜单，不做精选筛除；需要精选和分类时看“领域分类”。</div>
        <h3 class="section-title">平台分布</h3>
        {source_rows}
      </aside>
    </div>
    <script>
      const sourceList = document.getElementById('source-list');
      const sourceButtons = Array.from(document.querySelectorAll('.control-btn'));
      function applySource(name) {{
        const sections = Array.from(sourceList.querySelectorAll('.source-section'));
        sections.forEach(section => {{
          section.style.display = section.dataset.sourceSection === name ? '' : 'none';
        }});
        sourceButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.source === name));
        localStorage.setItem('newshot-source', name);
      }}
      sourceButtons.forEach(btn => btn.addEventListener('click', () => applySource(btn.dataset.source)));
      const initialSource = localStorage.getItem('newshot-source') || 'all';
      applySource(sourceButtons.some(btn => btn.dataset.source === initialSource) ? initialSource : 'all');
    </script>
    """
    page = layout("来源热榜", body, "raw")
    hotlists_dir = output_root / "hotlists"
    latest_dir = output_root / "html" / "latest"
    hotlists_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    (hotlists_dir / "index.html").write_text(page, encoding="utf-8")
    (latest_dir / "current.html").write_text(page, encoding="utf-8")


def render_daily(events: list[Event], dates: list[str], latest_date: str, output_root: Path, top: int) -> None:
    daily_dir = output_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_entries(events, dates)
    archive_html = "".join(
        f'<a class="chip" href="/daily/{e(date)}.html">{e(date)} · {e(title[:22])}</a>'
        for date, title in archive
    )
    for date in dates:
        day_events = selected_events(events, date, top)
        must = day_events[:10]
        backup = day_events[10:top]
        must_html = "\n".join(event_card(event, idx + 1) for idx, event in enumerate(must))
        backup_html = "\n".join(event_card(event, idx + 11, compact=True) for idx, event in enumerate(backup))
        body = f"""
        <div class="page-head">
          <div>
            <h1>{e(date)} 新闻日报</h1>
            <div class="sub">10 条必看 + {max(0, top - 10)} 条备查。每条都给入选理由和后续观察点。</div>
          </div>
        </div>
        <div class="chips">{archive_html}</div>
        <section class="daily-section">
          <h2 class="section-title">必看 10 条</h2>
          {must_html or '<div class="panel event-card">今天还没有足够的精选事件。</div>'}
        </section>
        <section class="daily-section">
          <h2 class="section-title">备查 40 条</h2>
          {backup_html}
        </section>
        """
        page = layout(f"{date} 新闻日报", body, "daily")
        (daily_dir / f"{date}.html").write_text(page, encoding="utf-8")
        if date == latest_date:
            (daily_dir / "index.html").write_text(page, encoding="utf-8")
            (daily_dir / "latest.md").write_text(render_daily_markdown(date, must, backup), encoding="utf-8")


def render_daily_markdown(date: str, must: list[Event], backup: list[Event]) -> str:
    lines = [f"# {date} 新闻日报", "", "## 必看 10 条"]
    for idx, event in enumerate(must, 1):
        lines.append(f"{idx}. {event.title}")
        lines.append(f"   - 入选理由：{'、'.join(event.reasons)}")
        lines.append(f"   - 后续看什么：{event.next_watch}")
    lines.append("")
    lines.append("## 备查")
    for idx, event in enumerate(backup, 11):
        lines.append(f"{idx}. {event.title}")
    lines.append("")
    return "\n".join(lines)


def render_timeline(events: list[Event], output_root: Path) -> None:
    timeline_dir = output_root / "timeline"
    timeline_dir.mkdir(parents=True, exist_ok=True)
    tracks = [event for event in events if event.trackable]
    tracks = sorted(tracks, key=lambda event: (event.last_dt, event.score), reverse=True)
    deduped_tracks: list[Event] = []
    seen_tracks: set[str] = set()
    for event in tracks:
        name = track_name(event)
        if name in seen_tracks:
            continue
        seen_tracks.add(name)
        deduped_tracks.append(event)
        if len(deduped_tracks) >= 8:
            break
    tracks = deduped_tracks
    sections = []
    for event in tracks:
        nodes = timeline_nodes(event)
        nodes_html_parts: list[str] = []
        last_date_label = ""
        for node in nodes:
            date_label = cn_date(node.last_dt)
            if date_label != last_date_label:
                nodes_html_parts.append(f'<div class="timeline-date">{e(date_label)}</div>')
                last_date_label = date_label
            score_label = str(round(node.score))
            rank_label = f"最高第{node.best_rank}" if node.best_rank else "RSS"
            nodes_html_parts.append(
                f"""
                <div class="timeline-node">
                  <div class="timeline-time">{node.last_dt.strftime('%H:%M')}</div>
                  <div class="timeline-rail"><span class="timeline-dot"></span></div>
                  <article class="timeline-card">
                    <div class="timeline-card-top">
                      <span class="timeline-source">{e(node.source_name)} · {e(node.source_tier)}</span>
                      <span class="timeline-score">{e(score_label)}</span>
                    </div>
                    <h3><a href="{e(node.url)}" target="_blank" rel="noopener noreferrer">{e(node.title)}</a></h3>
                    <div class="meta">{e(node.source_type)} · {e(rank_label)} · 累计 {node.count} 次出现</div>
                  </article>
                </div>
                """
            )
        nodes_html = "".join(nodes_html_parts)
        origin = nodes[-1].title if nodes else event.title
        latest = nodes[0].title if nodes else event.title
        sections.append(
            f"""
            <section class="panel event-card">
              <div class="event-top"><span class="score">{event.score:.0f}</span><span class="status">{e(event.status)}</span></div>
              <h2>{e(track_name(event))}</h2>
              <div class="meta">最新进展：{e(latest)}<br>较早节点：{e(origin)}</div>
              <div class="watch">可能走向：{e(event.next_watch)}</div>
              <div class="chips">{''.join(f'<span class="chip reason">{e(r)}</span>' for r in event.reasons)}</div>
              <div class="timeline">{nodes_html}</div>
            </section>
            """
        )
    body = f"""
    <div class="page-head">
      <div>
        <h1>热点脉络</h1>
        <div class="sub">这里只追踪长期变化、影响足够大、且有后续变量的事件。普通热搜不会进入这里。</div>
      </div>
    </div>
    {''.join(sections) if sections else '<div class="panel event-card">暂时没有达到追踪阈值的大事件。</div>'}
    """
    (timeline_dir / "index.html").write_text(layout("热点脉络", body, "timeline"), encoding="utf-8")


def cn_date(value: datetime) -> str:
    return f"{value.month}月{value.day}日"


def track_name(event: Event) -> str:
    matched = event_topic(event)
    for definition in TRACK_DEFINITIONS:
        if definition["id"] == matched or definition["id"] == event.key:
            return definition["name"]
    return event.title


def event_topic(event: Event) -> str | None:
    if event.key in {definition["id"] for definition in TRACK_DEFINITIONS}:
        return event.key
    for item in event.items:
        topic = match_topic(item.title)
        if topic:
            return topic
    return None


def timeline_nodes(event: Event) -> list[RawItem]:
    by_day: dict[str, list[RawItem]] = {}
    for item in event.items:
        by_day.setdefault(item.date, []).append(item)
    nodes: list[RawItem] = []
    for date in sorted(by_day, reverse=True):
        day_items = sorted(by_day[date], key=lambda item: (item.last_dt, item.score, item.count), reverse=True)
        for item in day_items[:3]:
            if not any(similar(normalize_title(item.title), normalize_title(existing.title)) for existing in nodes):
                nodes.append(item)
                break
    if len(nodes) < 4:
        for item in sorted(event.items, key=lambda item: item.last_dt, reverse=True):
            if not any(similar(normalize_title(item.title), normalize_title(existing.title)) for existing in nodes):
                nodes.append(item)
            if len(nodes) >= 8:
                break
    return sorted(nodes, key=lambda item: item.last_dt, reverse=True)[:12]


def preserve_raw_latest(output_root: Path) -> None:
    raw = output_root / "html" / "latest" / "current.html"
    hotlists = output_root / "hotlists"
    if raw.exists():
        hotlists.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(raw, hotlists / "index.html")


def generate(
    output_root: Path,
    lookback_days: int,
    home_top: int,
    daily_top: int,
    skip_daily: bool = False,
    hotlists_only: bool = False,
) -> None:
    dates = recent_db_dates(output_root, lookback_days)
    if not dates:
        raise SystemExit("No TrendRadar database files found.")
    latest_date = dates[-1]
    if hotlists_only:
        render_source_hotlists(output_root, latest_date)
        print(f"[hotlists] generated source page for {latest_date}")
        return
    home_items = [item for item in load_hotlist_items(output_root, latest_date) if item.title]
    home_events = cluster_events(home_items)
    items = [item for item in load_items(output_root, dates) if candidate_item(item)]
    events = cluster_events(items)
    preserve_raw_latest(output_root)
    render_home(home_events, latest_date, output_root, home_top)
    if not skip_daily:
        render_daily(events, dates, latest_date, output_root, daily_top)
    render_timeline(events, output_root)
    render_source_hotlists(output_root, latest_date)
    daily_status = "skipped" if skip_daily else str(daily_top)
    print(f"[curated] generated home=all:{len(home_events)}, daily={daily_status}, events={len(events)}, dates={len(dates)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--home-top", type=int, default=24)
    parser.add_argument("--daily-top", type=int, default=50)
    parser.add_argument("--skip-daily", action="store_true", help="Do not overwrite /daily/ pages")
    parser.add_argument("--hotlists-only", action="store_true", help="Only regenerate the styled source hotlist page")
    args = parser.parse_args()
    generate(Path(args.output_root), args.lookback_days, args.home_top, args.daily_top, args.skip_daily, args.hotlists_only)


if __name__ == "__main__":
    main()
