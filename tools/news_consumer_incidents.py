"""Conservative product-safety evidence, not retailer/brand topic clustering.

Only named products with an evidenced brand and safety intent qualify. Brands
are extracted from the report; no company aliases or incident whitelist.
"""
from __future__ import annotations

from datetime import date
import re
import unicodedata
from news_fact_normalization import canonical_quantities, NUMBER


PRODUCTS = {
    '牡蛎精': r'牡蛎(?:精|提取物)(?:胶囊)?',
    '抗糖丸': r'抗糖丸', '褪黑素': r'褪黑素(?:片|胶囊)?',
    '鱼油': r'鱼油(?:胶囊)?', '蛋白粉': r'蛋白粉', '奶粉': r'奶粉',
    '益生菌': r'益生菌(?:粉|胶囊)?', '维生素片': r'维生素(?:[a-z0-9]+)?片',
    '燕窝': r'燕窝', '咖啡': r'咖啡', '巧克力': r'巧克力',
    '面霜': r'面霜', '防晒霜': r'防晒霜', '洗发水': r'洗发水',
    '隐形眼镜': r'隐形眼镜', '湿巾': r'湿巾', '纸尿裤': r'纸尿裤',
}
SAFETY = re.compile(r'检出|超标|不合格|污染物|风险警示|安全风险|质量问题|召回')
FOCUS = re.compile(r'召回|下架|停售|检出|超标|污染物|不合格|退款|退货|风险警示|质量问题|立案|处罚|罚款|判赔|判决')
ISSUES = {'lead':r'铅(?:含量)?(?:超标|超出)', 'mercury':r'汞(?:含量)?(?:超标|超出)',
          'cadmium':r'镉(?:含量)?(?:超标|超出)', 'salmonella':r'沙门氏菌'}


def clean(text):
    return unicodedata.normalize('NFKC', re.sub(r'<[^>]+>', '', str(text or ''))).lower()


def compact(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', clean(text))


def brand_evidence(text, pattern):
    quoted, direct = set(), set()
    for product in re.finditer(pattern, text, re.I):
        prefix = text[max(0,product.start()-90):product.start()]
        match = re.search(r'[“"「]([^”"」\n]{2,24})[”"」](?:\s*\(([^)]{2,24})\))?(?:品牌|牌)?(?:的)?[“"「]?\s*$', prefix)
        if match:
            quoted.add(compact(match[1]))
            if match[2]: quoted.add(compact(match[2]))
            continue
        prefix = re.sub(r'(?:品牌|牌)(?:的)?[“"「]?\s*$','',prefix)
        # An explicit customer-service speaker is also brand evidence. If we
        # split at 回应 first, "Swisse客服回应抗糖丸…" loses its brand and the
        # legacy parser incorrectly produces both a report and a response.
        speaker = re.search(r'(?:^|[，,。；;：:\n【】])([\u4e00-\u9fffA-Za-z·]{2,12}?)(?:官方)?客服(?:回应|表示|称|答复)?\s*$',prefix)
        if speaker:
            direct.add(compact(speaker[1]))
        tail = re.split(r'[，,。；;：:\n#【】]|召回|下架|回应|退款|检出|收到|(?:标称|进口|这款|该款|一款|的)',prefix)[-1]
        tail = re.sub(r'^(?:紧急|立即|全面|部分|部分批次|本次|涉及|对|将|为)+','',tail).strip('“”" ')
        if re.fullmatch(r'[\u4e00-\u9fffA-Za-z·]{2,12}',tail) and tail not in {'商品','产品','相关','所有批次','胶囊','消费者','涉事公司'}:
            direct.add(compact(tail))
    # Explicit quote/brand-product adjacency outweighs a guessed free-text
    # prefix. Two unrelated free-text brands are ambiguity, not aliases.
    return quoted or (direct if len(direct)==1 else set())


def incident_identity(title, summary='', publication_day=''):
    head, opening = clean(title), clean(summary)[:650]
    if re.search(r'电影|电视剧|剧集|小说',head):
        return None
    head_products = {name for name,pattern in PRODUCTS.items() if re.search(pattern,head)}
    body_products = {name for name,pattern in PRODUCTS.items() if re.search(pattern,opening)}
    candidates = head_products or body_products
    background = bool(head_products and re.search(r'曾|此前|历史|过往',head) and re.search(r'整改|处罚|纠纷|诉讼',head))
    if len(candidates)!=1 or not (FOCUS.search(head) or background) or not SAFETY.search(head+' '+opening):
        return None
    if not head_products and re.search(r'立案|处罚|罚款|判赔|判决',head) and not re.search(r'召回|下架|检出|超标|污染物|质量问题',head):
        return None
    # A product's share-price reaction is a separate financial subject.
    if re.search(r'股价|股票|etf|lof|基金',head) and re.search(r'涨|跌|停牌|评级|目标价',head):
        return None
    product = next(iter(candidates))
    brands = brand_evidence(head+'\n'+opening, PRODUCTS[product])
    if not brands:
        return None
    issues = sorted(key for key,pattern in ISSUES.items() if re.search(pattern,head+' '+opening))
    batches = sorted(set(re.findall(r'(?:批号|批次号?|生产批次|batch|lot)[：:\s]*([a-z0-9][a-z0-9-]{2,30})',head+' '+opening)))
    models = sorted(set(re.findall(r'(?:[一二三123]段|[a-z]{1,4}\d{2,6}型)',head)))
    stamp = publication_day
    explicit = False
    dated = re.search(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日.{0,80}?(?:召回|下架|检出|风险警示)',head+' '+opening)
    if dated and publication_day:
        try:
            stamp = date(int(dated[1] or publication_day[:4]),int(dated[2]),int(dated[3])).isoformat()
            explicit = True
        except ValueError:
            pass
    return dict(family='consumer-safety',actor=sorted(brands,key=lambda x:(not bool(re.search(r'[\u4e00-\u9fff]',x)),len(x),x))[0],
                brands=sorted(brands),object=product,issues=issues,batches=batches,models=models,
                incident_day=stamp,date_explicit=explicit)


def same_incident(a,b):
    if not a or not b:
        return None
    if a.get('object')!=b.get('object') or not set(a.get('brands',[a.get('actor')])) & set(b.get('brands',[b.get('actor')])):
        return False
    for key in ('issues','batches','models'):
        if a.get(key) and b.get(key) and set(a[key]).isdisjoint(b[key]):
            return False
    if not a.get('incident_day') or not b.get('incident_day'):
        return False  # A brand/product alone never proves the same episode.
    try:
        return abs((date.fromisoformat(a['incident_day'])-date.fromisoformat(b['incident_day'])).days)<=3
    except ValueError:
        return False


def same_incident_progress(a,b):
    if same_incident(a,b) is not True or a.get('action')!=b.get('action'):
        return False
    for key in ('scope','stance','detail','stage'):
        if a.get(key) and b.get(key) and a[key]!=b[key]:
            return False
    for key in ('batches','issues','models'):
        if a.get(key) and b.get(key) and set(a[key])!=set(b[key]):
            return False
    return True


def incident_statements(title, summary='', publication_day=''):
    identity = incident_identity(title,summary,publication_day)
    if not identity:
        return []
    head = clean(title)
    result=[]
    def add(kind,action,label=None,**details):
        result.append((kind,label or title,{**identity,'action':action,**details}))
    if re.search(r'曾|此前|历史|过往',head) and re.search(r'整改|处罚|纠纷|诉讼',head):
        add('analysis','background'); return result
    # A headline explicitly asserting a remedy/action is not a second generic
    # response too. Do not split every historical verb in the body into nodes.
    for kind,pattern in (('withdrawal',r'下架|停售'),('recall',r'召回'),
                         ('investigation',r'立案|启动调查|监管调查'),
                         ('penalty',r'处罚|罚款|被罚'),('judgment',r'判赔|判决|裁决')):
        if re.search(pattern,head):
            clauses=' '.join(clause for clause in re.split(r'[，,。；;]',head) if re.search(pattern,clause)
                            and not re.search(r'(?:否认|尚未|并未|没有|不会|无需|不予|是否|未启动).{0,8}(?:'+pattern+r')',clause)
                            and not re.search(r'(?:'+pattern+r').{0,6}[吗？?]',clause))
            if not clauses:
                continue
            scope='all' if re.search(r'所有批次|全部批次',clauses) else 'partial' if '部分批次' in clauses else ''
            quantity=re.search(r'(?:召回|下架)[^\d，,。；;]{0,8}(\d+(?:\.\d+)?)\s*(件|瓶|盒|批)',clauses)
            money=re.search(r'(?:罚款|被罚|判赔)[^\d，,。；;]{0,8}('+NUMBER+r')(美元|元)',canonical_quantities(clauses))
            stage='planned' if re.search(r'(?:拟|计划|将).{0,6}(?:'+pattern+r')',clauses) else 'completed' if re.search(r'已完成|完成召回|召回完成',clauses) else ''
            add(kind,kind,scope=scope,stage=stage,detail=(money[1]+money[2]) if money else (quantity[1]+quantity[2]) if quantity else '')
    response = bool(re.search(r'回应|官方客服|客服[：:]|客服.{0,8}(?:称|表示|答复)',head))
    if re.search(r'退款|退货',head):
        add('response','refund',stance='refund')
    elif response and re.search(r'辟谣|否认|不属实|系谣言|未检出|未超标',head):
        add('response','denial',stance='denial')
    elif response and re.search(r'正在核查|核实中|正在调查',head):
        add('response','checking',stance='checking')
    elif response and re.search(r'(?:未|没有|并未).{0,4}进入中国|未在中国.{0,3}销售',head):
        add('response','response',detail='not-sold-in-china')
    elif response and re.search(r'(?:已|已经).{0,3}在中国销售|承认.{0,8}(?:销售|问题|超标)',head):
        add('response','response',detail='confirmed-problem')
    # A bare 'responds to contamination' does not independently assert a fresh
    # detection or a new response detail. It supports the disclosure instead.
    first_clause=re.split(r'[，,。；;]',head,maxsplit=1)[0]
    if re.search(r'检出|超标|不合格|风险警示',first_clause) and not re.search(r'回应|客服',first_clause):
        disclosure=re.split(r'[，,。；;]',str(title),maxsplit=1)[0].removesuffix('后')
        add('report','disclosure',disclosure)
    if not result:
        add('report','disclosure')
    return result[:4]


def repeated_action_response(title):
    text=clean(title)
    action=re.search(r'回应(下架|召回)',text)
    return bool(action and text.count(action[1])>=2
                and not re.search(r'[：:]|原因|因为|否认|并未|退款|核查|解释|新增|扩大',text))


def is_background(identity):
    return bool(identity and identity.get('family')=='consumer-safety' and identity.get('action')=='background')
