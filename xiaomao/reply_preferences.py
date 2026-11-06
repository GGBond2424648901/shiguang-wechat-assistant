"""Conversation expression choices, independent of model and desktop adapters."""

REPLY_MODES = {
    'natural': '自然参与',
    'mentions': '仅 @ 或点名',
    'all': '逐条回复',
}
STICKER_FREQUENCIES = {
    'requested': '仅按需',
    'occasional': '偶尔',
    'natural': '自然',
    'expressive': '爱用表情',
}
RULE_VALUES = {'reply_mode': REPLY_MODES, 'sticker_frequency': STICKER_FREQUENCIES}

STICKER_PACING = {'requested': (0, 0), 'occasional': (120, 3), 'natural': (60, 2), 'expressive': (20, 0)}


def sticker_instruction(frequency):
    descriptions={
        'requested':'只有当前成员明确索要表情时才选图，其余用文字或安静。',
        'occasional':'以文字接话，只有情绪特别贴合时偶尔配一张表情，不为凑频率而选图。',
        'natural':'按这句话的意思选择文字或表情，连续几轮不用图也正常。',
        'expressive':'喜欢用表情表达情绪。遇到贴切的共鸣、玩笑、庆祝或打招呼，可以直接用一张图接住，'
                     '不必先凑一段文字，也不必另外解释表情画面。有具体问题仍先答问题，不用图代替必要信息。',
    }
    return ('本会话表情偏好：'+STICKER_FREQUENCIES[frequency]+'。'+descriptions[frequency]+
            '频率是表达偏好，不是每轮必须发图。严肃求助和技术细节优先文字。明确索图仍按当前请求处理。')


def validate_rule_changes(changes, *, allow_inherit=True):
    if not isinstance(changes, dict) or set(changes)-set(RULE_VALUES):
        raise ValueError('回复规则字段无效')
    for key, value in changes.items():
        if allow_inherit and value is None:
            continue
        if not isinstance(value, str) or value not in RULE_VALUES[key]:
            raise ValueError('回复方式或表情频率无效')
    return dict(changes)


def validate_rule_map(rules):
    if not isinstance(rules, dict) or len(rules)>64:
        raise ValueError('会话回复规则无效')
    for scope, preferences in rules.items():
        if (not isinstance(scope, str) or not 3<=len(scope)<=513 or scope.count('/')!=1
                or not all(scope.split('/')) or any(ord(c)<32 for c in scope)):
            raise ValueError('会话回复规则身份无效')
        validate_rule_changes(preferences, allow_inherit=False)

