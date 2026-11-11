from copy import deepcopy

import pytest

from xiaomao.news import CATEGORIES, default_subscription, validate_subscription


def test_new_subscriptions_are_independent_disabled_drafts_with_no_recipients():
    one, two = default_subscription(), default_subscription()
    one['categories'].append('game')
    assert not two['enabled'] and two['recipients'] == []
    assert one['id'] != two['id']
    assert 'game' not in two['categories']
    assert validate_subscription(two) == two
    assert {'game', 'anime', 'music', 'esports', 'food', 'world', 'technology', 'ai',
            'digital', 'film', 'finance', 'sports', 'travel', 'life', 'education',
            'science', 'auto', 'health'} <= {row['id'] for row in CATEGORIES}


@pytest.mark.parametrize('changes', [
    {'name': ''}, {'name': 'x' * 41}, {'enabled': 1}, {'selectionMode': 'random'},
    {'selectionMode': 'single', 'categories': ['game', 'music']},
    {'categories': ['unknown']}, {'categories': [], 'keywords': ''},
    {'keywords': 'x' * 181}, {'keywords': '联系 wxid_secret_person'},
    {'keywords': 'sk-private-secret'}, {'excludeKeywords': 'x' * 121},
    {'sourceDomains': '127.0.0.1'}, {'sourceDomains': 'https://example.org/path'},
    {'sourceDomains': 'a.org,b.org,c.org,d.org,e.org,f.org'},
    {'time': '24:00'}, {'time': '9:00'}, {'weekdays': [7]}, {'weekdays': [True]},
    {'weekdays': []}, {'itemCount': 0}, {'itemCount': True}, {'itemCount': 13},
    {'lookbackHours': 12}, {'style': 'invent'}, {'includeLinks': 'yes'},
    {'recipients': ['x'] * 9}, {'recipients': ['x\n']}, {'enabled': True},
    {'id': '../escape'},
])
def test_invalid_subscriptions_are_rejected_with_readable_errors(changes):
    config = default_subscription()
    config.update(changes)
    if 'time' in changes:
        config.pop('times', None)
        config.pop('timeRanges', None)
    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        validate_subscription(config)


def test_validation_normalizes_public_inputs_without_trusting_web_targets():
    config = default_subscription()
    config.update(categories=['game', 'game'], keywords='  独立游戏  ',
                  sourceDomains='WWW.Example.org，news.example.org',
                  weekdays=[4, 0, 4], recipients=['chat-1', 'chat-1'],
                  targets=[{'session': 'injected', 'scope': 'owner'}], revision=999)
    before = deepcopy(config)
    clean = validate_subscription(config)
    assert clean['categories'] == ['game']
    assert clean['keywords'] == '独立游戏'
    assert clean['sourceDomains'] == 'www.example.org,news.example.org'
    assert clean['weekdays'] == [0, 4] and clean['recipients'] == ['chat-1']
    assert 'targets' not in clean and 'revision' not in clean
    assert config == before


def test_keyword_only_subscription_and_enabled_target_are_supported():
    config = default_subscription()
    config.update(categories=[], keywords='开源 科学', recipients=['chat-1'], enabled=True)
    assert validate_subscription(config)['enabled']


def test_default_schedule_lists_are_independent_and_subscription_validation_preserves_rules():
    one, two = default_subscription(), default_subscription()
    assert one.get('times') == ['09:00'] and one.get('timeRanges') == []
    one['times'].append('18:00')
    one['timeRanges'].append({'start': '10:00', 'end': '11:00', 'intervalMinutes': 20})
    assert two['times'] == ['09:00'] and two['timeRanges'] == []
    before = deepcopy(one)
    clean = validate_subscription(one)
    assert clean['times'] == ['09:00', '18:00']
    assert clean['timeRanges'] == [{'start': '10:00', 'end': '11:00', 'intervalMinutes': 20}]
    assert one == before


def test_subscription_validation_migrates_legacy_time_and_accepts_explicit_range_only_schedule():
    legacy = default_subscription()
    legacy.pop('times', None)
    legacy.pop('timeRanges', None)
    legacy['time'] = '17:30'
    clean = validate_subscription(legacy)
    assert clean.get('times') == ['17:30'] and clean.get('timeRanges') == []
    ranges = [{ 'start': '23:00', 'end': '01:00', 'intervalMinutes': 30}]
    clean = validate_subscription({**legacy, 'times': [], 'timeRanges': ranges})
    assert clean.get('times') == [] and clean.get('timeRanges') == ranges and clean['time'] == '23:00'


def test_category_topics_are_independent_public_drafts_and_preserve_unselected_categories():
    config, other = default_subscription(), default_subscription()
    assert config.get('categoryTopics') == {}
    config.update(categories=['game', 'sports'], categoryTopics={
        'game': [' 三角洲 ', '三角洲', '独立游戏'], 'sports': ['摩托张雪'], 'music': ['摇滚']})
    before = deepcopy(config)
    clean = validate_subscription(config)
    assert clean['categoryTopics'] == {
        'game': ['三角洲', '独立游戏'], 'sports': ['摩托张雪'], 'music': ['摇滚']}
    assert other['categoryTopics'] == {} and config == before
    legacy = {key: value for key, value in config.items() if key != 'categoryTopics'}
    assert validate_subscription(legacy).get('categoryTopics') == {}


@pytest.mark.parametrize('topics', [
    None, True, [], {'unknown': ['公开主题']}, {'game': '三角洲'}, {'game': ('三角洲',)},
    {'game': [True]}, {'game': ['']}, {'game': ['  ']}, {'game': ['题' * 61]},
    {'game': ['三角洲\n']}, {'game': ['联系 wxid_private_person']}, {'game': ['sk-private-secret']},
    {'game': [f'公开主题{index}' for index in range(9)]},
    {'game': [f'游戏主题{index}' for index in range(8)],
     'sports': [f'体育主题{index}' for index in range(8)],
     'music': [f'音乐主题{index}' for index in range(8)], 'science': ['额外主题']},
])
def test_category_topics_reject_unknown_private_malformed_or_unbounded_values(topics):
    config = default_subscription()
    config['categoryTopics'] = topics
    with pytest.raises(ValueError, match=r'[\u3400-\u9fff]'):
        validate_subscription(config)


def test_category_topics_accept_twenty_four_public_topics_and_sixty_character_boundary():
    config = default_subscription()
    config['categoryTopics'] = {
        'game': ['题' * 60, *[f'游戏主题{index}' for index in range(7)]],
        'sports': [f'体育主题{index}' for index in range(8)],
        'music': [f'音乐主题{index}' for index in range(8)]}
    assert validate_subscription(config).get('categoryTopics') == config['categoryTopics']
