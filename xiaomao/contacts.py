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


def visual_title_matches(observed,expected):
    """Allow missing pictographs, but never a visible contradictory pictograph."""
    def formatted(value):
        return ''.join(c for c in name_key(value) if unicodedata.category(c)!='Cf'
                       and not '\ufe00'<=c<='\ufe0f' and not '\U000e0100'<=c<='\U000e01ef')
    observed,expected=formatted(observed),formatted(expected)
    if not observed or visual_name_key(observed)!=visual_name_key(expected):return False
    index=0
    for char in expected:
        if index<len(observed) and observed[index]==char:index+=1
        elif unicodedata.category(char) not in ('So','Sk'):return False
    return index==len(observed)


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
    fields=['username','nick_name','remark','alias','delete_flag','verify_flag','local_type']
    query=','.join('"'+field+'"' if field in columns else "''" for field in fields)
    rows=[];self_names=[];hidden_names=[]
    for uid,nickname,remark,alias,deleted,verified,local_type in conn.execute('SELECT '+query+' FROM contact'):
        if not isinstance(uid,str) or not uid or deleted:continue
        nickname=nickname or '';name=remark or nickname or uid
        if uid==self_id:
            self_names.append(visual_name_key(name));continue
        kind='group' if uid.endswith('@chatroom') else 'private'
        # Service/system accounts are shown, but cannot be auto-chat targets.
        if not uid.endswith('@chatroom') and (verified or uid.startswith('gh_') or '@' in uid
                or uid in ('filehelper','fmessage','newsapp','weixin','medianote','floatbottle','qmessage','tmessage','qqmail','feedsapp')):
            kind='service'
        # The same table also contains group-member/stranger metadata. Those
        # records remain available to the member-name reader, not as friends
        # in the target picker. Retain their titles as collision vetoes: an
        # already-open stranger chat must not impersonate a same-named friend.
        if kind=='private' and 'local_type' in columns and local_type!=1:
            hidden_names.append(visual_name_key(name));continue
        reason=SERVICE_DISABLED if kind=='service' else ''
        if len(name)>80 or any(ord(c)<32 for c in name):reason='会话标题过长或包含不可识别字符'
        rows.append(Contact(uid,account,name,kind,nickname,alias or '',not reason,reason))
    counts=Counter(visual_name_key(row.name) for row in rows)
    counts.update(self_names)
    counts.update(hidden_names)
    collisions=set()
    groups={visual_name_key(row.name):row.id for row in rows if row.kind=='group'}
    for name in (*self_names,*hidden_names):
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


def resolve_title_contact(rows,label,window_kind='main'):
    """Resolve a complete header against one full account directory, without fuzzy aliases.

    Nicknames and WeChat IDs remain useful search terms. A different current
    remark is not evidence that the window belongs to that contact.
    """
    from types import SimpleNamespace
    from .vision import header_matches
    rows=tuple(rows)
    accounts={row.account for row in rows}
    if len(accounts)!=1 or not next(iter(accounts),''):
        raise ValueError('当前窗口未能对应一个完整的登录账号目录，请刷新联系人')
    matches=[row for row in rows if header_matches(label,SimpleNamespace(
        target_group=row.name,header_label=row.name,target_kind=row.kind,
        ocr_title=visual_name_key(row.name),window_kind=window_kind))]
    if len(matches)>1:
        raise ValueError(f'微信目录中有 {len(matches)} 个同名对象，无法仅凭标题确认身份；请先在微信设置唯一备注，再刷新联系人')
    if not matches:
        if any(visual_title_matches(label,value) for row in rows for value in (row.nickname,row.alias) if value):
            raise ValueError('会话标题与微信目录中的备注名不一致，请核对完整备注并刷新联系人')
        raise ValueError('当前会话标题未能与联系人列表唯一对应，请刷新联系人；若目录已有该对象，请框选完整标题')
    contact=matches[0]
    if contact.kind not in ('private','group'):raise ValueError(contact.reason or SERVICE_DISABLED)
    if not contact.selectable:raise ValueError(contact.reason)
    # Defend callers using manually constructed directory rows as well as
    # read_directory(), which already marks visual/counted-title collisions.
    if sum(visual_name_key(row.name)==visual_name_key(contact.name) for row in rows)!=1:
        raise ValueError('存在同名会话，请先在微信设置唯一备注，再刷新联系人')
    return contact


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
