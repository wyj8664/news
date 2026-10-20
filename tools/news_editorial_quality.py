"""Editorial eligibility, not ingestion filtering or a source/brand blacklist."""
import re


def rejection_reason(title, summary=''):
    text = re.sub(r'<[^>]+>', '', str(title)).strip()
    if re.search(r'^(?:投票|互动|有奖问答)[：:]|小调查[：:]|你看过几部|你最喜欢哪|你多久|你逛过|欢迎.{0,8}(?:评论|投票|留言)',text):
        return 'interactive_not_event'
    if re.search(r'日报上新|挂单报告[：:]|市场赔率数据表|立即解锁|扫码.{0,8}(?:领取|订阅)|点击.{0,8}(?:订阅|购买)',text):
        return 'trading_service_promotion'
    if re.search(r'(?:每日|每周).{0,16}(?:动态汇总|要闻汇总|新闻汇总)|^(?:早报|晚报|晨报|午报)[：:]',text):
        return 'multi_event_digest'
    if re.fullmatch(r'(?:研选[•·・｜|]?行业数据|行业数据|盘中宝|电报解读|公告全知道|机构调研|早报|晚报|要闻速递)[\s！!。]*',text):
        return 'no_identifiable_fact'
    if re.search(r'这家(?:公司|企业)|这家.{0,12}(?:服务商|供应商)|这只.{0,6}(?:股票|标的)',text) and re.search(r'盘中宝|研选|电报解读|机构调研|受益|催化',text):
        return 'unnamed_stock_teaser'
    if re.search(r'【[^】\n]{2,30}[：:]$',text):
        return 'truncated_column_title'
    if re.fullmatch(r'.{2,12}(?:大涨|暴涨|大跌|暴跌)',text) and not re.search(r'股价|净值|基金|ETF|LOF',text,re.I):
        return 'unquantified_market_teaser'
    return ''
