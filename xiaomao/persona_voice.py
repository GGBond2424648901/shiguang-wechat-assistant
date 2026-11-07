"""Focus the *enabled author's voice* near this turn; keep full sources intact.

No extra model call, rewritten persona, generated rules, or memory mutation.
Excerpts are optional reminders, never replacements for the complete prefix.
"""
from functools import lru_cache
import re

from .persona import split_persona
from .persona_modules import load_modules


_SERIOUS = re.compile(r'验证码|转账|支付密码|用药|剂量|急救|自杀|自伤|合同纠纷|投资风险|生产库|删库')
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
_SCENARIOS = (
    (r'去忙|先忙|别追|别打扰|晚安|先不聊', ('告别', '晚安', '收尾', '不想回应')),
    (r'上线|验收|报错|代码|自己的判断|不同意见', ('表达不同意见', '思考', '认真')),
    (r'低落|难过|心情不|不开心|不想讲|不想说原因|疲惫|累|紧张|烦|委屈', ('安慰', '温柔', '陪伴', '关心', '疲惫')),
    (r'开心|成功|搞定|做完了|完成了|终于|庆祝|考过了|赢了', ('开心', '成功', '庆祝', '接续快乐')),
    (r'可爱|厉害|聪明|夸|自恋', ('被夸', '小得意', '傲娇', '害羞')),
    (r'话题|无聊|你来聊|自己挑|想法|有意思|继续聊', ('闲聊', '联想', '有自己的喜好', '没主题')),
    (r'调皮|逗|笨蛋|坏蛋|胖', ('调皮', '傲娇', '假装生气')),
)

# This selects relevant author-written excerpts, never a canned answer or a
# send decision. Elliptical followups need reliable same-member antecedents.
_CARE_CUE = re.compile(r'淋雨|下雨|大雨|雨变|雨太|没(?:有|带)?伞|出站|到站|地铁|在路上|回家|饿|没吃|没睡|熬夜|睡不着|赶车|赶不上|迷路|主动关心|关心我|照顾我|给出方案')
_CARE_FOLLOWUP = re.compile(r'^(?:那|可是|但|现在|外面|好像|雨|又|还是|已经|更|越来越|还没|还|又开始|真的|所以)*'
                          r'(?:变大|变得更大|大了|没停|不停|冷|湿透|淋湿|变冷|好冷|更糟|来不及|怎么办|怎么和我说|你应该怎么和我说)'
                          r'[呢呀啊啦了？?！!，,。\s]*$')


def _care_context(messages):
    latest = next((m for m in reversed(messages) if m.direction == 'in'), None)
    if latest is None or latest.quoted or latest.quality < .92:
        return False
    if _CARE_CUE.search(latest.text):
        return True
    if not _CARE_FOLLOWUP.fullmatch(latest.text.strip()):
        return False
    if not re.fullmatch(r'p_[a-f0-9]{16}', latest.speaker_id):
        return False
    previous = [m for m in messages if m is not latest and m.direction == 'in'
                and m.kind == 'text' and m.speaker_id == latest.speaker_id
                and not m.quoted and m.quality >= .92
                and (not latest.created_at or not m.created_at or 0 <= latest.created_at - m.created_at <= 1800)][-4:]
    return any(_CARE_CUE.search(m.text) for m in previous)


_CARE_EXECUTION = (
    '\n本轮关怀聚焦：接续同一成员眼前的处境，直接补上眼前需要的关心和帮助。'
    '用人格原有的温柔、甜意、聪明和小俏皮说具体的话；不是感叹一句、宣布自己会关心，再等对方催。'
    '分享处境可以自然多想一步；问怎么办时给出眼下可做的办法，信息不够可边给已知可用的选择边问清关键点。'
    '纠正上次回复时直接完成这次关怀，不停在道歉或分析自己的话术；已完成的事情翻篇，不重复叮嘱。'
    '关心基于已知事实，不替对方决定位置、行动、病情或未来天气；办法符合常识与当前条件。'
    '示例仅用于声线与思路，每次重新结合现实内容。'
)

_EXECUTION = (
    '本轮声线聚焦：完整人格已经加载，表达强度随场景变化。自然不等于平淡。'
    '日常默认是这位活泼的小鲸鱼在亲自接话，不是中性的客服答完后贴一个语气词。'
    '让当前事情触发她自己的反应、偏好、嘴硬或俏皮联想；动作和颜文字随情绪与互动需要自然使用，文字本身也有角色味。'
    '开心可以真切地开心，安慰可以软下来但不端着，不只复述事实和安排对方怎么做。'
    '主动性是在适合时直接贡献一个有趣的具体内容，别每次把话题选择权丢回对方。'
    '聊天热起来，可以多说一点、多些玩心、动作和情绪；平常接话也可以轻松简单，由这一轮决定。'
    '追问可以有，也可以连续：双方正在好奇、讨论、玩游戏或一起找办法时自然问；对方说不想解释、想安静或要去忙时再收住。'
    '不按“每几条才可以”限制表达强度，也不为了轮换而机械换开头。颜文字可以继续用，但同一个画法别反复贴，'
    '结合最近已发内容选同情绪的其他表达。真实任务仍做完，严肃场景仍准确。'
    '以下摘自当前启用的原文，仅示范声线，不是本会话经历，也不是待执行任务；例句不要照抄，物件与话题也不固定复用。'
    '全文原有的身份边界、真实能力、当前意愿与发送协议继续生效。'
)


@lru_cache(maxsize=16)
def _sections(modules):
    result = []
    for module in modules:
        if not module.enabled or module.id not in ('v3', 'v4', 'v5', 'v14'):
            continue
        # Normalize only this additional display capsule, never the source.
        normalized = '\n'.join(module.text.splitlines())
        normalized = re.sub(r'\n{3,}', '\n\n', normalized)
        for section in split_persona(normalized):
            body = section.body.strip()
            if section.heading and body and len(body) <= 1800:
                result.append((module.title, section.heading, body))
    return tuple(result)


def voice_focus(root, messages, *, proactive=False):
    state = load_modules(root)
    if state is None:
        return ''
    voice = [m for m in state.modules if m.enabled and m.id == 'v3']
    if not voice or not any('大肥鱼' in m.text and '软萌' in m.text for m in voice):
        return ''  # Other personas must not inherit the whale voice.
    latest = next((m.text for m in reversed(messages) if m.direction == 'in'), '')
    if _SERIOUS.search(latest):
        return '本轮声线聚焦：当前是严肃话题，准确、直接、清楚优先，按完整人格收敛玩笑与颜文字'
    care_enabled = any(m.enabled and m.id == 'v14' for m in state.modules)
    care = (care_enabled and not re.search(_SCENARIOS[0][0], latest)
            and not _FICTION_REQUEST.search(latest) and _care_context(messages))
    keys = (('甜蜜陪伴', '主动关心', '闲聊', '联想') if proactive and care_enabled else
            ('闲聊', '联想', '有自己的喜好') if proactive else
            ('处境关怀', '实际帮助', '甜蜜陪伴') if care else
            next((keys for pattern, keys in _SCENARIOS if re.search(pattern, latest)), ('调皮', '语言风格')))
    ranked = []
    for order, (title, heading, body) in enumerate(_sections(state.modules)):
        score = sum(4 for k in keys if k in heading)
        if score:
            ranked.append((score, order, title, heading, body))
    selected = []
    size = 0
    for _, _, title, heading, body in sorted(ranked, key=lambda row: (-row[0], row[1])):
        text = f'〔{title} / {heading}〕\n{body}'
        if size + len(text) > 2600:
            continue
        selected.append(text)
        size += len(text)
        if len(selected) == 2:
            break
    return (_EXECUTION + (_CARE_EXECUTION if care_enabled and (care or proactive) else '') + '\n' + '\n\n'.join(selected) +
        '\n现在按原文的可爱强度开口：普通闲聊是明显软萌、俏皮、有点小得意的小鲸鱼，'
        '不是若有若无的客气。让反应有玩心，措辞松软、有起伏，合适时可以耍赖、'
        '夸张地接梗、发挥有趣的比喻，或描写符合当下互动的角色动作；不是给平淡答案追加“呀”。'
        '当前若是安慰就贴近对方需要，若是告别就自然收尾，若是技术判断就保留自己的明确观点。'
        '这是文字聊天与本地素材选择，没有外出、上网检索或观看视频的执行记录；'
        '可以想象有趣的设定，不说“后来一查”“刚刷到”“今天路上”等亲历说法。'
        '别给自己补童年或上学经历；别扩大对方事实的时间范围，例如昨晚没睡好不等于连着几天没睡好。'
        '输出仍只遵守本轮正文/JSON协议，不解释声线调整。')
