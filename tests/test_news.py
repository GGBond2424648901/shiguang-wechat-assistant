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
