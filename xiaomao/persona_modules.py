"""Lossless, atomic persona modules. No model is allowed to mutate this store."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .privacy import atomic_private_write, private_dir
from .persona_documents import archive_persona_source, file_revision


@dataclass(frozen=True)
class PersonaModule:
    id: str
    title: str
    text: str
    enabled: bool = True
    origin: str = '自定义'


@dataclass(frozen=True)
class PersonaModules:
    title: str
    modules: tuple[PersonaModule, ...]
    revision: str


def manifest_path(root):
    return Path(root)/'config'/'persona_modules.json'


def modules_revision(root):
    return file_revision(manifest_path(root))


def _validate(title, modules):
    from .persona import validate_persona
    if not isinstance(title,str) or not 1<=len(title.strip())<=100 or len(title.splitlines())!=1:
        raise ValueError('人格名称需为 1 至 100 字的一行文字')
    validate_persona(title)
    if not 1<=len(modules)<=48 or not any(m.enabled for m in modules):
        raise ValueError('人格需包含 1 至 48 个模块，至少启用一个')
    ids=set()
    for m in modules:
        if (not isinstance(m,PersonaModule) or not isinstance(m.id,str)
                or not re.fullmatch(r'[a-z0-9_-]{1,64}',m.id) or m.id in ids
                or not isinstance(m.title,str) or not 1<=len(m.title.strip())<=100
                or len(m.title.splitlines())!=1 or type(m.enabled) is not bool
                or not isinstance(m.origin,str) or not 1<=len(m.origin)<=80):
            raise ValueError('人格模块名称、来源或标识无效')
        validate_persona(m.text);validate_persona(m.title);validate_persona(m.origin)
        ids.add(m.id)
    # Budget is explicit and validated before commit, never a silent truncation.
    if sum(len(m.text) for m in modules)>200000:
        raise ValueError('人格模块合计超过 200000 字，未保存或截断任何内容')


@lru_cache(maxsize=24)
def _load(path,revision):
    if revision is None:return None
    if revision[0]>3*1024*1024:raise ValueError('人格模块文件超过 3 MB，未读取不完整规则')
    try:
        raw=path.read_bytes();data=json.loads(raw)
        if set(data)!={'version','title','modules'} or data['version']!=1:raise ValueError()
        modules=tuple(PersonaModule(**m) for m in data['modules'])
        _validate(data['title'],modules)
        return PersonaModules(data['title'],modules,hashlib.sha256(raw).hexdigest())
    except (ValueError,TypeError,KeyError,UnicodeDecodeError):
        raise ValueError('人格模块文件无效，原文件保留；不会退回旧稿或忽略模块') from None


def load_modules(root):
    path=manifest_path(root)
    return _load(path,file_revision(path))


def export_modules(state,*,include_disabled=False):
    parts=[f'# {state.title}\n\n']
    for m in state.modules:
        if not m.enabled and not include_disabled:continue
        parts.append(f'\n<!-- persona-module:{m.id}; {"启用" if m.enabled else "停用"}; {m.title} -->\n')
        parts.append(m.text)
        parts.append(f'\n<!-- /persona-module:{m.id} -->\n')
    return ''.join(parts)


@contextmanager
def _locked(directory):
    """Serialize read/compare/write across panels, not merely Python threads."""
    path=directory/'persona_modules.lock'
    with path.open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            deadline=time.monotonic()+3
            while True:
                try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>=deadline:raise ValueError('人格正在保存，请稍后重试') from None
                    time.sleep(.05)
            try:yield
            finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def save_modules(root,modules,*,title,expected_revision):
    modules=tuple(modules);_validate(title,modules)
    path=manifest_path(root);directory=private_dir(path.parent)
    with _locked(directory):
        previous=load_modules(root)
        if (previous.revision if previous else None)!=expected_revision:
            raise ValueError('人格已被其他操作更新；请先导出当前草稿，再重新加载最新版本')
        # Every revision retains immutable exact originals, including disabled ones.
        for m in modules:archive_persona_source(root,m.text.encode('utf-8'))
        if previous:
            history=private_dir(directory/'persona_module_versions')
            atomic_private_write(history/(previous.revision+'.json'),path.read_bytes())
        raw=json.dumps({'version':1,'title':title,'modules':[asdict(m) for m in modules]},
                       ensure_ascii=False,indent=2).encode('utf-8')
        atomic_private_write(path,raw)
    return load_modules(root)
