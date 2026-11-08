"""A semantic execution reminder; full enabled persona sources stay in the prefix.

Never classify a turn's mood or choose its voice from a keyword list.
"""
import re

from .persona_modules import load_modules


_FICTION_REQUEST = re.compile(r'(?:写|编|续写|创作|讲)(?:一个|一段|个|篇|一篇)?[^\n]{0,12}(?:故事|小说|虚构|独白)|(?:原样|逐字)(?:转述|引用|复述|输出)')
# Narrowly guard observed false autobiography/tool use, not imaginative language.
_UNFOUNDED = re.compile(r'(?:我|本鱼|肥鱼)小时候|(?:^|[，,。！？\n])\s*我?(?:后来|刚才|刚刚)一查|我(?:今天|刚刚|刚才|昨天)?(?:在路上|出门时|刷到(?:个|一条|一个)?视频)')


def unsupported_activity(text, messages):
    latest = next((m.text for m in reversed(messages) if m.direction == 'in'), '')
    if _FICTION_REQUEST.search(latest):
        return False
    # Quoted statements are not necessarily the assistant's own claims.
    prose = re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"\n]*"', '', text)
    for match in _UNFOUNDED.finditer(prose):
        preceding = prose[max(0,match.start()-12):match.start()]
        if re.search(r'(?:不能|不会|不该|不可以|没有|并非|不是|不曾)(?:说|假装|声称|真的|所谓)?[^，。！？\n]{0,6}$',preceding):
            continue
        return True
    return False

_EXECUTION = (
    '本轮人格执行：完整启用原稿已经加载，身份、语气、兴趣与表达强度按原稿及当前语境决定。'
    '结合完整语义、否定、引用和同一成员前文回应，具体任务讲准讲全；示例不是本会话经历。'
    '近期表达仅供发现空洞复读，不额外添加原稿未要求的词语、动作、颜文字或问句配额，也不额外强制轮换或改变原稿风格。'
    '仅输出本轮协议要求的答复，不解释内部执行提示。'
)


def voice_focus(root, messages, *, proactive=False):
    state = load_modules(root)
    return _EXECUTION if state is not None else ''
