"""Ordered persona sources, private immutable originals, and revision-aware reads.

The editable main persona remains config/persona.md. The library can hold
multiple supplements, with at most one enabled at a time.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

from .privacy import atomic_private_write, private_dir


CATEGORIES = ('identity', 'style', 'relationship', 'capability', 'memory', 'custom')
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_CORPUS_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 32
_LOCK = threading.RLock()


@dataclass(frozen=True)
class PersonaDocument:
    id: str
    title: str
    category: str
    text: str
    sha256: str
    source_name: str
    source_path: Path
    enabled: bool = True


def persona_source_directory(root):
    return Path(root)/'config'/'persona_sources'


def _manifest_path(root):
    return Path(root)/'config'/'persona_documents.json'


def file_revision(path):
    try:
        info = Path(path).stat()
    except FileNotFoundError:
        return None
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _validate_text(text):
    # Shared validation also protects backups from accidental credential paste.
    from .persona import validate_persona
    validate_persona(text)


def _document_title(title):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100 or len(title.splitlines()) != 1:
        raise ValueError('请填写 100 字以内的一行补充稿名称')
    title = title.strip()
    _validate_text(title)
    return title


def _decode(data):
    if not isinstance(data, bytes) or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError('人格文档需为 1 MB 以内的 UTF-8 Markdown 文字')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('人格文档请另存为 UTF-8 编码后导入') from None
    _validate_text(text)
    return text


def archive_persona_source(root, data):
    """Preserve exact bytes under their digest before changing active pointers."""
    _decode(data)
    digest = hashlib.sha256(data).hexdigest()
    directory = private_dir(persona_source_directory(root))
    path = directory/(digest+'.md')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('人格原文备份校验失败，请检查配置目录')
    else:
        atomic_private_write(path, data)
    return digest, path


@lru_cache(maxsize=64)
def _manifest(path, revision):
    if revision is None:
        return ()
    if revision[0] > 256 * 1024:
        raise ValueError('人格文档目录过大，请检查配置文件')
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError()
        rows = value.get('documents')
        if not isinstance(rows, list) or len(rows) > MAX_DOCUMENTS:
            raise ValueError()
        result, ids = [], set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'id','title','category','sha256','source_name','enabled'}
                    or not isinstance(row['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', row['id'])
                    or row['id'] in ids or not isinstance(row['title'], str) or not 1 <= len(row['title']) <= 100
                    or row['category'] not in CATEGORIES or type(row['enabled']) is not bool
                    or not isinstance(row['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                    or not isinstance(row['source_name'], str) or len(row['source_name']) > 255):
                raise ValueError()
            ids.add(row['id'])
            result.append(tuple(row[key] for key in ('id','title','category','sha256','source_name','enabled')))
        # Legacy manifests may have multiple enabled rows. Prefer the last one
        # in import order and leave the saved originals intact.
        active = next((row[0] for row in reversed(result) if row[-1]), None)
        return tuple((*row[:-1], row[0] == active) for row in result)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise ValueError('人格文档目录格式无效，未忽略任何原稿') from None


def _rows(root):
    path = _manifest_path(root)
    return _manifest(path, file_revision(path))


@lru_cache(maxsize=96)
def _source(path, revision, digest):
    if revision is None:
        raise ValueError('人格文档原文缺失，请检查配置目录或重新导入')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('人格文档原文校验失败，未发送不完整设定')
    return _decode(data)


def list_persona_documents(root):
    result, total = [], 0
    for identity, title, category, digest, source_name, enabled in _rows(root):
        path = persona_source_directory(root)/(digest+'.md')
        revision = file_revision(path)
        text = _source(path, revision, digest)
        total += revision[0]
        if total > MAX_CORPUS_BYTES:
            raise ValueError('人格文档总量超过 2 MB，请分批管理，未截断任何文档')
        result.append(PersonaDocument(identity, title, category, text, digest, source_name, path, enabled))
    return tuple(result)


def get_persona_document(root, identity):
    return next((item for item in list_persona_documents(root) if item.id == identity), None)


def persona_documents_revision(root):
    """Only stat small pointers and source files during normal generation."""
    rows = _rows(root)
    sources = tuple((row[3], file_revision(persona_source_directory(root)/(row[3]+'.md'))) for row in rows)
    return file_revision(_manifest_path(root)), sources


def _write_documents(root, documents, *, activate=None):
    rows = [dict(id=item.id, title=item.title, category=item.category, sha256=item.sha256,
                 source_name=item.source_name, enabled=item.enabled if activate is None else item.id == activate)
            for item in documents]
    path = _manifest_path(root)
    private_dir(path.parent)
    atomic_private_write(path, json.dumps({'version':1, 'documents':rows}, ensure_ascii=False, indent=2).encode('utf-8'))


def _import(root, data, title, category, source_name, enabled):
    text = _decode(data)
    title = _document_title(title)
    if category not in CATEGORIES or type(enabled) is not bool:
        raise ValueError('人格文档分类或启用状态无效')
    _validate_text(source_name)
    with _LOCK:
        documents = list_persona_documents(root)
        digest = hashlib.sha256(data).hexdigest()
        duplicate = next((item for item in documents if item.sha256 == digest), None)
        if duplicate:
            return duplicate
        if len(documents) >= MAX_DOCUMENTS or sum(item.source_path.stat().st_size for item in documents)+len(data) > MAX_CORPUS_BYTES:
            raise ValueError('人格文档最多 32 份、总计 2 MB，未截断或覆盖原稿')
        digest, path = archive_persona_source(root, data)
        document = PersonaDocument(uuid.uuid4().hex, title, category, text, digest, source_name, path, enabled)
        _write_documents(root, (*documents, document), activate=document.id if enabled else None)
        return document


def import_persona_file(root, path, *, category='custom', title='', enabled=True):
    path = Path(path)
    if path.suffix.lower() not in ('.md','.markdown','.txt'):
        raise ValueError('请选择 Markdown 或纯文本文件')
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError('人格文档超过 1 MB，未读取或截断原稿')
    return _import(root, path.read_bytes(), title or path.stem, category, path.name, enabled)


def import_persona_text(root, text, *, title, category='custom', enabled=True):
    if not isinstance(text, str):
        raise ValueError('请输入完整人格文字')
    return _import(root, text.encode('utf-8'), title, category, '直接粘贴', enabled)


def update_persona_document(root, identity, *, text=None, enabled=None, category=None, title=None, expected_sha256=None):
    if title is not None:
        title = _document_title(title)
    with _LOCK:
        documents = list_persona_documents(root)
        current = next((item for item in documents if item.id == identity), None)
        if current is None:
            raise ValueError('人格文档已不存在，请刷新文档列表')
        if expected_sha256 is not None and current.sha256 != expected_sha256:
            raise ValueError('人格文档已被其他编辑更新，请核对草稿')
        if category is not None and category not in CATEGORIES or enabled is not None and type(enabled) is not bool:
            raise ValueError('人格文档分类或启用状态无效')
        updated = current
        if text is not None:
            data = text.encode('utf-8')
            _decode(data)
            if sum(item.source_path.stat().st_size for item in documents if item.id != identity)+len(data) > MAX_CORPUS_BYTES:
                raise ValueError('人格文档总量超过 2 MB，未截断任何文档')
            digest, path = archive_persona_source(root, data)
            updated = replace(updated, text=text, sha256=digest, source_path=path)
        updated = replace(updated, enabled=enabled if enabled is not None else current.enabled,
                          category=category if category is not None else current.category,
                          title=title if title is not None else current.title)
        _write_documents(root, tuple(updated if item.id == identity else item for item in documents),
                         activate=identity if enabled else None)
        return updated


@lru_cache(maxsize=16)
def _render(documents):
    from .persona import PERSONA_CATEGORIES
    return '\n\n'.join('〔补充人格文档：'+item.title+'｜'+PERSONA_CATEGORIES[item.category]+'〕\n'+item.text
                       for item in documents if item.enabled)


def render_persona_documents(root):
    return _render(list_persona_documents(root))
