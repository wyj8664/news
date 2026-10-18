"""Exact quantity normalization and bounded financial-fact scopes.

This is not title replacement or a company whitelist. Source text stays intact;
only derived matching evidence is canonicalized. Decimal avoids float rounding.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata

NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
QUANTITY = re.compile(rf"(?P<number>{NUMBER})\s*(?P<unit>亿美元|万美元|美元|亿元|万元|元|亿欧元|万欧元|欧元|个百分点|%|人死亡|人受伤|级地震)")


def decimal_text(value):
    number = Decimal(str(value).replace(',', ''))
    return '0' if not number else format(number.normalize(), 'f')


def quantity_text(text):
    text = unicodedata.normalize('NFKC', str(text)).replace('−', '-')
    # Unit elision in ranges: 5亿至8亿元 / 5亿元-8亿元 carry two amounts.
    text = re.sub(rf"({NUMBER})(亿|万)(?:元)?\s*(?:至|到|[-—–~])\s*(?:人民币)?({NUMBER})(亿|万)?元",
                  lambda m: f'{m[1]}{m[2]}元至{m[3]}{m[4] or m[2]}元', text)
    text = re.sub(rf"({NUMBER})\s*(?:至|到|[-—–~])\s*({NUMBER})%",
                  lambda m: f'{m[1]}%至{m[2]}%', text)
    # A hyphen following an amount is a range separator, not a negative sign.
    text = re.sub(r"(?<=[元%])\s*[-—–~]\s*(?=\d)", '至', text)
    return text


def canonical_quantities(text):
    def convert(match):
        number = Decimal(match['number'].replace(',', ''))
        unit = match['unit']
        if unit.endswith(('元', '美元', '欧元')):
            multiplier = Decimal('100000000') if unit.startswith('亿') else Decimal('10000') if unit.startswith('万') else Decimal(1)
            unit = unit.lstrip('亿万')
            number *= multiplier
        return decimal_text(number) + unit
    return QUANTITY.sub(convert, quantity_text(text))


def quantity_tokens(text):
    return tuple(sorted({m[0].replace(' ', '') for m in QUANTITY.finditer(canonical_quantities(text))}))


def reporting_period(text):
    text = unicodedata.normalize('NFKC', text).lower()
    year = next(iter(re.findall(r'(?<!\d)(20\d{2})年', text)), '')
    chinese = {'一':'1','二':'2','三':'3','四':'4'}
    cumulative = re.search(r'(?:前|首)([一二三四1-4])季', text)
    quarter = re.search(r'(?:第)?([一二三四1-4])季|q([1-4])', text)
    if cumulative:
        value = chinese.get(cumulative[1], cumulative[1])
        period = 'Q1' if value == '1' else 'YTDQ' + value
    elif re.search(r'上半年|半年度|中报', text):
        period = 'H1'
    elif re.search(r'下半年', text):
        period = 'H2'
    elif re.search(r'首季', text):
        period = 'Q1'
    elif quarter:
        period = 'Q' + (chinese.get(quarter[1], quarter[1]) if quarter[1] else quarter[2])
    elif re.search(r'全年|年度|年报', text):
        period = 'FY'
    else:
        period = ''
    return year, period


def financial_scope(title, summary=''):
    """Metric, reporting period, publication stage and direction are not aliases.

    A vague 'net profit' is enriched only by the leading original-source clause;
    later Q3 comparisons or outlook paragraphs do not rewrite the headline fact.
    """
    text = unicodedata.normalize('NFKC', title)
    patterns = {
        'adjusted-net-profit': r'扣(?:除)?非(?:经常性损益)?(?:后)?(?:的)?(?:归母)?净利(?:润)?',
        'parent-net-profit': r'归母净利(?:润)?|归属于(?:上市公司股东|母公司(?:股东|所有者)?)(?:的)?净利润',
        'consolidated-net-profit': r'合并净利润|含少数股东.{0,8}净利润',
        'net-profit': r'净利润|净亏损|净利(?=同比|增长|增加|下降|减少|亏损|[：:\d])',
        'operating-profit': r'营业利润', 'gross-profit': r'毛利(?:润)?(?!率)',
        'revenue': r'营业收入|营收', 'net-margin': r'净利率', 'gross-margin': r'毛利率',
    }
    metrics = {key for key, pattern in patterns.items() if re.search(pattern, text)}
    if not metrics:
        return None
    if metrics & {'adjusted-net-profit','parent-net-profit','consolidated-net-profit'}:
        metrics.discard('net-profit')
    if 'adjusted-net-profit' in metrics:
        metrics.discard('parent-net-profit')
    leading = re.split(r'[。；;]', summary[:240])[0]
    if metrics == {'net-profit'}:
        for key in ('adjusted-net-profit','parent-net-profit','consolidated-net-profit'):
            if re.search(patterns[key], leading):
                metrics = {key}
                break
    year, period = reporting_period(text)
    # Fill an omitted year only when the opening describes the same period.
    context_year, context_period = reporting_period(leading)
    if not year and period and context_period == period:
        year = context_year
    if re.search(r'修正|更正|修订', text):
        stage = 'revision'
    elif re.search(r'预计|预告|预增|预减|预盈|预亏', text):
        stage = 'forecast'
    elif re.search(r'快报', text):
        stage = 'preliminary'
    elif not re.search(r'实现|正式|年报|季报|半年报', text) and re.search(r'预计|预告', leading):
        stage = 'forecast'
    else:
        stage = 'reported'
    direction = 'increase' if re.search(r'增长|增加|上升|提升|预增', text) else 'decrease' if re.search(r'下降|减少|下滑|预减', text) else ''
    basis = 'yoy' if '同比' in text else 'qoq' if '环比' in text else ''
    polarity = 'profit' if re.search(r'扭亏为盈|盈利|预盈', text) else 'loss' if re.search(r'亏损|预亏|净亏损', text) else ''
    if not polarity and any('profit' in key for key in metrics) and re.search(r'净利润.{0,4}[+-]?\d+(?:\.\d+)?(?:亿|万)?元', text):
        polarity = 'loss' if re.search(r'净利润.{0,4}-\d', text) else 'profit'
    return {'metrics': sorted(metrics), 'year': year, 'period': period, 'stage': stage,
            'direction': direction, 'basis': basis, 'polarity': polarity}


def scopes_conflict(left, right):
    if not left or not right:
        return False
    for key in ('year','period','stage','direction','basis','polarity'):
        if left.get(key) and right.get(key) and left[key] != right[key]:
            return True
    a, b = set(left['metrics']), set(right['metrics'])
    # A headline may abbreviate attributable profit, but never adjusted profit,
    # revenue, margins or explicitly consolidated profit.
    return a != b and {tuple(sorted(a)), tuple(sorted(b))} != {('net-profit',), ('parent-net-profit',)}
