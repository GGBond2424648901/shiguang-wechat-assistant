"""Validated public-news subscriptions, independent of conversation settings."""

import re
import unicodedata
import uuid

from .news_schedule import normalize_schedule
from .web_search import _public_url, public_query


CATEGORIES = [
    {'id': key, 'label': label, 'query': query}
    for key, label, query in (
        ('game', '游戏', '游戏 新作 资讯'), ('anime', '动漫', '动漫 新番 资讯'),
        ('music', '音乐', '音乐 新专辑 演出 资讯'), ('esports', '电竞', '电竞 赛事 资讯'),
        ('food', '美食', '美食 餐饮 资讯'), ('world', '国际', '国际 新闻'),
        ('technology', '科技', '科技 最新资讯'), ('ai', '人工智能', '人工智能 最新资讯'),
        ('digital', '数码', '数码 产品 发布'), ('film', '影视', '影视 新片 资讯'),
        ('finance', '财经', '财经 新闻'), ('sports', '体育', '体育 赛事 新闻'),
        ('travel', '旅游', '旅游 资讯'), ('life', '生活', '生活 消费 资讯'),
        ('education', '教育', '教育 新闻'), ('science', '科学', '科学 研究 新发现'),
        ('auto', '汽车', '汽车 新车 资讯'), ('health', '健康', '健康 医学 研究 资讯'),
    )
]
_IDS = {row['id'] for row in CATEGORIES}


def default_subscription():
    return dict(id=uuid.uuid4().hex, name='每日资讯', enabled=False, selectionMode='multiple',
                categories=['technology'], categoryTopics={}, keywords='', excludeKeywords='', sourceDomains='',
                time='09:00', times=['09:00'], timeRanges=[],
                weekdays=list(range(7)), itemCount=5, lookbackHours=24,
                style='brief', includeLinks=True, recipients=[])


def _text(value, label, limit, *, required=False):
    if (not isinstance(value, str) or len(value) > limit
            or any(unicodedata.category(char).startswith('C') for char in value)):
        raise ValueError(label + '格式无效或过长')
    value = value.strip()
    if required and not value:
        raise ValueError('请填写' + label)
    return value


def _public_terms(value, label, limit):
    value = _text(value, label, limit)
    # The public-query validator has a 120-character transport limit. The
    # overlapping windows preserve that privacy validation for longer drafts.
    if value and any(public_query(window) is None for window in (value[:120], value[-120:])):
        raise ValueError(label + '只允许公开主题，不得包含私人标识、联系方式或密钥')
    return value


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError('订阅配置必须为对象')
    defaults = default_subscription()
    clean = {key: value.get(key, default) for key, default in defaults.items()}
    identity = clean['id']
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise ValueError('订阅编号无效')
    clean['name'] = _text(clean['name'], '订阅名称', 40, required=True)
    for key in ('enabled', 'includeLinks'):
        if type(clean[key]) is not bool:
            raise ValueError('订阅开关必须是布尔值')
    if clean['selectionMode'] not in ('single', 'multiple'):
        raise ValueError('方向选择模式无效')
    categories = clean['categories']
    if (not isinstance(categories, list) or len(categories) > 20
            or any(not isinstance(key, str) or key not in _IDS for key in categories)):
        raise ValueError('资讯方向无效，最多选择二十项')
    clean['categories'] = list(dict.fromkeys(categories))
    if clean['selectionMode'] == 'single' and len(clean['categories']) > 1:
        raise ValueError('单选模式只能选择一个方向')
    topics = clean['categoryTopics']
    if not isinstance(topics, dict):
        raise ValueError('分类细分主题必须为对象')
    clean_topics = {}
    for category, items in topics.items():
        if not isinstance(category, str) or category not in _IDS:
            raise ValueError('细分主题的资讯方向无效')
        if not isinstance(items, list) or len(items) > 8:
            raise ValueError('每个方向最多设置八个细分主题')
        normalized = []
        for item in items:
            topic = _public_terms(item, '细分主题', 60)
            if not topic:
                raise ValueError('请填写细分主题')
            if topic not in normalized:
                normalized.append(topic)
        clean_topics[category] = normalized
    if sum(len(items) for items in clean_topics.values()) > 24:
        raise ValueError('细分主题总计最多二十四个')
    clean['categoryTopics'] = clean_topics
    clean['keywords'] = _public_terms(clean['keywords'], '关键词', 180)
    clean['excludeKeywords'] = _public_terms(clean['excludeKeywords'], '排除词', 120)
    if not clean['categories'] and not clean['keywords']:
        raise ValueError('请至少选择一个方向或填写公开关键词')
    domains = _text(clean['sourceDomains'], '来源域名', 600)
    hosts = []
    for host in re.split(r'[,，]', domains):
        host = host.strip().lower().rstrip('.')
        if not host:
            continue
        if (not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', host)
                or not _public_url('https://' + host)):
            raise ValueError('来源请填写公开域名，不含协议、端口或路径')
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > 5:
        raise ValueError('来源域名最多五个')
    clean['sourceDomains'] = ','.join(hosts)
    clean.update(normalize_schedule(value))
    weekdays = clean['weekdays']
    if (not isinstance(weekdays, list) or not 1 <= len(weekdays) <= 7
            or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)):
        raise ValueError('请至少选择一个有效星期')
    clean['weekdays'] = sorted(set(weekdays))
    if type(clean['itemCount']) is not int or not 1 <= clean['itemCount'] <= 12:
        raise ValueError('资讯条数应为一至十二条')
    if type(clean['lookbackHours']) is not int or clean['lookbackHours'] not in (24, 48, 72):
        raise ValueError('回看时长应为二十四、四十八或七十二小时')
    if clean['style'] not in ('brief', 'persona'):
        raise ValueError('简报风格无效')
    recipients = clean['recipients']
    if (not isinstance(recipients, list) or len(recipients) > 8
            or any(not isinstance(item, str) or not 1 <= len(item) <= 160
                   or item != item.strip()
                   or any(unicodedata.category(char).startswith('C') for char in item)
                   for item in recipients)):
        raise ValueError('推送目标无效，最多选择八个已确认会话')
    clean['recipients'] = list(dict.fromkeys(recipients))
    if clean['enabled'] and not clean['recipients']:
        raise ValueError('启用订阅前请选择推送目标')
    return clean
