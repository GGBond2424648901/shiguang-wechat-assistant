"""Short labels for opaque member references within a displayed conversation."""
import re


def member_label(speaker,labels,unknown):
    if not speaker:return unknown
    if speaker=='self':return '小玲'
    if re.fullmatch(r'p_[0-9a-f]{16}',speaker):
        return f'成员 {labels.setdefault(speaker,len(labels)+1)}'
    return speaker


def memory_text(text,labels):
    return re.sub(r'(?<![A-Za-z0-9_])[pm]_[a-f0-9]{16}(?![A-Za-z0-9_])',
                  lambda m:member_label(m[0],labels,'') if m[0].startswith('p_') else '聊天记录',text)
