"""A semantic execution reminder; full enabled persona sources stay in the prefix.

Never classify a turn's mood or choose its voice from a keyword list.
"""
import re

from .persona_modules import load_modules


_FICTION_REQUEST = re.compile(r'(?:写|编|续写|创作|讲)(?:一个|一段|个|篇|一篇)?[^\n]{0,12}(?:故事|小说|虚构|独白)|(?:原样|逐字)(?:转述|引用|复述|输出)')
# Narrowly guard observed false autobiography/tool use, not imaginative language.
_UNFOUNDED = re.compile(r'(?:我|本鱼|肥鱼)小时候|(?P<search>(?:^|[，,。！？\n])\s*我?(?:后来|刚才|刚刚)一查)|我(?:今天|刚刚|刚才|昨天)?(?:在路上|出门时|刷到(?:个|一条|一个)?视频)')


def unsupported_activity(text, messages, *, searched=False):
    latest = next((m.text for m in reversed(messages) if m.direction == 'in'), '')
    if _FICTION_REQUEST.search(latest):
        return False
    # Quoted statements are not necessarily the assistant's own claims.
    prose = re.sub(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|"[^"\n]*"', '', text)
    for match in _UNFOUNDED.finditer(prose):
        if searched and match.group('search'):
            continue
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

_PRESENCE_EXECUTION = (
    '已启用 V6.1 角色临场感。轻松闲聊先从眼前的事说出自己的具体感受或小愿望，接住角色小剧场；'
    '已经接住玩笑后，也不用再拆解它的操作逻辑。惊喜可以只开心或期待，想问再自然开口，不把分享变成索取细节。'
    '对方说保密就接受，累了、要安静或要道别就接眼前的心情，不顺带再提旧话头。'
    '隔天聊回约定时，用可靠原话里的具体细节接续，未知内容留白，不必催揭晓。'
    '揭晓后围绕对方刚说的用途表现期待或俏皮联想；未经确认的旧事只当想象，'
    '想试一个功能可以说好想试，实际操作与发送须有宿主记录。'
    '对惊喜揭晓、夸奖等有具体内容的互动，先给贴合的角色文字，即使本会话爱用表情也一样；'
    '候选素材只在情绪与实际配字都贴合这次答复时补充，同一个物件或标签不算贴合，没合适素材就留空。'
    '动作和俏皮可以自然融进表达。避免照搬示例、接连用同一个开头或把上次的反应换几个词重说。'
    '对方要求停玩笑就收住，自己的玩笑由自己认领，留出空间，先不追问。明确索图、只要文字、只给一行或原样等要求优先，'
    '按指定内容与格式直接回应。实际任务缺必需信息时再具体澄清。'
)


def presence_enabled(state):
    return state is not None and any(m.enabled and m.id=='v6_1_presence' for m in state.modules)


def voice_focus(root, messages, *, proactive=False):
    state = load_modules(root)
    if state is None:
        return ''
    if presence_enabled(state):
        return _EXECUTION+'\n'+_PRESENCE_EXECUTION
    return _EXECUTION
