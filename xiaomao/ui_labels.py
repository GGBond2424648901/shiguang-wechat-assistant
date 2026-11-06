"""Short labels for opaque member references within a displayed conversation."""
import re
from .member_names import known_member, safe_name


def member_label(speaker,labels,unknown):
    if not speaker:return unknown
    if speaker=='self':return '小玲'
    if known_member(speaker):
        name=safe_name(labels.get(speaker))
        if name:
            same=[key for key,value in labels.items() if known_member(key) and safe_name(value)==name]
            return name if len(same)==1 else f'{name}（成员 {same.index(speaker)+1}）'
        value=labels.setdefault(speaker,len(labels)+1)
        return f'成员 {value}' if type(value) is int else unknown
    return unknown


def memory_text(text,labels):
    return re.sub(r'(?<![A-Za-z0-9_])[pm]_[a-f0-9]{16}(?![A-Za-z0-9_])',
                  lambda m:member_label(m[0],labels,'') if m[0].startswith('p_') else '聊天记录',text)
