"""Homepage industry taxonomy, independent of archive topics and source labels.

Classify content, never the publisher or hot-list rank. Society requires positive
domestic evidence; unclassified international/military news stays on source pages.
"""
from __future__ import annotations

import html
import re
from typing import Iterable
from news_editorial_quality import rejection_reason
from news_fact_normalization import financial_scope


CATEGORY_LABELS = {
    "hardtech": "硬科技",
    "commodities": "大宗",
    "consumer": "新消费",
    "auto": "汽车",
    "newenergy": "新能源",
    "finance": "金融",
    "logistics": "物流",
    "society": "社会",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)
ALL_ONLY_CATEGORY = "other"  # Audit/source pages only; not a homepage domain.
SECURITIES_MISCONDUCT = re.compile(
    r"老鼠仓|内幕交易|利用未公开信息交易|操纵(?:证券|股票|证券期货)市场|证券欺诈"
)
# A fund name is not an industry subject. Decide from the financial vehicle AND
# its activity, before industry keyword scores or the broad 'new product' veto.
LISTED_FUND = re.compile(r"(?<![a-z])(?:etf|lof|qdii|fof|reits?)(?![a-z])", re.I)
FUND_PRODUCT = re.compile(r"(?:指数|股票|债券|证券投资|公募|主题|联接)基金|基金产品")
FUND_TRADING = re.compile(
    r"停牌|复牌|停复牌|暂停交易|恢复交易|上市交易|交易公告|交易提示|交易风险|风险提示|"
    r"溢价|折价|净值|份额|申购|申赎|赎回|认购|限购|限额|分红|派息|清盘|"
    r"上涨|下跌|大涨|大跌|涨停|跌停|涨幅|跌幅|领涨|领跌|成交|换手|"
    r"净流入|净流出|资金流入|资金流出|净买入|净卖出|持仓|调仓|增持|减持|"
    r"管理费|托管费|费率|基金经理变更"
)
FUND_ISSUANCE = re.compile(r"发行|发售|推出|发布|获批|上市|成立|募集")
FUND_PORTFOLIO_COMPANY = re.compile(
    r"(?:etf|lof|基金).{0,8}(?:重仓股|成[份分]股|重仓(?:的)?(?:公司|企业)|投资的(?:公司|企业))", re.I
)

FOREIGN_PLACES = re.compile(
    r"美国|英国|法国|德国|日本|韩国|朝鲜|俄罗斯|乌克兰|伊朗|以色列|巴勒斯坦|加沙|中东|"
    r"沙特|也门|胡塞|黎巴嫩|叙利亚|伊拉克|约旦|阿联酋|卡塔尔|科威特|阿曼|巴林|"
    r"阿富汗|巴基斯坦|印度|孟加拉|尼泊尔|斯里兰卡|缅甸|泰国|越南|新加坡|印尼|印度尼西亚|"
    r"马来西亚|菲律宾|柬埔寨|老挝|文莱|东帝汶|土耳其|乌兹别克斯坦|哈萨克斯坦|"
    r"加拿大|墨西哥|巴西|阿根廷|智利|秘鲁|委内瑞拉|哥伦比亚|巴拿马|哥斯达黎加|"
    r"澳大利亚|澳洲|新西兰|南非|肯尼亚|尼日利亚|坦桑尼亚|埃及|苏丹|埃塞俄比亚|"
    r"立陶宛|拉脱维亚|爱沙尼亚|波兰|罗马尼亚|挪威|瑞典|芬兰|丹麦|冰岛|瑞士|荷兰|"
    r"比利时|西班牙|葡萄牙|意大利|希腊|塞尔维亚|白俄罗斯|欧盟|欧洲|东盟|北约|"
    r"纽约|曼哈顿|华盛顿|堪萨斯|洛杉矶|加州|佛罗里达|伦敦|巴黎|柏林|东京|首尔|"
    r"莫斯科|圣彼得堡|列宁格勒|基辅|德黑兰|利雅得|关岛|黑海|霍尔木兹|红海|南美|北美|海外|国外"
)
GEOPOLITICS = re.compile(
    r"外交|外长|国务卿|大使|驻外|使馆|国事访问|正式访问|元首|总统|首相|"
    r"特朗普|鲁比奥|泽连斯基|内塔尼亚胡|马克龙|默茨|白宫|五角大楼|联合国|多边贸易|亚太合作|APEC|IMF|"
    r"军事|军方|国防|军用|军队|海军|空军|陆军|军演|实弹射击|航行警告|武装|"
    r"导弹|战斗机|航母|潜艇|弹药|空袭|战争|停火|俄乌|中东局势|远程打击|"
    r"击落|摧毁|拦截|无人机残骸|无人机.*威胁"
)
DOMESTIC_EVIDENCE = re.compile(
    r"中国(?!地震台网|驻)|我国|国内|全国|内地|大陆|港澳|香港|澳门|台湾|"
    r"北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|"
    r"内蒙古|广西|西藏|宁夏|新疆|深圳|广州|成都|武汉|杭州|南京|苏州|郑州|西安|长沙|合肥|厦门|福州|济南|青岛|昆明|贵阳|南宁|南昌|石家庄|太原|沈阳|长春|哈尔滨|呼和浩特|兰州|西宁|银川|乌鲁木齐|拉萨|海口|三亚|宁波|温州|无锡|佛山|东莞|珠海|汕头|泉州|恩施|汶川|黄渤海|北方.*(?:降温|冷空气)|"
    r"国家(?:医保局|卫健委|监委|监察委|市场监督管理总局|统计局)|教育部|民政部|公安部|人社部|住建部|最高法|最高检|"
    r"央视(?:曝光|调查|揭露)|人民日报(?:评|调查)"
)
SOCIAL_LIFE = re.compile(
    r"社会|民生|居民|市民|城市|人口|养老|父母|老人|儿童|孩子|婴儿|学生|校园|大学|学校|高考|教育|考试|"
    r"医疗|医保|医院|疾病|癌|健康|医生|救治|骗局|诈骗|传销|治安|刑案|违法|犯罪|警方|法院|判决|维权|纠纷|"
    r"事故|灾害|地震|火灾|爆炸|救援|伤亡|死亡|高坠|失踪|通报|调查|监察|审查|腐败|受贿|"
    r"就业|招聘|工资|欠薪|住房|房租|社保|消费权益|食品安全|环境|污染|生态|野生动物|"
    r"铁路|旅客|交通|地铁|公交|客运|出行|气象|冷空气|降温|气温|台风|暴雨|降雨|大风|"
    r"演唱会|黄牛|门票|文化|文物|体育|比赛|赛事|热线|社区|街道|不文明|排泄"
)

# Editorial consumer scope includes digital services and cultural products, not
# just physical brands. A platform/creator name alone is NOT sector evidence:
# it can be the place a public incident was filmed, or a celebrity's gossip.
CONSUMER_PLATFORMS = re.compile(
    r"抖音|快手|小红书|视频号|哔哩哔哩|(?<![a-z])(?:b站|bilibili)(?![a-z])|"
    r"爱奇艺|腾讯视频|优酷|芒果tv|芒果超媒|咪咕|西瓜视频|搜狐视频|"
    r"视频平台|长视频|短视频平台|流媒体|网络视听|直播平台|"
    r"(?<![a-z])(?:tiktok|youtube|netflix|spotify|disney\+)(?![a-z])|奈飞|"
    r"腾讯音乐|网易云音乐|QQ音乐|阅文|番茄小说|起点读书|晋江文学|斗鱼|虎牙|"
    r"腾讯游戏|网易游戏|米哈游|叠纸|鹰角|库洛|任天堂|育碧|暴雪|游戏科学|(?<![a-z])steam(?![a-z])",
    re.I,
)
COMMERCE_CREATORS = re.compile(
    r"三只羊|小杨哥|大杨哥|张庆杨|张开杨|李佳琦|薇娅|辛巴|董宇辉|"
    r"与辉同行|东方甄选|交个朋友|遥望科技|(?<![a-z])mcn(?![a-z])|"
    r"直播电商|直播带货|带货主播|主播机构|直播机构|网红经济|创作者经济",
    re.I,
)
CULTURAL_PRODUCTS = re.compile(
    r"影视|电影|票房|院线|影院|新片|新剧|春节档|国庆档|暑期档|贺岁档|电视剧|剧集|网剧|微短剧|短剧|综艺|番剧|"
    r"动画片|动漫|跨年晚会|网络晚会|网络文学|网文|数字阅读|有声书|"
    r"网络游戏|电子游戏|(?:题材|单机|主机|独立)游戏|游戏产业|游戏公司|游戏版号|游戏发行|新游|国产游戏|手游|端游|"
    r"数字内容消费|文化消费|文娱消费|文创|IP衍生|IP授权",
    re.I,
)
EXPERIENCE_PRODUCTS = re.compile(r"演唱会|音乐节|音乐会|音乐剧|舞台剧|演艺市场|演出市场|线下演出|livehouse", re.I)
CONSUMER_OPERATIONS = re.compile(
    r"经营|业务|商业化|变现|营收|收入|利润|盈利|亏损|财报|季报|年报|融资|上市|"
    r"并购|收购|股权|裁员|高管|董事长|总裁|CEO|转型|离职|跳槽|解约|签约|"
    r"会员|订阅|付费|收费|涨价|广告|版权|分账|票务|票价|售票|退票|销售|"
    r"用户(?:数|量|增长|流失|规模|活跃|权益)|月活|日活|获客|引流|涨粉|掉粉|增粉|"
    r"粉丝(?:数|量)|粉丝.{0,6}(?:清零|归零|增长|下降|暴涨|缩水|流失)|"
    r"复播|复出|重返|回归|封禁|封号|禁言|限流|整改|规则|处罚|"
    r"账号.{0,8}(?:注册|注销|封禁|恢复)|新功能|新产品|新服务|上线|开播|"
    r"创作者(?:激励|分成|收入)|平台(?:治理|政策)|带货|电商|品牌|代言",
    re.I,
)
PRIVATE_ENTERTAINMENT = re.compile(r"恋情|绯闻|出轨|分手|结婚|婚礼|离婚|怀孕|生子|庆生|生日|素颜|身材|穿搭|造型|红毯|饭圈|应援|私生活")
CONCRETE_CONSUMER_BUSINESS = re.compile(r"电商|带货|会员|订阅|营收|财报|利润|品牌|代言|解约|赔偿|票房|售票|退票|收费|分账|销售|交易额|版权")
PUBLIC_INCIDENT = re.compile(r"地震|火灾|爆炸|伤亡|死亡|逝世|救援|失踪|走失|落水|枪击|撞人|坠楼|高坠|校园霸凌|军事|军方|军演|空袭|导弹|停火|战争")
CULTURAL_MARKET_EVENT = re.compile(r"上映|定档|开播|票房|发行|预告|新片|新剧|排片|上座率|制作成本|制片|出品|版号|上线|流水|销量")
CORE_TECH_DEVELOPMENT = re.compile(r"(?:发布|推出|开源|自研|研发|训练|突破|投产|量产).{0,35}(?:大模型|芯片|算力|模型训练|光刻|半导体)")
MILITARY_OPERATIONS = re.compile(
    r"袭击|空袭|炮击|交火|交战|作战|战场|军事行动|军事演习|军演|实弹射击|武装|战争|停火|撤军|驻军|"
    r"发射导弹|击落|摧毁|拦截|军援|武器援助|战斗机|战机|军机|航母|潜艇|坦克|弹药|远程打击"
)
MILITARY_TOPIC = re.compile(r"军事|军方|军用|军队|(?:俄|乌|美|以|伊|英|法|德|印|日|韩|朝)军|国防部|国防预算|防卫费|武器|导弹|海军|空军|陆军|俄乌冲突")
MILITARY_AIRSPACE = re.compile(r"(?:无人机|军机|战机).{0,35}(?:领空|领海|侵入|越境|转向|残骸|威胁)|(?:领空|领海).{0,35}(?:无人机|军机|战机)")
POLITICAL_ACTIVITY = re.compile(r"大选|选举|就职|内阁|元首|首脑|总理.{0,8}(?:会谈|会见)|国会.{0,12}(?:议长|议员)")
ECONOMIC_FACT = re.compile(
    r"油价|金价|运价|价格|汇率|利率|降息|加息|降准|货币政策|关税|出口管制|出口限制|禁售|产业补贴|补贴|"
    r"投资额|投资计划|项目投资|投资项目|建厂|工厂|产能|产量|供应|投产|量产|订单|交付|营收|净利润|财报|"
    r"融资|并购|收购|GDP|CPI|PPI|国内生产总值", re.I
)
ECONOMIC_IMPACT = re.compile(
    r"油价|金价|运价|汇率|(?:原油|天然气|石油|航运).{0,16}(?:价格|供应|产量|运输|中断)|股市|股指|股价|"
    r"(?:物流枢纽|仓库|港口|码头).{0,24}(?:起火|停运|关闭|受损|中断)|"
    r"(?:海峡|航道|港口).{0,16}(?:通航|断航)|(?:航运|货运|供应链).{0,16}(?:中断|受阻|恢复)"
)
COMPANY_FINANCIAL_FACT = re.compile(r"(?:公司|企业|集团|股份).{0,35}(?:营收|净利润|财报|融资|上市)|(?:财报|营收|净利润).{0,20}(?:公司|企业|集团)")
INDUSTRIAL_POLICY = re.compile(r"出口管制|出口限制|关税|产业补贴|禁售|投资审查|民用.{0,10}(?:许可|规定|政策)")
CASUAL_CONTENT_CHAT = re.compile(r"推荐.{0,12}(?:看|听|电影|电视剧|综艺)|(?:看什么|看啥)(?:电影|电视剧|综艺)?|你(?:最|会|想|觉得|喜欢)|好看吗|追剧打卡|剧情讨论")


def _consumer_anchor(text: str) -> bool:
    return bool(CONSUMER_PLATFORMS.search(text) or COMMERCE_CREATORS.search(text)
                or CULTURAL_PRODUCTS.search(text) or EXPERIENCE_PRODUCTS.search(text))


def _private_entertainment(text: str) -> bool:
    person = re.search(r"明星|演员|艺人|网红|歌手|主播", text)
    return bool(PRIVATE_ENTERTAINMENT.search(text) and (person or _consumer_anchor(text))
                and not CONCRETE_CONSUMER_BUSINESS.search(text))


def digital_consumer_score(text: str) -> int:
    """Cultural products qualify; platforms/creators need operating evidence.

    Keep this lower than a concrete car/chip/energy product's sector score, and
    don't turn real-world incidents, stock quotes or celebrity gossip into
    consumption merely because a platform or cinema is mentioned.
    """
    if _private_entertainment(text) or CORE_TECH_DEVELOPMENT.search(text):
        return 0
    culture = bool(CULTURAL_PRODUCTS.search(text) or EXPERIENCE_PRODUCTS.search(text))
    if culture and CASUAL_CONTENT_CHAT.search(text) and not (CULTURAL_MARKET_EVENT.search(text) or CONCRETE_CONSUMER_BUSINESS.search(text)):
        return 0
    if PUBLIC_INCIDENT.search(text) and not (culture and CULTURAL_MARKET_EVENT.search(text)):
        return 0
    if culture:
        return 18
    if re.search(r"明星|演员|艺人|歌手|主播", text) and re.search(r"代言|商务合作|品牌.*解约", text):
        return 12
    if (CONSUMER_PLATFORMS.search(text) or COMMERCE_CREATORS.search(text)) and CONSUMER_OPERATIONS.search(text):
        return 12
    # Generic game/esports coverage needs a product or market signal; a match
    # score or casual gaming anecdote alone is not a consumer-industry story.
    if re.search(r"游戏|电竞|音乐平台|播客", text) and re.search(r"版号|上线|发行|流水|付费|营收|收入|订阅|版权|销售|商业化|赞助|转播权", text):
        return 18
    return 0


def public_category(title: str, summary: str = "") -> str:
    """Conservative location gate; a Chinese publisher is not domestic evidence."""
    focus = _clean(title)
    context = _clean(summary)[:300]
    if not SOCIAL_LIFE.search(focus + " " + context):
        return ALL_ONLY_CATEGORY
    if FOREIGN_PLACES.search(focus) or GEOPOLITICS.search(focus):
        return ALL_ONLY_CATEGORY
    if DOMESTIC_EVIDENCE.search(focus):
        return "society"
    if FOREIGN_PLACES.search(context) or GEOPOLITICS.search(context):
        return ALL_ONLY_CATEGORY
    return "society" if DOMESTIC_EVIDENCE.search(context) else ALL_ONLY_CATEGORY

# Specific products/businesses outweigh a brand's secondary business and generic
# corporate-finance words. Do not treat all IPOs/earnings as financial-sector news.
RULES = {
    "hardtech": (
        (r"人工智能|大模型|智能体|具身智能|机器人|外骨骼|芯片|半导体|晶圆|光刻|封测|算力|超算|超导|曙光8000|量子|光通信|光模块|数据中心|云计算|数控机床|工业母机|高端装备|科技要闻|干细胞|细胞治疗", 20),
        (r"(?<![a-z0-9])(?:ai|gpu|cpu|gpt\d*|chatgpt|cpo|6g|5g)(?![a-z0-9])", 18),
        (r"手机|消费电子|智能终端|智能手表|折叠屏|操作系统|计算机|服务器|航空制造|商业航天|航天|火箭|卫星|飞船|探测器|无人机|无人驾驶航空器|低空经济|合成生物|生物制造", 18),
        (r"英伟达|英特尔|台积电|中芯国际|海力士|美光|高通|宇树|寒武纪|科大讯飞|华为|小米|苹果公司|苹果(?=发布|推出|财报|营收)|(?<![a-z])(?:openai|anthropic|deepseek|nvidia|asml|amd)(?![a-z])", 8),
    ),
    "commodities": (
        (r"大宗商品|商品期货|有色金属|贵金属|黄金(?!周)|白银|金价|银价|原油|油价|布伦特|天然气|液化气|油气|石油|石化|煤炭|焦煤|焦炭|动力煤", 20),
        (r"铁矿石|螺纹钢|钢铁|热卷|沪铜|铜价|铜矿|沪铝|铝价|铝土矿|锌价|镍价|稀土|碳酸锂|锂价|钴价|(?:铜|铝|锌|镍|锂|钴|锡|铅)(?:矿|价|生产|产量|产能|现货|期货|冶炼)|甲醇|尿素|纯碱|玻璃期货|棕榈油|豆粕|大豆|玉米|棉花|白糖|小麦", 20),
        (r"(?<![a-z])(?:opec\+?|wti|lme|comex|pta|lng)(?![a-z])|中石油|中石化|中国石油|中国石化|紫金矿业|洛阳钼业|宝钢|中国神华", 8),
    ),
    "consumer": (
        (r"新消费|消费品|零售|电商|直播带货|即时零售|跨境电商|餐饮|茶饮|咖啡|奶茶|白酒|啤酒|饮料|乳制品|预制菜|保健品|褪黑素|美妆|化妆品|护肤|服装|服饰|鞋服|潮玩|免税|酒店|旅游|文旅|家电|宠物经济", 20),
        (r"茅台|五粮液|瑞幸|星巴克|蜜雪|霸王茶姬|喜茶|奈雪|农夫山泉|伊利|蒙牛|海底捞|麦当劳|肯德基|泡泡玛特|山姆|沃尔玛|安踏|李宁|珀莱雅|携程|美团|拼多多|淘宝|天猫|京东(?!物流)|阿里巴巴|健合|(?<![a-z])swisse(?![a-z])", 8),
    ),
    "auto": (
        (r"汽车|车企|整车|乘用车|商用车|电动车|新能源车|智能驾驶|自动驾驶|辅助驾驶|无人驾驶(?!航空器)|智驾|车载|车规|新车|车市|购车|车型|车展|车联网|网约车|埃安|岚图|阿维塔|极氪|极狐|小马智行|文远知行|萝卜快跑|(?<![a-z])suv(?![a-z])", 20),
        (r"比亚迪|特斯拉|蔚来|小鹏|理想汽车|零跑|小米汽车|吉利|长城汽车|长安汽车|上汽|一汽|广汽|东风汽车|赛力斯|鸿蒙智行|问界|智界|宝马|奔驰|奥迪|大众汽车|丰田|本田|保时捷|法拉利|(?<![a-z])tesla(?![a-z])", 8),
        (r"交付量|汽车销量|乘联会|中汽协|燃油车|智能座舱", 12),
    ),
    "newenergy": (
        (r"光伏|光储|储能|风电|风能|风力发电|太阳能|氢能|绿氢|核电|核聚变|可再生能源|绿电|绿色电力|新型电力|动力电池|固态电池|锂电池|锂电|电芯|电池材料|充电桩|换电站|新能源(?!汽?车)", 22),
        (r"宁德时代|比亚迪电池|中创新航|亿纬锂能|国轩高科|隆基|通威|晶科|晶澳|天合光能|阳光电源|金风科技|远景能源|华能|三峡能源|中国广核", 8),
        (r"电网|电力|发电|输电|配电|电价|碳中和|碳交易|碳排放", 10),
    ),
    "finance": (
        (r"央行|美联储|欧洲央行|日本央行|货币政策|降准|降息|加息|利率|汇率|美元(?:指数|兑|走强|走弱|升值|贬值)|人民币(?:兑|中间价|升值|贬值)|通胀|通缩|(?<![a-z])(?:cpi|ppi|gdp)(?![a-z])|非农|外汇储备|财政政策|国债", 18),
        (r"银行|保险|券商|证券公司|金融|信托|理财|私募|公募|基金|债券|信贷|贷款|存款|支付|数字货币|比特币|加密货币|证监会|交易所|资本市场|股市|股指|美股|港股|A股|沪指|纳指|标普|道指", 18),
        (r"(?<![a-z])etf(?![a-z])|股价|股息|市值|涨停|跌停|开盘|收盘|领涨|领跌|板块|指数", 14),
        (r"(?<![a-z])ipo(?![a-z])|上市|融资|财报|营收|利润|并购|收购|回购.*股|股.*回购|申购|校园贷", 4),
    ),
    "logistics": (
        (r"物流|快递|快运|仓储|货运|货代|运价|集装箱|港口|航运|海运|空运|班列|船运|船舶|货轮|货船|商船|码头|干散货|油轮|航道|通航|断航|供应链物流|航空货运|铁路货运|多式联运|冷链", 22),
        (r"顺丰|中通|圆通|申通|韵达|极兔|京东物流|菜鸟|中远海控|中远海运|马士基|达飞|赫伯罗特|万联易达|满帮|货拉拉|(?<![a-z])(?:ups|fedex|dhl)(?![a-z])", 8),
    ),
    "society": (),
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, "未归入关注领域")


def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    # Source/date boilerplate must not become content evidence.
    return re.sub(r"^(?:财联社|金十数据)\d{1,2}月\d{1,2}日电[，,:：\s]*", "", text).strip()


def headline_focus(title: str) -> str:
    title = _clean(title)
    heading = re.match(r"^【([^】]+)】", title)
    return heading[1] if heading and heading[1] not in {"电报解读", "公告全知道", "盘中宝", "机构调研"} else title[:240]


def financial_instrument_news(text: str) -> bool:
    """Fund trading/product notices outrank the sector in the product's name.

    Fund investment in a factory or a portfolio company's own R&D/earnings is
    not a fund transaction notice. Do not turn every occurrence of 基金 into
    finance, or infer fund activity from an unrelated industry article's body.
    """
    if not (LISTED_FUND.search(text) or '基金' in text):
        return False
    if FUND_PORTFOLIO_COMPANY.search(text) and re.search(r"研发|投产|量产|新产品|新品|财报|营收|净利润|交付|销量", text):
        return False
    return bool(FUND_TRADING.search(text) or
                ((LISTED_FUND.search(text) or FUND_PRODUCT.search(text)) and FUND_ISSUANCE.search(text)))


def _sector_scoring_text(text: str) -> str:
    # "ETF重仓企业英伟达公布财报" is about the company, not the fund's
    # own earnings. Remove only that introductory portfolio label for scoring.
    if FUND_PORTFOLIO_COMPANY.search(text) and re.search(r"研发|投产|量产|新产品|新品|财报|营收|净利润|交付|销量", text):
        return FUND_PORTFOLIO_COMPANY.sub('', text)
    return text


def category_scores(text: str) -> dict[str, int]:
    text = _sector_scoring_text(text)
    scores = {
        category: sum(weight for pattern, weight in rules if re.search(pattern, text, re.I))
        for category, rules in RULES.items()
    }
    scores["consumer"] = max(scores["consumer"], digital_consumer_score(text))
    return scores


def _can_use_context(title: str) -> bool:
    # Do not classify a diplomatic meeting, digest, or random public incident
    # from an unrelated AI/finance keyword buried in its longer body.
    return bool(re.search(r"公司|企业|集团|项目|产品|方案|技术|平台|产业|行业|研发|新动作|新突破|新进展", title)
                or (_consumer_anchor(title) and not _private_entertainment(title) and not PUBLIC_INCIDENT.search(title)))


def military_politics_only(title: str) -> bool:
    """News intent precedes incidental technology nouns, regardless of country.

    Battlefield/airspace and diplomatic statements are not technology launches.
    Keep genuine commodity/market consequences, company financial facts and
    concrete industrial policy; a bare 'R&D/production/new model' is no exemption.
    """
    focus = _clean(title)[:400]
    # Fictional/cultural product releases are not battlefield reporting.
    if CULTURAL_PRODUCTS.search(focus) and CULTURAL_MARKET_EVENT.search(focus):
        return False
    economic_impact = bool(ECONOMIC_IMPACT.search(focus))
    company_fact = bool(COMPANY_FINANCIAL_FACT.search(focus))
    if MILITARY_OPERATIONS.search(focus) or MILITARY_AIRSPACE.search(focus):
        return not (economic_impact or company_fact)
    if MILITARY_TOPIC.search(focus):
        return not (economic_impact or company_fact or INDUSTRIAL_POLICY.search(focus))
    if GEOPOLITICS.search(focus) or POLITICAL_ACTIVITY.search(focus):
        return not (ECONOMIC_FACT.search(focus) and max(category_scores(focus).values()) >= 8)
    return False


def classify_text(title: str, summary: str = "") -> str:
    title = _clean(title)
    if rejection_reason(title,summary):
        return ALL_ONLY_CATEGORY
    # Securities misconduct is a financial-market story even if the subject is
    # a non-financial company or the headline emphasizes the criminal sentence.
    if SECURITIES_MISCONDUCT.search(title[:400]) or (
        re.search(r"研究员|基金经理|证券|券商|投研|交易员", title[:240])
        and SECURITIES_MISCONDUCT.search(_clean(summary)[:400])
    ):
        return "finance"
    # Native feed headlines in brackets often precede the full article body.
    focus = headline_focus(title)
    if re.search(r'运价|航运指数|集装箱结算',focus) and not re.search(r'ETF|基金|股价|股票',focus,re.I):
        return 'logistics'
    if re.search(r'(?:AI|人工智能).{0,5}剧.{0,12}(?:激励|奖励|补贴|扶持)计划',focus,re.I):
        return 'consumer'
    broker=bool(re.search(r'投行|券商|证券|小摩|大摩|高盛|摩根|花旗|瑞银|巴克莱|美银|汇丰|杰富瑞|考恩',focus))
    research=bool(re.search(r'目标价|(?:增持|减持|买入|卖出|中性|跑赢|跑输|持有).{0,6}评级',focus))
    physical_price=bool(re.search(r'(?:黄金|白银|原油|天然气|煤炭|铜|铝)(?:价格|目标价)',focus))
    if broker and research and not physical_price:
        return 'finance'
    if re.search(r'上.{0,12}当|AI.{0,30}(?:骗局|诈骗|欺诈|卖惨)|(?:AI|深度伪造).{0,12}(?:骗|冒充)',focus,re.I):
        return public_category(title,summary)
    # AI can be the means of a rumour, not the industry the story is about.
    if re.search(r'(?:AI|人工智能).{0,8}(?:造假|假视频|伪造|谣言)|误信AI|AI影片',focus,re.I):
        consumer = any(re.search(pattern,focus,re.I) for pattern,_ in RULES['consumer'])
        if consumer or re.search(r'顾客|商超|超市|门店|零售|消费者',focus):
            return 'consumer'
        return public_category(title,summary)
    if re.search(r'窃听|骨灰.{0,8}被盗|嫌疑人.{0,12}(?:被捕|逃)|接受审查调查',focus) and not re.search(r'数据泄露|网络攻击|商业机密|供应|生产|经营|营收',focus):
        return public_category(title,summary)
    if re.search(r'(?:建议|呼吁).{0,10}(?:减仓|加仓)|(?:美股|股市|股票).{0,25}(?:抛售|反弹)|(?:抛售|反弹).{0,25}(?:美股|股市|股票)',focus,re.I):
        return 'finance'
    if financial_instrument_news(focus):
        return "finance"
    if military_politics_only(focus):
        return ALL_ONLY_CATEGORY
    if _private_entertainment(focus):
        return ALL_ONLY_CATEGORY
    digital_consumer = digital_consumer_score(focus)
    # A drone/missile attack is public/world news, not a technology launch. An
    # explicitly economic consequence (oil prices, freight, production) can
    # still belong to the relevant industry instead.
    if re.search(r"冲入.*(?:场地|人群)|撞人|枪击|地震|高考|学校.*(?:伤亡|死亡)", focus) and not digital_consumer:
        return public_category(title, summary)
    scores = category_scores(focus)
    industry = [category for category in CATEGORY_ORDER if category not in {"finance", "society"}]
    # An otherwise vague earnings title may use an explicit operating-business
    # description in the source opening, not a later shareholder background.
    if financial_scope(focus) and scores['finance']<=4 and max(scores[k] for k in industry)==0:
        opening=_clean(summary)[:400]
        if re.search(r'主营|从事|业务|产品|行业',opening):
            context=category_scores(opening)
            sector=max(industry,key=lambda k:context[k])
            if context[sector]>=18:
                return sector
    # A sector ETF/stock-price move is a market story, but a company's financial
    # report stays in its industry. Physical commodity quotes stay in 大宗.
    quote = re.search(r"(?<![a-z])ETF(?![a-z])|股价|市值|涨停|跌停|领涨|领跌|板块|股指|指数|概念.{0,10}(?:走强|走高|拉升|上涨|下跌)|科技(?:盘中)?(?:大|暴|直线)?(?:涨|跌)|(?:[中美港A]股|股票).*(?:涨|跌|开盘|收盘)", focus, re.I)
    product = re.search(r"发布|推出|投产|量产|研发|突破|新车|交付|销量|财报|营收|利润", focus)
    if quote and not product and (not scores["commodities"] or re.search(r"(?<![a-z])ETF(?![a-z])|基金|股票|板块|股价", focus, re.I)):
        return "finance"
    # Concrete products/projects take precedence over a multi-sector brand.
    best_industry = max(industry, key=lambda category: scores[category])
    if scores[best_industry] and scores["finance"] <= 4:
        return best_industry
    best = max(CATEGORY_ORDER, key=lambda category: scores[category])
    if scores[best]:
        return best
    # Use the article body only when the headline has no sector evidence; titles
    # such as public accidents must not inherit industries from incidental text.
    public_event = re.search(r"地震|火灾|爆炸|伤亡|死亡|救援|军事|空袭|导弹|停火|选举|总统|外交|法院|高考|教育|医院|学校|体育|比赛|电影|演唱会", focus)
    if public_event or not _can_use_context(focus):
        return public_category(title, summary)
    if military_politics_only(_clean(summary)[:400]):
        return public_category(title, summary)
    context_text = _clean(summary)[:400]
    # For a vague headline, require financial intent in the opening sentence;
    # a later shareholder/fund background paragraph must not hijack the story.
    context_opening = re.split(r'[。；;\n]', context_text, maxsplit=1)[0]
    if financial_instrument_news(context_opening):
        return "finance"
    context = category_scores(context_text)
    if _consumer_anchor(focus):
        context["consumer"] = max(context["consumer"], digital_consumer_score(focus + " " + _clean(summary)[:400]))
    best = max(CATEGORY_ORDER, key=lambda category: context[category])
    return best if context[best] >= 8 else public_category(title, summary)


def classify_event(title: str, items: Iterable[object]) -> str:
    """The selected headline leads; other reports supply context, not vote spam."""
    reports = list(items)
    matching = next((item for item in reports if item.title == title), None)
    if rejection_reason(_clean(title), matching.summary if matching else ""):
        return ALL_ONLY_CATEGORY
    if financial_instrument_news(headline_focus(title)):
        return "finance"
    if military_politics_only(title):
        return ALL_ONLY_CATEGORY
    primary = classify_text(title, matching.summary if matching else "")
    if primary not in {"society", ALL_ONLY_CATEGORY}:
        return primary
    # Only a genuinely vague headline can borrow a sector from its same-event
    # reports. A clearly public story is not reclassified by a longer repost.
    if not _can_use_context(_clean(title)) or re.search(r"地震|火灾|爆炸|死亡|救援|军事|军方|空袭|导弹|停火|选举|总统|外交|高考|教育|医院|学校|比赛|电影|演唱会", _clean(title)):
        return primary
    alternatives = {classify_text(item.title, item.summary) for item in reports}
    alternatives.difference_update({"society", ALL_ONLY_CATEGORY})
    return next(iter(alternatives)) if len(alternatives) == 1 else primary
