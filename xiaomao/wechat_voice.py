"""Delivery rules for the editable v2.2 WeChat language layer."""
import re
import json

from .dafeiyu import formatted_reply


SECTION = '【语言习惯（全面拟人层）】'
FACES = ('(｡･ω･｡)', '(´･ω･`)', '(￣▽￣)', '(´▽`ʃ♡ƪ)', '(๑•̀ㅂ•́)و✧', '( ´ ▽ ` )ﾉ')
_FACE = re.compile('|'.join(map(re.escape,FACES))+r'|[（(](?=[^()（）\n]{0,20}[ωД▽∀♡•╥｀｡￣≧≦ಥ])[^()（）\n]{1,22}[)）]')
_EMOJI = re.compile(r'[\U0001f300-\U0001faff\u2600-\u26ff\u2700-\u27bf]')
_ACKS = {'行':('行吧','可以啊'),'好':('好啊','好嘛'),'嗯':('嗯呢','懂了'),'哦':('晓得','懂了'),'对':('对呀','没错')}
_OPENERS = sorted(('唔…','诶','那个','啊这','不是','你别说','等一下','我看看','让我想下','话说','对了','其实','讲真','说实话',
                   '所以','然后呢','要不','不然','这样','那','哎','嘿','嗯？','好嘛','好啊','行吧','成','收到',
                   '可以啊','嗯呢','对呀','懂了','没错','明白','晓得'),key=len,reverse=True)
_KEEP_MEANING = {'不是':'不','要不':'不如','不然':'要不然','所以':'这么一来','等一下':'等下',
                 '我看看':'我先看看','让我想下':'让我想想','然后呢':'接下来'}
_TAIL = re.compile(r'([啊吧呢嘛啦哈呗咯哦欸])[？！!?～~…\s]*$')
_SERIOUS = re.compile(r'报错|异常栈|安全|风险|生产库|转账|验证码|支付密码|医嘱|用药|药物|剂量|急救|就医|法律|合同纠纷|投资|借款|删库')
_EXACT = re.compile(r'照抄|逐字|原封不动|(?:按原样|原样)(?:输出|回复|发送|返回|转述)|(?:只|仅)输出(?:代码|命令|路径|原文|配置)')
_TECH_SOURCE = re.compile(r'命令|参数|路径|文件名|配置|报错原文')
_LINE_START = (r'(?:>|[a-z]:[\\/]|\\\\|/|~[\\/]|\.{1,2}[\\/]|https?://|www\.|'
               r'(?:git|pip|pythonw?|npm|node|curl|cargo|docker|pytest|powershell|pwsh|cmd|'
               r'cd|copy|xcopy|robocopy|move|ren|dir|del|type|(?:Get|Set|Copy|Move|New|Remove)-[\w-]+)\s+\S|'
               r'(?:原话|原文|报错|错误|参数|配置|命令|路径)\s*[:：]|[A-Za-z_]\w*[\w.-]*[ \t]*=[^=]|'
               r'Traceback|[\w.]+(?:Error|Exception)\s*[:：])')
_LITERAL_LINE = re.compile(r'^[ \t]*'+_LINE_START,re.I)
_SPAN_PATTERN = (r'`{3}[\s\S]*?(?:`{3}|$)|~{3}[\s\S]*?(?:~{3}|$)|`[^`\n]+`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|'
                 r'''"[^"\n]*"|(?<![a-z0-9])'[^'\n]*'(?![a-z0-9])|https?://[^\s`<>“”]+|'''
                 r'(?:[a-z]:[\\/]|\\\\|\.{1,2}[\\/]|~[\\/]|(?<![a-z0-9/])/)[^\s`<>“”]+|'
                 r'[\w.-]+[\\/][^\s`<>“”]*?[.。][A-Za-z0-9]{1,16}(?=$|[\s,，。；;）)])|'
                 r'(?m:^[ \t]*'+_LINE_START+r'[^\n]*)')
_SPANS = re.compile(_SPAN_PATTERN,re.I)
_TOKENS = re.compile('('+_FACE.pattern+'|'+_SPAN_PATTERN+')',re.I)


def keep_verbatim(text,sources):
    if any(_EXACT.search(m.text) for m in sources if m.direction=='in'):return True
    if re.match(r'^(?: {4}|\t)\S|^\s*\|.*\|',text):return True
    stripped=text.strip()
    if stripped.startswith(('```','~~~')):
        fenced=_SPANS.match(stripped)
        if fenced and fenced.end()==len(stripped):return True
    if (any(_TECH_SOURCE.search(m.text) for m in sources if m.direction=='in') and
            re.match(r'^\s*(?:[A-Za-z][\w.-]*\s+\S|[^\s]+[\\/][^\s]+\s*$)',text)):return True
    if text.lstrip().startswith(('[','{','"')):
        try:json.loads(text)
        except (ValueError,RecursionError):pass
        else:return True
    return bool(_LITERAL_LINE.match(text) and (len(text.strip().splitlines())==1 or
                all(_LITERAL_LINE.match(line) for line in text.splitlines() if line.strip())))


def preserve_layout(text,sources):
    return keep_verbatim(text,sources) or bool(_SPANS.search(_FACE.sub('',text)))


def _recent(history):
    return [m.text.strip() for m in history if m.direction=='out' and m.kind=='text' and m.text.strip()][-10:]


def _opening(text):
    if re.match(r'^唔(?:…+|\.{2,})',text):return '唔…'
    return next((s for s in _OPENERS if text.startswith(s)),text.split('，',1)[0][:6])


def history_hint(history):
    recent=_recent(history)
    if not recent:return ''
    first=_opening(recent[-1])
    # Only copy tokens from our language list into instructions, never arbitrary history.
    hint='上个气泡以“'+first+'”开头，这次换个入口或直接说内容。' if first in _OPENERS or first in _ACKS else ''
    if len(recent)>=2:
        tails=[_TAIL.search(s) for s in recent[-2:]]
        if all(tails) and tails[0][1]==tails[1][1]:hint+='前两条都以“'+tails[0][1]+'”收尾，这条换收法。'
    if any(_FACE.search(s) for s in recent[-5:]):hint+='最近已经用过颜文字，这条休息一下。'
    return ('\n本会话刚发出的文字节奏：'+hint) if hint else ''


def render_bubble(text,history,sources):
    if keep_verbatim(text,sources):return text
    recent=_recent(history)
    previous=_opening(recent[-1]) if recent else ''
    serious=any(_SERIOUS.search(m.text) for m in sources) or _SERIOUS.search(text)
    allow_face=not (serious or any(_FACE.search(s) for s in recent[-5:]) or _EMOJI.search(_FACE.sub('',text)))
    # Protect quoted words, code and paths at their exact offsets. A face containing
    # a backtick is a single token when it occurs outside a literal span.
    tokens=_TOKENS.split(text)
    plain=not formatted_reply(_FACE.sub('',text))
    result=[]
    for i,token in enumerate(tokens):
        if i%2:
            if _FACE.fullmatch(token):
                if allow_face:result.append(token);allow_face=False
            else:result.append(token)
        else:
            end=re.search(r'。+(?=\s*$)',token) if i==len(tokens)-1 else None
            if end:token=token[:end.start()]+token[end.end():]
            if plain:
                token=re.sub(r'。+(?=[ \t]*\n)','',token)
                token=re.sub(r'。+(?!\s*$)', '\n',token) if i==len(tokens)-1 else token.replace('。','\n')
            result.append(token)
    body=''.join(result)
    middle=body.strip()
    match=re.match(r'^(行|好|嗯|哦|对)(?:[，,、！!…~～\n]\s*|$)',middle)
    if match:
        a,b=_ACKS[match[1]]
        rest=middle[match.end():].lstrip()
        if rest and previous in (match[1],a,b):middle=rest
        else:middle=(b if previous==a else a)+('，'+rest if rest else '')
    elif previous in _OPENERS and _opening(middle)==previous:
        remainder=re.sub(r'^唔(?:…+|\.{2,})','',middle) if previous=='唔…' else middle[len(previous):]
        cut=re.match(r'^[，,、…~～\s]+(.+)$',remainder,re.S)
        if cut:middle=(_KEEP_MEANING[previous]+'，' if previous in _KEEP_MEANING else '')+cut[1]
        elif previous=='唔…' and remainder:middle=remainder
        elif not remainder and previous in ('行吧','好啊','好嘛','嗯呢','收到','成','可以啊'):
            middle='好嘛' if previous=='可以啊' else '可以啊'
    if len(recent)>=2:
        tails=[_TAIL.search(s) for s in [*recent[-2:],middle]]
        if all(tails) and tails[0][1]==tails[1][1]==tails[2][1] and not re.search(r'(?:网吧|酒吧|贴吧|氧吧|毛呢|花呢|哈[哈]+)[？！!?～~…\s]*$',middle):
            middle=middle[:tails[2].start()]+middle[tails[2].start()+1:]
    # WeChat removes a final newline from casual text. Keep separators for
    # planning, then send exactly the content that the receipt can attest.
    return middle
