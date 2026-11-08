"""可编辑的角色表达。Markdown 原稿是编辑器和模型共同使用的唯一来源。"""
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
import re

from .privacy import atomic_private_write


DEFAULT = (Path(__file__).parent / "default_persona.md").read_bytes().decode("utf-8")

PERSONA_CATEGORIES = {
    'identity':'角色身份', 'style':'表达风格', 'relationship':'关系与分寸',
    'capability':'能力与边界', 'memory':'记忆与话题', 'custom':'自定义',
}
_CATEGORY_HINTS = {
    'relationship': ('关系','分寸','群聊与隐私','朋友','主人','亲疏'),
    'memory': ('记忆','话题衔接'),
    'capability': ('能力','权限','边界','输入与静默','工具','媒体','安全'),
    'style': ('口吻','风格','语言','节奏','表情','示例','语气','说话','表达'),
    'identity': ('身份','形象','性格','名字','人设','角色设定'),
}
_HEADING = re.compile(r'^\s{0,3}(?:#{1,6}[ \t]+\S.*|【[^】\r\n]+】)[ \t]*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


@dataclass(frozen=True)
class PersonaSection:
    category: str
    heading: str
    body: str
    newline: str = '\n'


def _category(heading, first_title):
    if first_title:
        return 'identity'
    for category, hints in _CATEGORY_HINTS.items():
        if any(hint in heading for hint in hints):
            return category
    return 'custom'


def split_persona(text: str) -> list[PersonaSection]:
    """Keep every line, heading spelling and line ending, including repeated headings."""
    sections = []
    heading = newline = fence = ''
    category = 'custom'
    body = []
    seen_title = False
    for line in text.splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        marker = _FENCE.match(raw)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not raw[marker.end():].strip():
                fence = ''
        if not fence and _HEADING.fullmatch(raw):
            if heading or body:
                sections.append(PersonaSection(category, heading, ''.join(body), newline))
            heading, newline = raw, line[len(raw):]
            is_title = bool(re.match(r'^ {0,3}#[ \t]+', raw))
            category = _category(heading, is_title and not seen_title)
            seen_title |= is_title
            body = []
        else:
            body.append(line)
    if heading or body or not sections:
        sections.append(PersonaSection(category, heading, ''.join(body), newline))
    return sections


def join_persona(sections) -> str:
    parts = []
    for section in sections:
        if section.heading and parts and not parts[-1].endswith(('\r','\n')):
            parts.append(section.newline or '\n')
        separator = section.newline or ('\n' if section.body else '')
        raw = (section.heading + separator if section.heading else '') + section.body
        if raw:
            parts.append(raw)
    return ''.join(parts)


@lru_cache(maxsize=16)
def render_persona(text):
    """Give the model section roles while retaining the author's order and wording."""
    parts = []
    for section in split_persona(text):
        parts.append('〔'+PERSONA_CATEGORIES[section.category]+'〕\n'+join_persona([section]).rstrip())
    return '\n\n'.join(parts)


@lru_cache(maxsize=32)
def uses_default_voice(text, heading):
    """Hand-authored voice instructions must survive the delivery postprocessor."""
    def style_sections(raw):
        return [(s.heading, s.body.strip().splitlines()) for s in split_persona(raw)
                if s.category in ('style','custom')]
    return heading in text and style_sections(text) == style_sections(DEFAULT)


def load_persona(root: Path) -> str:
    from .persona_modules import load_modules, export_modules
    modules=load_modules(root)
    if modules is not None:
        return export_modules(modules)
    from .persona_documents import file_revision
    path = Path(root)/'config'/'persona.md'
    revision = file_revision(path)
    if revision is None:
        return DEFAULT
    return _read_persona(path, revision)


@lru_cache(maxsize=32)
def _read_persona(path, revision):
    if revision[0] > 1024*1024:
        raise ValueError('人格设定超过 1 MB，未读取或截断原稿')
    text = path.read_bytes().decode('utf-8')
    validate_persona(text)
    return text


def validate_persona(text: str):
    if not isinstance(text,str) or not text.strip() or len(text) > 200000:
        raise ValueError('人格设定需为1至200000字的文字')
    if re.search(r'sk-[A-Za-z0-9_-]{9,}', text):
        raise ValueError('人格设定不能包含API密钥，请使用加密密钥输入框')


def save_persona(root: Path, text: str):
    from .persona_modules import modules_revision
    if modules_revision(root) is not None:
        raise ValueError('当前使用模块化人格，请在人格工作室编辑模块；旧编辑器不能覆盖整套规则')
    validate_persona(text)
    from .persona_documents import archive_persona_source
    directory = Path(root)/'config'
    directory.mkdir(parents=True, exist_ok=True)
    previous = directory/'persona.md'
    if previous.exists():
        data = previous.read_bytes()
        # An unreadable/invalid active file remains recoverable through the
        # existing explicit restore workflow; never archive credential paste.
        try: archive_persona_source(root, data)
        except (ValueError, UnicodeDecodeError): pass
    archive_persona_source(root, text.encode('utf-8'))
    atomic_private_write(directory/'persona.md', text.encode('utf-8'))
