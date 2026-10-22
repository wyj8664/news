"""Bounded, role-aware fact evidence; no network, models or company whitelist.

Missing auxiliary details are not novelty. Explicit primary-value, counterparty,
contract, date and stage conflicts are. Unhandled facts retain the strict legacy
matcher instead of globally weakening numeric safeguards.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from news_fact_normalization import NUMBER, canonical_quantities, decimal_text
from news_consumer_incidents import same_incident_progress


def compact(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(text)).lower())


def clean(text):
    text = unicodedata.normalize('NFKC', re.sub(r'<[^>]+>', '', str(text)))
    return re.sub(r'^(?:财联社|金十数据)\d{1,2}月\d{1,2}日电[，,:：\s]*', '', text).strip()


INSTRUMENT = r'现货黄金|现货白银|纽约期金|纽约期银|COMEX黄金|COMEX白银|WTI原油|布伦特原油|布伦特|比特币|以太坊|(?:焦煤|焦炭|铁矿石|螺纹钢|沪铜|沪铝|碳酸锂)(?:连续主力合约|主力合约|期货|现货)?'
INSTRUMENT_RE = re.compile(INSTRUMENT, re.I)
MOVE = r'(?:上涨|下跌|大涨|大跌|涨超|跌超|涨|跌|走高|走低|涨幅|跌幅|升破|突破|站上|失守|跌破)'


def market_parts(title):
    """Only split explicit multi-instrument quote clauses, never article bodies."""
    text = clean(title)
    matches = list(INSTRUMENT_RE.finditer(text))
    if len(matches) < 2:
        return [text]
    parts = [text[m.start():matches[i+1].start() if i+1 < len(matches) else len(text)].strip('，,。；; ')
             for i,m in enumerate(matches)]
    return parts if all(market_fact(p) for p in parts) else [text]


@lru_cache(maxsize=8192)
def market_fact(title):
    text = clean(title)
    # Analysis, forecasts, trading services and mixed narratives aren't ticks.
    if re.search(r'有望|预期|预测|建议|报告|押注|库存|挂单|赔率|据悉|计划|目标价|点评', text):
        return None
    matches = list(INSTRUMENT_RE.finditer(text))
    if len(matches) > 1:
        return None
    instrument = matches[0][0] if matches else ''
    if instrument and matches[0].start() > 10:
        return None
    if not instrument:
        if re.search(r'等多股|跟涨|、',text):
            return None
        stock = re.search(r'(?:^|[，,\s])([\u4e00-\u9fffA-Za-z·]{2,16}?)(?=(?:盘中)?(?:暴|大|直线)?(?:涨|跌)|扩大(?:涨|跌)幅至)', text)
        if not stock or not re.search(r'\d+(?:\.\d+)?%', text):
            return None
        instrument = stock[1]
    if not re.search(MOVE, text):
        return None
    contract = re.search(r'(?:\d{4}|\d{2}月)合约|连续主力合约|主力合约', text)
    contract = contract[0] if contract else ''
    aliases = {'纽约期金':'COMEX黄金', '纽约期银':'COMEX白银', '布伦特':'布伦特原油'}
    name = re.sub(r'连续主力合约|主力合约', '', instrument)
    name = aliases.get(name, name)
    phase = 'close' if re.search(r'收盘|收评',text) else 'open' if '开盘' in text else 'intraday'
    direction = 'down' if re.search(r'下跌|大跌|跌|走低|失守',text) else 'up'
    barrier = re.search(rf'(?:升破|突破|站上|上破|失守|跌破|下破)\s*({NUMBER})\s*(美元|元)', text)
    change = re.search(rf'(?:涨幅|跌幅|上涨|下跌|大涨|大跌|涨|跌|走低|走高)[^，,。；;\d]{{0,9}}({NUMBER})\s*%', text)
    quote = re.search(rf'(?:现报|报)\s*({NUMBER})\s*(美元|元)', text)
    if barrier:
        metric,value,unit = 'threshold', decimal_text(barrier[1]), barrier[2]
    elif change:
        metric,value,unit = 'change', decimal_text(change[1]), '%'
    elif quote:
        metric,value,unit = 'quote', decimal_text(quote[1]), quote[2]
    else:
        return None
    # Spot/futures are distinct even if they share the same metal and number.
    currency=re.search(r'美元|欧元|元',text)
    currency=currency[0] if currency else ('美元' if re.search(r'黄金|白银|期金|期银|原油|布伦特|比特币|以太坊',name,re.I) else '元')
    return dict(family='market', actor=compact(name), contract=contract, phase=phase,
                direction=direction, metric=metric, value=value, unit=unit,currency=currency,
                observed_quote=decimal_text(quote[1]) if quote else '')


def _stage(text):
    if re.search(r'完成收购|收购完成|完成增资|增资完成|正式交割|完成交割',text):
        return 'completed'
    if re.search(r'拟|计划|将(?:出资|斥资|收购|增资)|拟议',text):
        return 'proposed'
    return ''


def corporate_fact(title):
    text = canonical_quantities(clean(title))
    capital = re.search(rf'(?:增资至|增资到|注册资本(?:增加至|变更为))\s*(?:约)?({NUMBER})(美元|欧元|元)', text)
    if capital:
        actor = compact(text[:capital.start()].rstrip('：:，, '))
        if len(actor) >= 3:
            return dict(family='capital', actor=actor, value=capital[1]+capital[2], stage=_stage(text))
    if '收购' in text:
        prefix,target = text.split('收购',1)
        actor = re.split(r'将|拟|计划|斥资|出资|以|耗资|已完成|完成',prefix,maxsplit=1)[0].strip('：:，, ')
        target = re.split(r'\d+(?:\.\d+)?%|股权|股份',target,maxsplit=1)[0]
        target = target.replace('旗下','').rstrip('的')
        amount = re.findall(rf'{NUMBER}(?:美元|欧元|元)', text)
        stake = re.search(rf'({NUMBER})%\s*(?:的)?(?:股权|股份)',text)
        if len(actor)>=2 and len(target)>=4 and len(amount)==1:
            return dict(family='acquisition',actor=compact(actor),object=compact(target),
                        value=amount[-1],stake=decimal_text(stake[1]) if stake else '',stage=_stage(text))
    return None


def _grams(text, n=3):
    return {text[i:i+n] for i in range(max(0,len(text)-n+1))}


def speech_fact(title, summary=''):
    """Recover the attributed statement, not every historical fact in an article.

    Speaker + same dated publication + substantial shared assertion is required;
    a company/product name or a discussion topic alone never establishes identity.
    """
    text = clean(title) + '\n' + clean(summary)[:700]
    if not re.search(r'发文|长文|社交平台|微博|回应|辟谣|否认',text):
        return None
    person = next((m for m in re.finditer(r'(?:公关负责人|发言人|董事长兼首席执行官|董事长兼CEO|首席执行官|董事长|CEO)[\s@：:]*([\u4e00-\u9fff]{2,4}?)(?=\s|[，,:：]|发文|微博|发布|在|称|表示|就)',text,re.I)
                   if m[1] not in {'辟谣','回应','表示','否认'}),None)
    if not person:
        return None
    speaker = person[1]
    tail = text[person.end():]
    attributed = re.search(r'(?:发文(?:称|表示)?|表示|写道|称)[：:，,]?\s*(.{12,450})',tail,re.S)
    if not attributed:
        return None
    assertion = attributed[1]
    # Take actual attributed quotes when present, else its leading assertion.
    assertion = re.split(r'此外|另据|回顾|此前曾',assertion,maxsplit=1)[0]
    quotes = re.findall(r'[“"]([^”"\n]{12,300})[”"]',assertion)
    views = quotes + [re.split(r'此外|另据|回顾|此前曾',assertion,maxsplit=1)[0][:350]]
    views = [compact(re.sub(r'\d{1,2}月\d{1,2}日|\s+', '',v)) for v in views]
    views = [v for v in views if len(v)>=12]
    if not views:
        return None
    dates = re.findall(r'\d{1,2}月\d{1,2}日',text)
    stance = 'denial' if re.search(r'辟谣|否认|不实|不靠谱', title+' '+assertion[:200]) else 'admission' if re.search(r'承认|确认属实',title) else ''
    return dict(family='speech',actor=speaker,date=dates[0] if dates else '',stance=stance,views=views[:4])


def usage_response(title, summary=''):
    """Numerical assertion after a response colon; don't compare rumour numbers.

    E.g. 'X denies 55% ...: only 10% capacity used for video generation'. This
    is role parsing, not a brand alias: the named respondent, metric, use and
    asserted share must all agree.
    """
    text=clean(title)
    actor=re.search(r'([\u4e00-\u9fffA-Za-z]{2,12}?)(?:公关|方|发言人)?(?:回应|辟谣|否认)',text)
    if not actor:
        actor=re.search(r'([\u4e00-\u9fffA-Za-z]{2,10}?)公关负责人',text+'\n'+clean(summary))
    if not actor:
        return None
    assertion=re.split(r'[：:]',text)[-1] if re.search(r'[：:]',text) else ''
    pattern=rf'({NUMBER})\s*%\s*(算力|产能|收入|营收|费用|用户)?[^%]{{0,12}}?(?:用于|投入|投向|来自)(.{{3,50}})'
    match=re.search(pattern,assertion)
    if not match:
        attributed=re.search(r'(?:发文(?:称|表示)?|表示|写道|称)[：:，,]?(.{12,400})',clean(summary),re.S)
        assertion=attributed[1] if attributed else ''
        match=re.search(pattern,assertion)
    metric=re.search(r'算力|产能|收入|营收|费用|用户',assertion)
    if not match or not metric:
        return None
    use=re.split(r'[，,。；;】#]',match[3])[0]
    use=re.sub(r'模型(?:的)?(?:训练和推理)?|的训练和推理|训练和推理','',use).strip()
    if re.search(r'全文|…|\.\.\.',use):
        return None
    return dict(family='usage-response',actor=actor[1],metric=metric[0],object=compact(use),value=decimal_text(match[1]))


def press_fact(title,summary):
    text=clean(summary)[:700]
    attribution=re.search(r'(?:^|[，,。；;\s#】])([\u4e00-\u9fffA-Za-z]{2,18}?)(?:发布声明|发表声明)[#\s]*(?:称|表示)?[：:，,]?(.{15,400})',text,re.S)
    if not attribution:
        return None
    dates=re.findall(r'\d{1,2}月\d{1,2}日',text[:attribution.start()+1])
    assertion=attribution[2]
    return dict(family='press-release',actor=attribution[1],date=dates[-1] if dates else '',
                stance='admission' if re.search(r'承认|确认属实',title) else 'denial' if re.search(r'虚假|伪造|造假|辟谣|否认|恶意攻击',title+assertion) else '',
                views=[compact(assertion)])


def initiative_fact(title):
    text=clean(title)
    match=re.match(r'^(.{2,16}?)(?:发起|启动|推出|发布)(.{3,60}?(?:激励计划|奖励计划|补贴计划|扶持计划|支持计划))',text)
    if not match:
        return None
    reward=re.search(rf'最高.{{0,5}}?({NUMBER})(万元|亿元|元)',canonical_quantities(text))
    return dict(family='initiative',actor=compact(match[1]),object=compact(match[2]),
                value=(reward[1]+reward[2]) if reward else '')


def capacity_approval(title):
    text=clean(title)
    if not ('同意' in text and re.search(r'增加|新增',text) and re.search(r'启用库容|仓库.*库容',text)):
        return None
    target=re.search(r'同意([\u4e00-\u9fff（）()]{4,45}?有限公司)',text)
    issuer=re.split(r'[：:]',text,maxsplit=1)[0]
    issuer={'上期能源':'上海国际能源交易中心','上期所':'上海期货交易所'}.get(issuer,issuer)
    if not target or len(issuer)>20:
        return None
    size=re.search(rf'(?:增加至|增至)({NUMBER})(万)?立方米',text)
    return dict(family='capacity-approval',actor=issuer,object=compact(target[1]),
                value=(size[1]+('万' if size[2] else '')+'立方米') if size else '')


def policy_fact(title, summary=''):
    """Publication, extracts and official Q&A about one identified regulation.

    A sector/country word is not an identity. Require a named issuer and a
    concrete formal policy action/object, then a document reference or dated
    publication evidence. Application/enforcement news and third-party analysis
    aren't another announcement of that regulation.
    """
    head=clean(title)
    if not re.search(r'出口管制|出口限制|相关物项',head):
        return None
    if re.search(r'立案|查处|处罚|被罚|获准|获批|个案|企业申请|专家|分析师|研报|证券.{0,8}解读|影响几何|深度解读|评论|观点|制度逻辑|行业自觉|受益股|股价|有何影响',head):
        return None
    objects={'无人机' if name=='无人驾驶航空器' else name for name in policy_objects(head)}
    if len(objects)>1:
        return None  # A multi-policy article is not proof of one policy identity.
    text=head+'\n'+clean(summary)[:700]
    issuer=re.search(r'(美国|日本|英国|中国)?(?:商务部|海关总署)',text)
    if not issuer:
        return None
    actor=(issuer[1] or '中国')+'商务部' if issuer[0].endswith('商务部') else (issuer[1] or '中国')+'海关总署'
    formal=re.search(r'(优化调整|调整|优化|修订|修改|加强|取消|暂停|恢复|实施)(?:对)?(?:特定)?([\u4e00-\u9fffA-Za-z、]{2,30}?)(?:的)?出口管制(?:措施)?',text)
    if not formal:
        return None
    target=formal[2].replace('无人驾驶航空器','无人机')
    target=re.sub(r'(?:相关)?物项$','',target)
    target=target.removeprefix('特定').rstrip('的')
    if len(target)<2 or re.search(r'本次|有关|相关措施|政策和|法律|法规|条例|出口管制',target):
        return None
    action={'优化':'调整','优化调整':'调整','修改':'修订'}.get(formal[1],formal[1])
    reference=re.search(r'公告\s*[〔（(]?\s*(\d{4})\s*年?\s*[〕）)]?\s*第?\s*(\d+)\s*号',text)
    # An explicit issue date must be tied to publication, not the future date
    # introduced by "本公告自...实施" or an unrelated article dateline.
    date=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日[，,\s].{0,55}?(?:发布公告|发布关于|联合发布|发布了)',text)
    issue=f'{int(date[1]):04}-{int(date[2]):02}-{int(date[3]):02}' if date else ''
    effective=re.search(r'(?:本公告|相关措施|有关措施|措施)(?:将)?(?:自|于)(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日',text)
    effective_day=''
    if effective:
        year=effective[1] or (reference[1] if reference else (date[1] if date else ''))
        if year:
            effective_day=f'{int(year):04}-{int(effective[2]):02}-{int(effective[3]):02}'
    starts=re.search(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日(?:起)?(?:正式)?(?:实施|生效)',head)
    stage='publication'
    if starts:
        stage='scheduled-implementation'
    elif re.search(r'今日起.{0,6}(?:生效|实施)|正式生效|正式实施|开始执行',head) and not re.search(r'将|拟|计划',head):
        stage='implementation'
    elif re.search(r'修订|修改|新增|扩大.{0,8}范围|延期|延长|推迟|变更',head):
        stage='revision'
    elif re.search(r'取消|终止|废止|撤销',head):
        stage='withdrawal'
    elif re.search(r'本次新增|另行新增|现决定.{0,8}(?:延长|扩大|修改)',clean(summary)[:300]):
        stage='revision'
    return dict(family='policy',actor=actor,object=target,action=action,
                reference=(reference[1]+'-'+reference[2]) if reference else '',
                date=issue,date_explicit=bool(date),effective_date=effective_day,stage=stage,
                headline_start=('-'.join(starts.groups(default=''))) if starts else '')


def dated_policy(fact,day):
    if fact and fact.get('family')=='policy':
        fact={**fact,'date':fact.get('date') or day,'publication_day':day}
        if fact['stage']=='scheduled-implementation' and day:
            year,month,date=fact['headline_start'].split('-')
            start=f'{year or day[:4]}-{int(month):02}-{int(date):02}'
            fact['stage']='implementation' if day>=start else 'publication'
            fact['effective_date']=fact.get('effective_date') or start
        return fact
    return fact


def same_policy(a,b):
    """Same regulatory event; distinct implementation/revision nodes may follow."""
    if not a or not b or a.get('family')!='policy' or b.get('family')!='policy':
        return False
    if any(a.get(k)!=b.get(k) for k in ('actor','object')):
        return False
    if a.get('reference') and b.get('reference'):
        return a['reference']==b['reference'] and not (a.get('date_explicit') and b.get('date_explicit') and a['date']!=b['date'])
    return bool(a.get('date') and a.get('date')==b.get('date') and a.get('action')==b.get('action'))


@lru_cache(maxsize=8192)
def fact_identity(title, summary=''):
    return (market_fact(title) or corporate_fact(title) or capacity_approval(title) or initiative_fact(title)
            or policy_fact(title,summary) or usage_response(title,summary) or press_fact(title,summary) or speech_fact(title,summary))


def same_fact(a,b):
    """Tri-state: True/False for typed facts, None for unsupported evidence."""
    if not a or not b:
        return None
    if a['family'] != b['family']:
        return False
    if a['family']=='consumer-safety':
        return same_incident_progress(a,b)
    if a['family']=='policy':
        return (same_policy(a,b) and a.get('stage')==b.get('stage')
                and not (a.get('effective_date') and b.get('effective_date') and a['effective_date']!=b['effective_date']))
    if a.get('publication_day') and b.get('publication_day') and a['publication_day']!=b['publication_day']:
        return False
    family=a['family']
    if a['actor'] != b['actor']:
        return False
    if family in {'speech','press-release'}:
        if any(a.get(k) and b.get(k) and a[k]!=b[k] for k in ('date','stance')):
            return False
        for x in a['views']:
            for y in b['views']:
                shared=_grams(x)&_grams(y)
                if len(shared)>=12 and len(shared)/max(1,min(len(_grams(x)),len(_grams(y))))>=.62:
                    return True
                blocks=[m.size for m in SequenceMatcher(None,x,y,autojunk=False).get_matching_blocks() if m.size>=5]
                if a.get('date') and a.get('date')==b.get('date') and len(blocks)>=2 and sum(blocks)>=18:
                    return True
        return False
    if family=='initiative':
        x,y=a['object'],b['object']
        if x!=y and not (min(len(x),len(y))>=8 and (x in y or y in x)):
            return False
        return not (a.get('value') and b.get('value') and a['value']!=b['value'])
    keys = ('phase','direction','metric','value','unit','currency') if family=='market' else ('object','metric','value','stake','stage')
    if any(a.get(k) and b.get(k) and a[k]!=b[k] for k in keys):
        return False
    # Missing futures contract is NOT proof of the same series.
    if family=='market' and a.get('contract') != b.get('contract'):
        return ({a.get('contract'),b.get('contract')}=={'主力合约','连续主力合约'}
                and bool(a.get('observed_quote')) and a.get('observed_quote')==b.get('observed_quote'))
    return True


def event_key(fact):
    if not fact:
        return ''
    return '|'.join(str(fact.get(k,'')) for k in ('family','actor','object','contract','phase'))


def policy_objects(title):
    if not re.search(r'出口管制|出口限制|相关物项',title):
        return set()
    return {name for name in ('无人机','无人驾驶航空器','挖泥船','盾构机','芯片','半导体') if name in title}


def policy_conflict(a,b):
    left,right=policy_objects(a),policy_objects(b)
    left={('无人机' if x=='无人驾驶航空器' else x) for x in left}
    right={('无人机' if x=='无人驾驶航空器' else x) for x in right}
    return bool(left and right and left.isdisjoint(right))
