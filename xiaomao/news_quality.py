"""Conservative source association and explicit subscription-scoped feedback effects."""
from copy import deepcopy
from difflib import SequenceMatcher
import re
import unicodedata

from .web_search import _citation_identity, _public_url


def _key(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    # Punctuation can carry the fact: 1.2 != 12, -5% != 5%, 3-2 != 32,
    # and <= is the reverse of >=. Preserve it and word boundaries rather
    # than using the lossy headline/search normalization as event identity.
    # A publisher changing only the final full stop/exclamation mark has not
    # established a factual update. Remove that narrow prose suffix; retain
    # internal decimal/score/date punctuation, signs, operators and boundaries.
    return ' '.join(text.split()).rstrip(' .!。！')


def comparison_facts(text):
    # Retain digit boundaries AND every punctuation/operator token. This
    # protects a headline-only numeric change even when bodies are identical
    # and headline similarity would otherwise tolerate the small difference.
    return re.findall(r'\d+|[^\w\s]', _key(text))


def _stance(text):
    return re.findall(r'不|未|否认|拒绝|取消|撤回|恢复|修正|更新|评论|质疑|\b(?:not|no|never|without|denies|cancelled|withdrawn|updated|correction|opinion|concern)\b',
                      unicodedata.normalize('NFKC', text).casefold())


def same_event(one, two):
    """Require matching concrete headline AND near-identical evidence, never keywords alone."""
    title_one, title_two = _key(one.get('title', '')), _key(two.get('title', ''))
    body_one, body_two = _key(one.get('eventEvidence') or one.get('summary', '')), _key(two.get('eventEvidence') or two.get('summary', ''))
    if min(len(title_one), len(title_two)) < 10 or min(len(body_one), len(body_two)) < 12: return False
    if comparison_facts(title_one) != comparison_facts(title_two) or comparison_facts(body_one) != comparison_facts(body_two): return False
    if one.get('publishedAt') and two.get('publishedAt') and abs(one['publishedAt'] - two['publishedAt']) > 24 * 3600: return False
    if title_one != title_two and re.search(r'评论|观点|质疑|反对|分析|opinion|concern|critic|analysis|why', title_one + title_two): return False
    if _stance(one.get('title', '')) != _stance(two.get('title', '')): return False
    # Even a tiny negation, decimal, sign or correction in a long passage can
    # reverse the claim. Only case/Unicode/whitespace normalization is used.
    return SequenceMatcher(None, title_one, title_two, autojunk=False).ratio() >= .94 and body_one == body_two


def substantive_update(one, previous):
    new_body = _key(one.get('eventEvidence') or one.get('summary', ''))
    old_body = _key(previous.get('eventEvidence') or previous.get('summary', ''))
    if min(len(new_body), len(old_body)) < 12: return False
    title_changed = _key(one.get('title', '')) != _key(previous.get('title', ''))
    facts_changed = comparison_facts(new_body) != comparison_facts(old_body)
    stance_changed = _stance(one.get('eventEvidence') or one.get('summary', '')) != _stance(previous.get('eventEvidence') or previous.get('summary', ''))
    return title_changed or facts_changed or stance_changed or SequenceMatcher(None, new_body, old_body, autojunk=False).ratio() < .96


def article_urls(article):
    return list(dict.fromkeys(_citation_identity(url) for url in [article.get('url'),
        *(row.get('url') for row in article.get('sources', []) if isinstance(row, dict))] if _public_url(url)))[:8]


def source_metadata(row):
    metadata = {key: row.get(key, '') for key in ('url', 'source', 'title', 'sourceType', 'publishedAt', 'timeLabel', 'feedId')}
    metadata['collectedVia'] = row.get('collectedVia') or [row.get('sourceType', 'search')]
    return metadata


def group_events(rows):
    groups = []
    for original in rows:
        row = deepcopy(original)
        match = next((previous for previous in groups if same_event(previous, row)), None)
        if match is None:
            row['sources'] = [source_metadata(row)]
            row['merge'] = {'confidence': 'unmerged', 'reason': '证据不足时独立保留；仅关联标题与正文摘录高度一致的转载'}
            groups.append(row)
        elif len(match['sources']) < 8 and row['url'] not in article_urls(match):
            match['sources'].append(source_metadata(row))
            match['collectedVia'] = list(dict.fromkeys([*(match.get('collectedVia') or [match.get('sourceType', 'search')]),
                                                       *(row.get('collectedVia') or [row.get('sourceType', 'search')])]))
            if row.get('sourcePriority', 0) > match.get('sourcePriority', 0):
                match['sourcePriority'] = row['sourcePriority']
                match['feedId'] = row.get('feedId', '')
            match['merge'] = {'confidence': 'high', 'reason': '标题、正文摘录和数字一致；保守关联为同一事件的多个来源',
                              'sourceCount': len(match['sources'])}
    return groups


def apply_feedback(rows, preferences):
    result = []
    for row in rows:
        score, effects, blocked = int(row.get('sourcePriority', 0)), [], False
        for preference in preferences:
            scope, value, target = preference.get('scope'), preference.get('value'), preference.get('target')
            matches = ((scope == 'article' and target in article_urls(row))
                       or (scope == 'source' and target == row.get('source'))
                       or (scope == 'topic' and target == row.get('category', '') + '\n' + row.get('topic', '')))
            if not matches: continue
            if scope == 'article' and value in ('irrelevant', 'duplicate'): blocked = True
            else: score += (10 if scope == 'article' else 1) * (1 if value == 'like' else -1)
            effects.append({'scope': scope, 'value': value})
        if not blocked:
            row['preferenceEffects'] = effects
            row['_rank'] = score
            result.append(row)
    return sorted(result, key=lambda row: -row['_rank'])
