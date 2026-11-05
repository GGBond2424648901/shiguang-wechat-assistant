"""Local contact metadata, searchable without opening chats or sending messages."""
from collections import Counter
from dataclasses import dataclass,replace
import unicodedata
import re

SERVICE_DISABLED = '服务或系统会话不启用自动回复'

def name_key(value):
    return ''.join(unicodedata.normalize('NFKC',value).split())


def visual_name_key(value):
    """OCR may omit pictographs. Keep all letters, digits and punctuation.

    This key is usable only after the complete account directory is checked
    for collisions; it never permits prefix/truncated-name matching.
    """
    return ''.join(c for c in name_key(value) if unicodedata.category(c) not in ('So','Sk','Cf')
                   and not '\ufe00'<=c<='\ufe0f' and not '\U000e0100'<=c<='\U000e01ef')


@dataclass(frozen=True)
class Contact:
    id: str
    account: str
    name: str
    kind: str
    nickname: str = ''
    alias: str = ''
    selectable: bool = True
    reason: str = ''

    @property
    def binding(self):
        return {'id':self.id,'account':self.account,'kind':self.kind}


def read_directory(conn,self_id,account):
    columns={row[1] for row in conn.execute('PRAGMA table_info(contact)')}
    required={'username','nick_name','remark'}
    if not required<=columns:raise ValueError('微信联系人表结构不兼容')
    fields=['username','nick_name','remark','alias','delete_flag','verify_flag']
    query=','.join('"'+field+'"' if field in columns else "''" for field in fields)
    rows=[];self_names=[]
    for uid,nickname,remark,alias,deleted,verified in conn.execute('SELECT '+query+' FROM contact'):
        if not isinstance(uid,str) or not uid or deleted:continue
        nickname=nickname or '';name=remark or nickname or uid
        if uid==self_id:
            self_names.append(visual_name_key(name));continue
        kind='group' if uid.endswith('@chatroom') else 'private'
        # Service/system accounts are shown, but cannot be auto-chat targets.
        if not uid.endswith('@chatroom') and (verified or uid.startswith('gh_') or '@' in uid
                or uid in ('filehelper','fmessage','newsapp','weixin','medianote','floatbottle','qmessage','tmessage','qqmail','feedsapp')):
            kind='service'
        reason=SERVICE_DISABLED if kind=='service' else ''
        if len(name)>80 or any(ord(c)<32 for c in name):reason='会话标题过长或包含不可识别字符'
        rows.append(Contact(uid,account,name,kind,nickname,alias or '',not reason,reason))
    counts=Counter(visual_name_key(row.name) for row in rows)
    counts.update(self_names)
    collisions=set()
    groups={visual_name_key(row.name):row.id for row in rows if row.kind=='group'}
    for name in self_names:
        match=re.fullmatch(r'(.+)\(\d+\)',name)
        if match and match[1] in groups:collisions.add(groups[match[1]])
    for row in rows:
        if row.kind=='group':continue
        match=re.fullmatch(r'(.+)\(\d+\)',visual_name_key(row.name))
        if match and match[1] in groups:collisions.update((row.id,groups[match[1]]))
    return sorted((replace(row,selectable=False,reason='存在同名会话，请先设置唯一备注名')
        if counts[visual_name_key(row.name)]>1 or not visual_name_key(row.name) else replace(row,selectable=False,reason='个人与群聊完整标题可能相同，请设置唯一备注名')
        if row.id in collisions else row for row in rows),key=lambda r:(r.kind,name_key(r.name)))


def search_directory(rows,query='',kind='all'):
    key=name_key(query).casefold()
    return [row for row in rows if (kind=='all' or row.kind==kind)
            and (not key or any(key in name_key(v).casefold() for v in (row.name,row.nickname,row.alias,row.id)))]


def resolve_contact(rows,settings,name):
    binding=settings.conversation_targets.get(name)
    if binding:
        matched=[r for r in rows if r.id==binding['id'] and r.account==binding['account'] and r.kind==binding['kind']]
    else:
        # Compatibility for old explicitly configured groups only. A private
        # contact always gets an account-bound ID through the directory picker.
        matched=[r for r in rows if r.kind=='group' and r.name==name]
    if len(matched)!=1:raise ValueError('所选会话在当前登录账号中尚未唯一识别')
    row=matched[0]
    if not row.selectable:raise ValueError(row.reason)
    if row.name!=name:raise ValueError('所选会话名称已改变，请从联系人列表重新选择')
    if sum(visual_name_key(r.name)==visual_name_key(name) for r in rows)!=1:
        raise ValueError('会话重名，已阻止向不确定对象发送')
    return row
