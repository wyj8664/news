"""Editorial eligibility, not ingestion filtering or a source/brand blacklist."""
import re


# Decide the editorial intent before sector words such as tourism, AI or film.
# Job advertisements are not industry hiring trends or corporate expansion news.
HIRING_NOTICE = re.compile(
    r'(?:招聘|招录|招考).{0,18}(?:公告|启事|简章|岗位表|报名通知|面试通知|笔试通知)|'
    r'(?:招聘|招录|招考).{0,12}(?:资格审查|体检|拟聘|拟录用).{0,8}(?:通知|公告|公示)|'
    r'(?:拟聘用|拟录用|拟聘任)人员.{0,8}公示'
)
CIVIC_FILM_FORMAT = re.compile(r'微电影|宣传片|宣传视频|公益广告|普法短剧|普法微短剧|政务短剧')
CIVIC_PRODUCER = re.compile(r'法院|法庭|检察院|司法局|司法厅|政法委|公安局|公安厅|派出所|消防|纪委|政府|政务|党组织')
CIVIC_COMMUNICATION = re.compile(r'普法|法治宣传|反诈|禁毒|解纷|化解.{0,6}纠纷|纠纷调解|基层治理|警示教育|廉政|党建|文明宣传|宣传教育')
EXPLICIT_CIVIC_FORMAT = re.compile(r'普法(?:微电影|短剧|微短剧)|政务(?:宣传片|短剧)|反诈宣传片|公益宣传片')
# A film's subject can be courts/anti-fraud without making its commercial
# release or a copyright/industry-policy development a government promo.
CULTURAL_INDUSTRY_FACT = re.compile(
    r'票房|分账|付费|票务|商业发行|院线(?:发行|上映)|制作成本|'
    r'版权|著作权|判赔|产业政策|扶持政策|扶持资金|产业补贴|专项资金'
)


def rejection_reason(title, summary=''):
    text = re.sub(r'<[^>]+>', '', str(title)).strip()
    if HIRING_NOTICE.search(text):
        return 'routine_hiring_notice'
    opening = re.sub(r'<[^>]+>', '', str(summary))[:300]
    if (CIVIC_FILM_FORMAT.search(text) and not CULTURAL_INDUSTRY_FACT.search(text)
            and (EXPLICIT_CIVIC_FORMAT.search(text)
                 or CIVIC_PRODUCER.search(text + ' ' + opening) and CIVIC_COMMUNICATION.search(text + ' ' + opening))):
        return 'civic_publicity_not_industry'
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
