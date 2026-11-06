"""Trusted conversation labels; account IDs remain inside the local DB reader.

Names are mutable presentation data. Only the scoped speaker ID owns history,
preferences and relationships. No chat body, alias, OCR name or XML displayname
is used as member metadata.
"""
from dataclasses import dataclass
import math
import re
import unicodedata


NAME_SOURCES = frozenset(('wechat_remark', 'wechat_group', 'wechat_nickname'))
MAX_MEMBERS = 4096
_MEMBER = re.compile(r'p_[a-f0-9]{16}')
_RAW_ID = re.compile(r'wxid_[a-z0-9_-]+|gh_[a-z0-9_-]+|@chatroom', re.I)


def known_member(value):
    return isinstance(value, str) and _MEMBER.fullmatch(value) is not None


def safe_name(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if (not value or len(value) > 80 or _RAW_ID.search(value)
            or any(unicodedata.category(c) in ('Cc', 'Cs', 'Zl', 'Zp')
                   or '\u202a' <= c <= '\u202e' or '\u2066' <= c <= '\u2069' for c in value)):
        return ''
    return value


def trusted_display_name(message):
    source = getattr(message, 'sender_source', '')
    at = getattr(message, 'sender_observed_at', 0)
    if (message.direction != 'in' or not known_member(message.speaker_id)
            or not isinstance(source, str) or source not in NAME_SOURCES
            or type(at) not in (int, float) or not math.isfinite(at) or at < 0):
        return ''
    return safe_name(message.sender)


def profiles_from_messages(messages):
    profiles = {}
    for message in messages:
        name = trusted_display_name(message)
        if name:
            at = getattr(message, 'sender_observed_at', 0)
            if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                continue
            old = profiles.get(message.speaker_id)
            if old is None or at >= old['observed_at']:
                profiles[message.speaker_id] = {'name':name, 'source':message.sender_source, 'observed_at':at}
    return profiles


def merge_profiles(existing, incoming):
    result = dict(existing)
    for speaker, profile in incoming.items():
        if speaker not in result or profile['observed_at'] >= result[speaker]['observed_at']:
            result[speaker] = profile
    return result


def profile_labels(profiles):
    return {speaker:(p['name'], p['source']) for speaker, p in profiles.items()}


def validate_profiles(data):
    """Validate optional encrypted metadata; never accept raw account IDs."""
    if not isinstance(data, dict) or len(data) > MAX_MEMBERS:
        raise ValueError()
    for speaker, profile in data.items():
        if (not known_member(speaker) or not isinstance(profile, dict)
                or set(profile) not in ({'name', 'source'}, {'name', 'source', 'observed_at'}) or profile['source'] not in NAME_SOURCES
                or not safe_name(profile['name']) or safe_name(profile['name']) != profile['name']):
            raise ValueError()
        at = profile.get('observed_at', 0)
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
            raise ValueError()
    return {speaker:{**profile, 'observed_at':profile.get('observed_at', 0)} for speaker, profile in data.items()}


@dataclass(frozen=True)
class MemberName:
    name: str
    source: str


def _varint(data, at):
    value = 0
    for shift in range(0, 70, 7):
        if at >= len(data):
            raise ValueError()
        byte = data[at]
        at += 1
        value |= (byte & 127) << shift
        if byte < 128:
            if shift == 63 and byte > 1:
                raise ValueError()
            return value, at
    raise ValueError()


def _protobuf_fields(data):
    """Bounded wire decoder; only two explicit message levels are interpreted."""
    at = 0
    fields = 0
    while at < len(data):
        fields += 1
        if fields > 20000:
            raise ValueError()
        tag, at = _varint(data, at)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise ValueError()
        if wire == 0:
            value, at = _varint(data, at)
        elif wire in (1, 5):
            end = at + (8 if wire == 1 else 4)
            if end > len(data):
                raise ValueError()
            value, at = data[at:end], end
        elif wire == 2:
            size, at = _varint(data, at)
            end = at + size
            if end > len(data):
                raise ValueError()
            value, at = data[at:end], end
        else:
            raise ValueError()
        yield number, wire, value


def _group_names(blob):
    """chat_room.ext_buffer field 1 → member {1: username, 2: displayName}.

    Unknown fields are skipped, never searched for strings. Unsupported or
    truncated data yields no group override; contact remarks/nicknames remain.
    """
    if not isinstance(blob, bytes) or len(blob) > 4 * 1024 * 1024:
        return {}
    names, ambiguous = {}, set()
    try:
        for number, wire, member in _protobuf_fields(blob):
            if number != 1 or wire != 2:
                continue
            values = {}
            for field, kind, value in _protobuf_fields(member):
                if field not in (1, 2):
                    continue
                if kind != 2 or field in values:
                    raise ValueError()
                values[field] = value.decode('utf-8')
            uid = values.get(1, '')
            name = safe_name(values.get(2, ''))
            if not uid or len(uid) > 512 or not name or name == uid:
                continue
            if uid in names and names[uid] != name:
                ambiguous.add(uid)
            names[uid] = name
    except (ValueError, UnicodeError):
        return {}
    return {uid:name for uid, name in names.items() if uid not in ambiguous}


def read_member_names(conn, conversation_id):
    """Read exact keyed metadata from the selected account's contact snapshot."""
    contacts, ambiguous = {}, set()
    # A stranger record is useful only if the primary contact record is absent.
    for table in ('contact', 'stranger'):
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not {'username', 'nick_name', 'remark'} <= columns:
            continue
        deleted = 'delete_flag' if 'delete_flag' in columns else '0'
        current, conflicts = {}, set()
        for uid, nickname, remark, removed in conn.execute(
                f'SELECT username,nick_name,remark,{deleted} FROM {table}'):
            if not isinstance(uid, str) or not uid or removed:
                continue
            entry = (safe_name(remark), safe_name(nickname))
            entry = tuple('' if name == uid else name for name in entry)
            if uid in current and current[uid] != entry:
                conflicts.add(uid)
            current[uid] = entry
        for uid, entry in current.items():
            if uid not in contacts:
                contacts[uid] = entry
                if uid in conflicts:
                    ambiguous.add(uid)
    group_names = {}
    if conversation_id.endswith('@chatroom'):
        columns = {r[1] for r in conn.execute('PRAGMA table_info(chat_room)')}
        if {'username', 'ext_buffer'} <= columns:
            rows = conn.execute('SELECT ext_buffer FROM chat_room WHERE username=?', (conversation_id,)).fetchall()
            if len(rows) == 1:
                group_names = _group_names(rows[0][0])
    result = {}
    for uid in contacts.keys() | group_names.keys():
        if uid in ambiguous:
            continue
        remark, nickname = contacts.get(uid, ('', ''))
        for name, source in ((remark, 'wechat_remark'), (group_names.get(uid), 'wechat_group'),
                             (nickname, 'wechat_nickname')):
            if name:
                result[uid] = MemberName(name, source)
                break
    return result
