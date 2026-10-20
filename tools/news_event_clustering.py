"""Event-level grouping for the all-source home, separate from topic timelines."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import math
import re
import urllib.parse

from news_freshness import china_time
from news_fact_normalization import canonical_quantities, reporting_period
from news_fact_identity import fact_identity, same_fact, policy_conflict, dated_policy, same_policy

PUBLISHERS = {
    "cls-hot": "cls", "cls-telegraph": "cls", "jin10-flash": "jin10", "jin10-all": "jin10",
    "jiemian-flash": "jiemian", "jiemian": "jiemian",
    "wallstreetcn-hot": "wallstreetcn", "weibo-account:1875034341": "wallstreetcn",
    "sina-finance": "sina-finance", "weibo-account:1638782947": "sina-finance",
    "weibo-account:1651428902": "21jingji", "weibo-account:1885454921": "lanjing",
    "weibo-account:5993531560": "jwview",
    "weibo-account:1646051850": "beike-finance", "weibo-account:1650111241": "cbnews",
    "weibo-account:1641561812": "eeo", "weibo-account:5044281310": "thepaper",
    "weibo-account:1664176597": "stcn", "weibo-account:1926909715": "yicai",
    "weibo-account:2011593450": "cnautonews",
    "weibo-account:7399555658": "redstar-capital", "weibo-account:1684012053": "caijing",
    "weibo-account:7209515062": "star-daily",
}
PUBLISHER_NAMES = {"cls": "财联社", "jin10": "金十数据", "jiemian": "界面新闻", "wallstreetcn": "华尔街见闻",
                   "sina-finance": "新浪财经", "21jingji": "21世纪经济报道", "lanjing": "蓝鲸新闻", "jwview": "中新经纬",
                   "beike-finance": "贝壳财经", "cbnews": "中国经营报", "eeo": "经济观察报", "thepaper": "澎湃新闻",
                   "stcn": "证券时报", "yicai": "第一财经日报", "cnautonews": "中国汽车报",
                   "redstar-capital": "红星资本局", "caijing": "财经", "star-daily": "科创板日报"}
ALIASES = {"nvidia": "英伟达", "人工智能": "ai", "英伟达公司": "英伟达", "美国总统特朗普": "特朗普",
           "推出": "发布", "亮相": "发布", "上线": "发布", "首排": "第一排", "通胀": "通货膨胀",
           "斯维诗": "swisse", "启幕": "开幕", "终止": "取消", "退出": "取消"}
ENTITIES = tuple("英伟达 苹果 华为 小米 特斯拉 比亚迪 阿里巴巴 腾讯 百度 京东 美团 字节跳动 宇树 三星 台积电 微软 亚马逊 谷歌 蚂蚁 大麦 央视 国务院 商务部 财政部 证监会 美联储 央行 中国银行 工商银行 招商银行 中国 美国 加拿大 日本 韩国 伊朗 以色列 俄罗斯 乌克兰 欧盟 纽约 曼哈顿 圣彼得堡 特朗普 普京 黄金 白银 原油 人民币".split())
ACTION_RULES = {
    "release": r"发布|推出|上线|亮相|开源|上市新车",
    "funding": r"融资|募资|获投|筹资",
    "earnings": r"财报|季度业绩|营收|营业收入|净利润|盈利|亏损",
    "ipo": r"ipo|招股|递表|聆讯|挂牌上市",
    "quality": r"召回|超标|检出|不合格|质量问题",
    "expose": r"曝光|骗局|造假|诈骗",
    "acquisition": r"收购|并购|合并",
    "lawsuit": r"起诉|诉讼|判决|裁决|获刑",
    "election": r"大选|当选|选举|投票",
    "attack": r"袭击|空袭|发射导弹|轰炸",
    "ceasefire": r"停火|休战",
    "rates-up": r"加息|上调利率",
    "rates-down": r"降息|下调利率",
    "ticket": r"门票|票价|售票|演唱会",
    "explosion": r"爆炸|爆破",
    "fire": r"火灾|起火",
    "shooting": r"枪击|开枪",
    "earthquake": r"地震",
    "crash": r"坠毁|坠机",
    "diplomatic-call": r"敦促|呼吁|谴责",
    "buyback": r"回购",
    "cancel": r"取消|退出|终止",
    "opening": r"开幕|启幕|开业|首秀",
    "insurance": r"纳入.{0,8}医保",
    "approval": r"获准|获批|批准",
    "presale": r"预售",
}
STOP_WORDS = tuple("财联社 金十数据 据报道 报道 表示 宣布 消息 今日 今天 昨日 昨天 最新 快讯 记者 经济报道 新闻 发布 通报 回应 官方 公司 集团 新一代 新款 价格 相关 进行 已经 目前".split())


def publisher_id(item) -> str:
    return PUBLISHERS.get(item.source_id, item.source_id)


def publisher_name(item) -> str:
    return PUBLISHER_NAMES.get(publisher_id(item), item.source_name)


def safe_link(url: str) -> str:
    try:
        value = urllib.parse.urlsplit(url)
        return url if value.scheme in {"http", "https"} and value.hostname and not value.username and not value.password else ""
    except ValueError:
        return ""


def normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(text)).lower()
    text = canonical_quantities(text)
    text = re.sub(r'归属于(?:上市公司股东|母公司(?:股东|所有者)?)(?:的)?净利润', '归母净利润', text)
    text = text.replace('营业收入', '营收')
    text = re.sub(r'净利(?=同比|增长|增加|下降|减少|亏损|[：:\d])', '净利润', text)
    text = re.sub(r"^(?:财联社|金十数据)\d{1,2}月\d{1,2}日电[，,:：]?", "", text)
    for old, new in ALIASES.items():
        text = text.replace(old, new)
    text = re.sub(r"日本(?:jasm|熊本)二厂", "日本熊本二厂", text)
    text = re.sub(r"(?:拟以)?人民币(?=\d)", "", text)
    text = re.sub(r"(?<=回购)拟以|(?<=提议)拟以", "", text)
    text = re.sub(r"swisse(?:swisse)+", "swisse", text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def headline_views(item) -> list[str]:
    """Derived matching views only; never overwrite a publisher's original text."""
    raw = re.sub(r"<[^>]+>", "", str(item.title))
    summary = re.sub(r"<[^>]+>", "", str(item.summary or ""))[:800]
    # Column labels aren't a factual headline; keep the actual text after them.
    raw = re.sub(r'^【(?:研选[•·・｜|]?行业数据|盘中宝|电报解读|公告全知道|机构调研)】\s*', '', raw)
    def strip(value):
        value = re.sub(r"#([^#]+)#", r"\1", value)
        return re.sub(r"^(?:鲸视频|视频|快讯|独家|一探)[：:]", "", value).strip()
    brackets = [strip(value) for value in re.findall(r"【([^】]{6,180})】", raw)]
    preface = strip(raw.split("【", 1)[0]) if "【" in raw else ""
    factual_preface = 6 <= len(preface) <= 35 and any(re.search(p, preface.lower()) for p in ACTION_RULES.values())
    primary = preface if factual_preface else (brackets[0] if brackets else strip(raw.split("\n", 1)[0][:180]))
    views = [primary, *brackets]
    primary_grams = grams(normalize(primary), 3)
    for text in (raw, summary):
        for value in re.findall(r"#([^#\n]{6,80})#", text[:240]):
            # Topic/promotional tags are recall metadata, not proof of one event.
            # A tag must carry a factual action AND agree with the main headline.
            factual = any(re.search(p, value.lower()) for p in ACTION_RULES.values()) or re.search(r"死亡|被查|封禁|粉丝|铅超标|老鼠仓|高坠", value)
            if factual and len(primary_grams & grams(normalize(value), 3)) >= 2:
                views.append(value)
    views.append(strip(raw.split("\n", 1)[0][:180]))
    result = []
    for value in views:
        value = re.sub(r"^(?:鲸视频|视频|快讯)[：:]", "", value).strip()
        if value and value not in result:
            result.append(value)
    return result[:6]


def clean_headline(item) -> str:
    values = headline_views(item)
    return values[0]


def article_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        # A generic homepage/search URL does not identify a report.
        if parsed.path in {"", "/"} or re.search(r"^/(?:search|hot|weibo|S|q|quotes?|stocks?|symbols?|topics?|subjects?)(?:/|$)", parsed.path):
            return ""
        query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query)
                 if not key.lower().startswith(("utm_", "spm", "from", "source"))]
        return urllib.parse.urlunsplit(("https", parsed.hostname.lower().removeprefix("www."), parsed.path.rstrip("/"), urllib.parse.urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def deduplicate_reports(items: list) -> list:
    groups = {}
    for item in items:
        url = article_url(item.url)
        stamp = str(item.published_dt or item.first_dt)
        key = (publisher_id(item), url or getattr(item, "native_id", "") or (normalize(item.title), stamp))
        groups.setdefault(key, []).append(item)
    result = []
    for values in groups.values():
        chosen = max(values, key=lambda item: (bool(item.published_dt and item.publication_source),
                     item.kind != "hotlist", len(item.summary), item.collected_dt or item.last_dt, item.source_trust))
        published = [item for item in values if item.published_dt is not None and item.publication_source]
        original = min(published, key=lambda item: china_time(item.published_dt)) if published else chosen
        captures = [china_time(item.collected_dt or item.last_dt).replace(tzinfo=None) for item in values]
        result.append(replace(chosen, count=1, published_dt=original.published_dt,
                              publication_source=original.publication_source, collected_dt=max(captures)))
    return result


def grams(text: str, size: int = 2) -> set[str]:
    return {text[index:index + size] for index in range(max(0, len(text) - size + 1))}


@dataclass
class Features:
    text: str
    entities: set[str]
    actions: set[str]
    objects: set[str]
    tokens: set[str]
    quarter: set[str]
    models: set[str]
    url: str
    digest: bool
    quake_time: set[str]
    views: tuple[str, ...] = ()
    products: set[str] = None
    subject: str = ""
    dates: set[str] = None
    issues: set[str] = None
    fact: dict | None = None
    published_day: str = ''


def features(item) -> Features:
    text = normalize(clean_headline(item))
    views = tuple(dict.fromkeys(normalize(value) for value in headline_views(item)))
    actors = {entity.lower() for entity in ENTITIES if entity.lower() in text}
    if "特朗普" in actors:
        actors.add("美国")
    if "普京" in actors:
        actors.add("俄罗斯")
    actors.update(token.lower() for token in re.findall(r"[a-zA-Z]{3,}", text) if token.lower() not in {"ipo", "etf", "gdp", "cpi", "ppm"})
    for view in views:
        actors.update(entity.lower() for entity in ENTITIES if entity.lower() in view)
        actors.update(token for token in re.findall(r"[a-z]{3,}", view) if token not in {"ipo", "etf", "gdp", "cpi", "ppm"})
    prefix = re.match(r"^([\u4e00-\u9fff]{2,8}?)(?=发布|通报|宣布|回应|召回|获|被)", text)
    if prefix:
        subject = re.sub(r"(?:深夜|凌晨|今日|近日|最新|正式|再次|突然)$", "", prefix[1])
        if subject not in {"客服", "官方", "平台", "记者", "公司", "企业", "目前", "有关部门"}:
            actors.add(subject)
    subject = ""
    # Native datelines must not hide the actual company before a colon. This
    # matters more after numeric/unit normalization makes boilerplate similar.
    factual_headline = re.sub(r"^(?:财联社|金十数据)\d{1,2}月\d{1,2}日电[，,:：\s]*", "", clean_headline(item))
    colon = re.match(r"^([\u4e00-\u9fffA-Za-z0-9·]{2,14})[：:]", factual_headline)
    if colon and colon[1] not in {"鲸视频", "快讯", "据报道", "官方", "客服", "金十整理"}:
        subject = normalize(colon[1])
        actors.add(subject)
    if not subject:
        # Short Weibo titles often omit a colon: "某公司拟回购...". A named
        # financial actor is stronger evidence than similar boilerplate amounts.
        corporate = re.match(r"^([\u4e00-\u9fffA-Za-z·]{2,10}?)(?=(?:拟|将|计划)?回购|董事长|董事会)", factual_headline)
        if corporate and corporate[1] not in {"公司", "集团", "董事长", "科技股", "上市公司"}:
            subject = normalize(corporate[1])
            actors.add(subject)
    # Specific institutions/venues are stronger than a guessed title prefix.
    for name in re.findall(r"[\u4e00-\u9fff]{2,8}(?:大学|歌剧院|研究院|人民法院|中学)", text):
        actors.add(name)
    actions = {name for name, pattern in ACTION_RULES.items() if re.search(pattern, text)}
    objects = text
    for word in sorted(actors | set(STOP_WORDS), key=len, reverse=True):
        objects = objects.replace(word.lower(), "")
    for pattern in ACTION_RULES.values():
        objects = re.sub(pattern, "", objects)
    object_grams = grams(objects)
    for view in views:
        for word in sorted(actors | set(STOP_WORDS), key=len, reverse=True):
            view = view.replace(word.lower(), "")
        for pattern in ACTION_RULES.values():
            view = re.sub(pattern, "", view)
        object_grams.update(grams(view))
    # Short social headlines often omit the product/place named in their body.
    # Use a bounded opening only as object evidence, never as a broad topic key.
    if len(text) < 20 and item.summary and actions:
        context = normalize(item.summary[:240])
        for word in sorted(actors | set(STOP_WORDS), key=len, reverse=True):
            context = context.replace(word.lower(), "")
        objects += context
        object_grams.update(grams(context))
    quarters = set(re.findall(r"q([1-4])", text))
    quarters.update(str("一二三四".index(value) + 1) for value in re.findall(r"第?([一二三四])季", text))
    models = set(re.findall(r"(?:gpt|iphone|claude|qwen|deepseek)[-_]?[0-9]+(?:\.[0-9]+)?", text))
    digest = bool(re.search(r"早报|晚报|要闻速递|新闻汇总|局势跟踪|每日.{0,6}要闻", text))
    quake_time = set(re.findall(r"\d{1,2}月\d{1,2}日\d{1,2}时\d{1,2}分", text)) if "earthquake" in actions else set()
    products = set(re.findall(r"抗糖丸|褪黑素|蛋白粉|奶粉|智能手机|手机|ai芯片|人工智能模型|ai模型|固态电池", text))
    dates = set(re.findall(r"\d{1,2}月\d{1,2}日", text))
    issue_context = text + normalize(str(item.summary or "")[:500])
    issues = {key for key, pattern in {"lead": r"铅(?:含量)?(?:超标|超出)", "mercury": r"汞(?:含量)?(?:超标|超出)",
              "cadmium": r"镉(?:含量)?(?:超标|超出)", "salmonella": r"沙门氏菌"}.items() if re.search(pattern, issue_context)}
    if re.search(r"下架|召回|回应|立案|处罚|被罚", text) and any(actor in issue_context for actor in actors):
        products.update(re.findall(r"抗糖丸|褪黑素|蛋白粉|奶粉", issue_context))
        if products and issues:
            actions.add("quality")
    published = getattr(item,'published_dt',None) or getattr(item,'first_dt',None)
    return Features(text, actors, actions, object_grams, grams(text) | grams(text, 3), quarters, models,
                    article_url(item.url), digest, quake_time, views, products, subject, dates, issues,
                    dated_policy(fact_identity(clean_headline(item), str(item.summary or '')[:700]),
                                 china_time(published).strftime('%Y-%m-%d') if published else ''),
                    china_time(published).strftime('%Y-%m-%d') if published else '')


def same_event(left: Features, right: Features, idf: dict[str, float]) -> bool:
    if left.url and left.url == right.url:
        return True
    if left.quarter and right.quarter and not left.quarter.intersection(right.quarter):
        return False
    if left.models and right.models and not left.models.intersection(right.models):
        return False
    if left.text == right.text:
        return len(left.text) >= 6
    if left.digest or right.digest:
        return False
    if left.quake_time and right.quake_time and not left.quake_time.intersection(right.quake_time):
        return False
    if left.products and right.products and not left.products.intersection(right.products):
        return False
    if left.entities and right.entities and not left.entities.intersection(right.entities):
        return False
    if left.actions and right.actions and not left.actions.intersection(right.actions):
        return False
    shared = left.tokens & right.tokens
    if not shared:
        return False
    dot = sum(idf.get(token, 1) ** 2 for token in shared)
    denom = math.sqrt(sum(idf.get(token, 1) ** 2 for token in left.tokens) * sum(idf.get(token, 1) ** 2 for token in right.tokens))
    cosine = dot / denom if denom else 0
    sequence = SequenceMatcher(None, left.text, right.text, autojunk=False).ratio()
    objects = left.objects & right.objects
    # Same company + same verb is insufficient: require a shared concrete object.
    if left.entities & right.entities and left.actions & right.actions and not objects:
        return False
    short, long = sorted((left.text, right.text), key=len)
    if len(short) >= 12 and short in long and len(short) / len(long) >= 0.4:
        return True
    if sequence >= 0.79 and cosine >= 0.46:
        return True
    if cosine >= 0.65 and sequence >= 0.43:
        return True
    if left.entities & right.entities and left.actions & right.actions and len(objects) >= 2:
        coverage = len(objects) / max(1, min(len(left.objects), len(right.objects)))
        if len(objects) >= 3 and coverage >= 0.4 and sequence >= 0.55:
            return True
        return cosine >= 0.32 and sequence >= 0.36
    return False


def event_match(left: Features, right: Features) -> tuple[float, str]:
    """Conservative multi-view matching for the durable registry, not a topic key."""
    if policy_conflict(left.text,right.text):
        return 0, 'conflicting-policy-object'
    if left.fact and right.fact:
        a,b=left.fact,right.fact
        if a['family']==b['family']=='policy':
            return (.98,'same-regulatory-document') if same_policy(a,b) else (0,'conflicting-regulatory-document')
        if a['family']==b['family'] and a['family'] in {'market','speech','usage-response','press-release'} and left.published_day and right.published_day and left.published_day!=right.published_day:
            return 0, 'different-publication-day'
        if same_fact(a,b) is True:
            return .98, 'same-typed-fact'
        if a['family']==b['family']=='market' and all(a.get(k)==b.get(k) for k in ('actor','contract','phase')):
            return .94, 'same-market-series'
        if a['family']==b['family'] and a['actor']!=b['actor']:
            return 0, 'conflicting-fact-actor'
    if 'earnings' in left.actions & right.actions:
        a_year, a_period = reporting_period(left.text)
        b_year, b_period = reporting_period(right.text)
        if (a_year and b_year and a_year != b_year) or (a_period and b_period and a_period != b_period):
            return 0, 'conflicting-reporting-period'
    if left.quarter and right.quarter and left.quarter.isdisjoint(right.quarter):
        return 0, "conflicting-period"
    if left.models and right.models and left.models.isdisjoint(right.models):
        return 0, "conflicting-model"
    if left.products and right.products and left.products.isdisjoint(right.products):
        return 0, "conflicting-product"
    if left.issues and right.issues and left.issues.isdisjoint(right.issues):
        return 0, "conflicting-quality-issue"
    if left.quake_time and right.quake_time and left.quake_time.isdisjoint(right.quake_time):
        return 0, "conflicting-quake-time"
    if left.digest or right.digest:
        return (1, "same-digest") if left.text == right.text else (0, "multi-event-digest")
    shared_entities = left.entities & right.entities
    weak = {"中国", "美国", "日本", "俄罗斯", "人民币", "黄金", "白银", "原油", "ai"}
    specific = shared_entities - weak
    left_specific, right_specific = left.entities - weak, right.entities - weak
    if left_specific and right_specific and left_specific.isdisjoint(right_specific):
        return 0, "conflicting-specific-entity"
    latin_left = {entity for entity in left.entities if re.fullmatch(r"[a-z]{3,}", entity)}
    latin_right = {entity for entity in right.entities if re.fullmatch(r"[a-z]{3,}", entity)}
    if specific and latin_left and latin_right and latin_left.isdisjoint(latin_right):
        return 0, "conflicting-named-counterparty"
    if left.subject and right.subject and left.subject != right.subject:
        return 0, "conflicting-subject"
    if left.actions and right.actions and left.actions.isdisjoint(right.actions):
        # A quality incident may be followed by investigation/recall/response;
        # these are resolved into progress nodes, not separate events.
        return 0, "different-action"
    best = (0.0, "insufficient-evidence")
    if specific and left.products and right.products and left.products & right.products and "quality" in left.actions & right.actions:
        best = (.91, "same-product-quality-incident")
    for a in left.views or (left.text,):
        for b in right.views or (right.text,):
            if a == b and len(a) >= 6:
                return 1, "same-factual-headline"
            short, long = sorted((a, b), key=len)
            if len(short) >= 12 and short in long and len(short) / len(long) >= .30:
                return .96, "contained-factual-headline"
            ga, gb = grams(a) | grams(a, 3), grams(b) | grams(b, 3)
            common = ga & gb
            cosine = len(common) / math.sqrt(max(1, len(ga) * len(gb)))
            sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
            if sequence >= .80 and cosine >= .50:
                best = max(best, (.94, "normalized-headline"))
            if specific and cosine >= .48 and sequence >= .53 and len(left.objects & right.objects) >= 4:
                best = max(best, (.90, "subject-and-object"))
            if specific and left.actions & right.actions and cosine >= .30 and sequence >= .32 and len(left.objects & right.objects) >= 5:
                best = max(best, (.86, "subject-action-object"))
            if left.actions & right.actions and cosine >= .45 and sequence >= .48 and len(left.objects & right.objects) >= 8:
                best = max(best, (.85, "specific-object-and-action"))
    if specific and "opening" in left.actions & right.actions:
        if not left.dates or not right.dates or left.dates & right.dates:
            best = max(best, (.92, "same-venue-opening"))
    if best[0] < .85 and same_event(left, right, {}):
        best = (.85, "legacy-concrete-evidence")
    return best


def group_event_items(items: list) -> list[list]:
    ordered = sorted(items, key=lambda item: (item.published_dt or item.first_dt, item.score), reverse=True)
    prepared = [features(item) for item in ordered]
    frequencies = Counter(token for value in prepared for token in value.tokens)
    idf = {token: 1 + math.log((len(items) + 1) / (count + 1)) for token, count in frequencies.items()}
    groups, anchors = [], []
    index = {}
    for item, value in zip(ordered, prepared):
        keys = set(value.entities) | set(value.tokens)
        candidates = set()
        for key in keys:
            candidates.update(index.get(key, ()))
        chosen = None
        for group_index in sorted(candidates):
            # Compare to the group's original anchor, not any matching member;
            # a chain of loosely related reports cannot swallow an entire topic.
            if same_event(value, anchors[group_index], idf):
                chosen = group_index
                break
        if chosen is None:
            chosen = len(groups)
            groups.append([])
            anchors.append(value)
        groups[chosen].append(item)
        for key in keys:
            index.setdefault(key, set()).add(chosen)
    return groups
