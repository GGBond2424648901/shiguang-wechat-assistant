"""Curated expression choices, not a requirement to decorate every message.

Fallbacks keep the original emotion and never invent an expression for plain text.
No external service, cross-conversation state, or persistent usage counters.
"""
import hashlib
import re


CATEGORIES = {
    '亲近・软萌': (
        '(｡･ω･｡)', '(◕‿◕)', '(●´ω｀●)', '(｡◕‿◕｡)', '(´｡• ω •｡`)',
        '(◍•ᴗ•◍)', '(｡•ᴗ•｡)', '(´,,•ω•,,)', '(o˘◡˘o)', '(๑╹ω╹๑)',
        '(´꒳`)', '(ꈍᴗꈍ)', '(｡´‿`｡)', '(◡‿◡✿)', '(๑´ㅂ`๑)', '(´ω｀*)',
    ),
    '开心・庆祝': (
        '(≧▽≦)', '(＾▽＾)', '(ﾉ´ヮ`)ﾉ*:･ﾟ✧', 'ヽ(✿ﾟ▽ﾟ)ノ', '(｡´∀｀)ﾉ',
        '(๑˃̵ᴗ˂̵)و', '(☆▽☆)', '(´∀｀*)', '(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧', '٩(｡•́‿•̀｡)۶',
        '(★ω★)', 'o(≧∇≦o)', 'ヽ(・∀・)ﾉ', '(⌒▽⌒)☆', '(≧◡≦)', '(*≧ω≦*)',
    ),
    '得意・小骄傲': (
        '(￣▽￣)', '(￣︶￣)', '(๑•̀ㅂ•́)و✧', '( •̀ ω •́ )✧', '(｡•̀ᴗ-)✧',
        '(´꒳`)✧', '(￣ー￣)ｂ', '(⌐■_■)', '(๑•̀ㅁ•́ฅ)', '(ง •̀_•́)ง',
        '( •̀ .̫ •́ )', '(๑¯◡¯๑)', '(｀・ω・´)ゞ', '(￣^￣)ゞ', '(｡•̀ᴗ•́｡)', '(๑•̀ㅂ•́)و',
    ),
    '害羞・被夸': (
        '(⁄ ⁄•⁄ω⁄•⁄ ⁄)', '(〃ω〃)', '(*/ω＼*)', '(〃▽〃)', '(｡ﾉω＼｡)',
        '(⁄ ⁄>⁄ ▽ ⁄<⁄ ⁄)', '(„ಡωಡ„)', '(〃￣ω￣〃)', '(*/▽＼*)', '(⁄˘⁄ ⁄ω⁄ ⁄˘⁄)',
        '(灬ºωº灬)', '(´,,•﹏•,,`)', '(〃´∀｀)', '(⁄ ⁄´⁄-⁄`⁄ ⁄)', '(｡･･｡)', '(๑´ڡ`๑)',
    ),
    '安慰・抱抱': (
        '(づ｡◕‿‿◕｡)づ', '(っ´ω`)ﾉ', '(つ≧▽≦)つ', '(づ￣ ³￣)づ', '(´▽`ʃ♡ƪ)',
        '(っ˘̩╭╮˘̩)っ', '(づ◡﹏◡)づ', '(っ´▽`)っ', '(つ´∀｀)つ', '(づ๑•ᴗ•๑)づ',
        '(っ˘ω˘ς)', '(♡˙︶˙♡)', '(´｡• ᵕ •｡`)♡', '(ღ˘⌣˘ღ)', '(づ´･ω･`)づ', '(っ｡•́‿•̀｡)っ',
    ),
    '委屈・低落': (
        '(´･ω･`)', '(｡•́︿•̀｡)', '(╥﹏╥)', '(っ˘̩╭╮˘̩)っ｡', '(｡•́ωก̀｡)',
        '(ಥ﹏ಥ)', '(´；ω；`)', '(T_T)', '(つ﹏⊂)', '(｡╯︵╰｡)',
        '(；ω；)', '(╯︵╰,)', '(ಥ_ಥ)', '(ó﹏ò｡)', '(っ- ‸ - ς)', '(｡•́ - •̀｡)',
    ),
    '惊讶・没想到': (
        '(⊙ω⊙)', '(°ロ°)', 'Σ(ﾟДﾟ)', '(๑°ㅁ°๑)', '(⊙_⊙;)',
        '(゜ロ゜)', '(°◇°)', '(ﾟοﾟ人)', '(⊙▽⊙)', '(｡ŏ_ŏ)',
        '(꒪⌓꒪)', '(ʘᗩʘ)', '(O_O)', '(ﾉﾟ0ﾟ)ﾉ', '(；ﾟДﾟ)', '(°o°)',
    ),
    '疑惑・琢磨': (
        '(・・?)', '(・_・ヾ)', '(´･_･`)', '(｡•ˇ‸ˇ•｡)', '(・・ )?',
        '(￣～￣;)', '(・–・;)ゞ', '(・・;)ゞ', '(・・。)ゞ', '(¬_¬ )?',
        '(。-`ω´-)', '(˙▿˙)?', '(。ヘ°)', '(•ิ_•ิ)?', '(´-ω-`)', '(๑•̌.•̑๑)ˀ̣ˀ̣',
    ),
    '调皮・接梗': (
        '(¬‿¬)', '(￢‿￢ )', '(๑>؂<๑)', '(｡•̀ᴗ-)و', '( •̀ᴗ•́ )و ̑̑',
        '(ಡωಡ)', '(ಡ‿ಡ)', '(｀∀´)Ψ', '(˵¯͒〰¯͒˵)', '(≖ᴗ≖✿)',
        '(｡•̀ᴗ-)✌', '(๑¯∀¯๑)', '(｀▽´)', '(＾ڡ＾)', '(¬ᴗ¬)', '( •̀∀•́ )',
    ),
    '小脾气・嘴硬': (
        '(｡•ˇ‸ˇ•｡)哼', '(￣へ￣)', '(｀へ´)', '(๑•̀д•́๑)', '(｡•̀へ•́｡)',
        '(¬_¬)', '(눈_눈)', '(╬￣皿￣)', '(｀ε´)', '(＃￣ω￣)',
        '(・`ω´・)', '(￣ε￣)', '(๑•̀ㅂ•́)و哼', '(｀⌒´メ)', '(｡｀Д´｡)', '(๑•̀ω•́๑)',
    ),
    '困倦・休息': (
        '(－ω－) zzZ', '(∪｡∪)｡｡｡zzZ', '(´-﹏-`)', '(￣o￣) . z Z', '(－_－) zzZ',
        '(っ˘ω˘c)', '(¦3[▓▓]', '(。-ω-)zzz', '(´～`)', '(ᴗ˳ᴗ)',
        '(－.－)...zzz', '(˘ω˘)', '(∪ω∪)', '(｡-_-｡)', '(。-ω-)ﾉ', '(´ぅω・｀)',
    ),
    '招呼・挥手': (
        '( ´ ▽ ` )ﾉ', '(｡･∀･)ﾉﾞ', '(・ω・)ノ', 'ヾ(＾∇＾)', '(o´ω`o)ﾉ',
        'ヾ(・ω・*)', '(｡･д･)ﾉ', '(*´∀｀)ノ', '(oﾟ▽ﾟ)o', 'ヾ(´︶`♡)ﾉ',
        '(｡･ω･)ﾉﾞ', 'ヾ(￣▽￣)', '( ´ ▽ ` )ﾉｼ', '(๑╹◡╹)ﾉ', '(*・ω・)ﾉ', 'ヾ(｡･ω･｡)',
    ),
    '期待・打气': (
        '(ง •̀ω•́)ง', '٩(๑•̀ω•́๑)۶', '(๑و•̀ω•́)و', '(ﾉ･ω･)ﾉ', '(๑✧∀✧๑)',
        '(๑˃̶͈̀o˂̶͈́๑)', '(☆ω☆)', '(人´∀｀)', '(人*´∀｀)｡*ﾟ+', '(๑•̀ㅁ•́๑)✧',
        '(o˘◡˘o)✧', '(๑•̀o•́๑)', '٩(ˊᗜˋ*)و', '(ﾉ´▽｀)ﾉ♪', '(☆_☆)', '(｡•̀ᴗ•́)و',
    ),
}
FACES = tuple(face for faces in CATEGORIES.values() for face in faces)
# Longest first prevents a shorter face swallowing its gesture/suffix.
FACE_RE = re.compile('|'.join(map(re.escape, sorted(FACES, key=len, reverse=True))))


def canonical(face):
    # Spaces in a drawing are not a genuinely new expression.
    return re.sub(r'\s+', '', face)


_GROUP = {canonical(f): name for name, faces in CATEGORIES.items() for f in faces}


def varied(face, used, seed):
    """Only replace a known, repeated drawing with its least-used peer."""
    key=canonical(face)
    name=_GROUP.get(key)
    if not name or not used[key]:return face
    pool=CATEGORIES[name]
    least=min(used[canonical(f)] for f in pool)
    choices=[f for f in pool if used[canonical(f)]==least and canonical(f)!=key]
    if not choices:return face
    index=int.from_bytes(hashlib.sha256((seed+'\0'+face).encode('utf-8')).digest()[:4],'big')%len(choices)
    return choices[index]


def guidance(used, seed):
    """Small per-turn alternatives from trusted vocabulary only."""
    recent=[face for face in FACES if used[canonical(face)]]
    digest=int.from_bytes(hashlib.sha256(seed.encode('utf-8')).digest()[:4],'big')
    choices=[]
    for index,(label,pool) in enumerate(CATEGORIES.items()):
        unused=[f for f in pool if not used[canonical(f)]] or list(pool)
        offset=(digest+index)%len(unused)
        choices.append(label+'：'+ ' / '.join((unused[offset],unused[(offset+1)%len(unused)])))
    return ('\n颜文字按当下情绪选，不把同一个当作签名反复贴。保留表达欲，换表情不等于减少可爱；'
            '文字本身已有情绪时也可以不加，别为遍历清单硬塞。'
            + ('最近已用：'+' / '.join(recent[:24])+'；这次想用时优先选同情绪的其他画法。' if recent else '')
            + '\n可选参考（不是固定轮流发送）：'+'；'.join(choices))


def persona_reference():
    return ('## 颜文字多样性与情绪选用\n\n'
        '颜文字服务于这句话的情绪，不是每轮末尾的固定签名。保留软萌、调皮、傲娇和互动表达的强度，'
        '不按条数禁止使用；但刚发过的同一个画法应换成相近情绪的其他表达，空格变化不算换新。'
        '同一个会话结合近期自己真正发出的回复选择，不把群友用的表情当成自己的使用记录。\n'
        '先理解语境再挑：开心、被夸、害羞、委屈、安慰、困倦分别选合适的表达；'
        '不为了多样把难过换成大笑，不靠颜文字代替具体回应。可以使用清单之外的自然表达。'
        '有时用动作，有时用文字，有时用颜文字或真实表情包，随互动自然变化，不固定配套。'
        '代码、命令、路径、引用和要求逐字复述的原文保持原样。\n\n'
        + '\n\n'.join('### '+label+'\n'+' / '.join(faces) for label,faces in CATEGORIES.items())
        + '\n')
