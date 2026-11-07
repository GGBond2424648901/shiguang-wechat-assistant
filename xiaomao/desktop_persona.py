"""Desktop persona workspace backed by the existing prompt stores."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .persona import DEFAULT, PERSONA_CATEGORIES, load_persona, save_persona
from .persona_documents import (
    import_persona_file, import_persona_text, list_persona_documents,
    update_persona_document,
)
from .persona_modules import PersonaModule, PersonaModules, _validate, export_modules, load_modules, save_modules
from .privacy import atomic_private_write


def _module_rows(rows):
    if not isinstance(rows, list) or len(rows) > 48:
        raise ValueError('人格最多可包含 48 个模块')
    try:
        return tuple(PersonaModule(**row) for row in rows)
    except (TypeError, ValueError):
        raise ValueError('人格模块格式无效') from None


class DesktopPersona:
    def __init__(self, root):
        self.root = root

    def read(self):
        state = load_modules(self.root)
        if state is not None:
            return {'format': 'modules', 'title': state.title, 'revision': state.revision,
                    'modules': [asdict(row) for row in state.modules]}
        main = load_persona(self.root)
        documents = list_persona_documents(self.root)
        rows = [{'id': 'main', 'title': '基础人格', 'text': main, 'enabled': True, 'origin': '主稿'}]
        rows.extend({'id': item.id, 'title': item.title, 'text': item.text, 'enabled': item.enabled,
                     'origin': item.source_name, 'category': item.category, 'digest': item.sha256} for item in documents)
        metadata = [(item.id, item.title, item.category, item.enabled, item.sha256) for item in documents]
        revision = hashlib.sha256(json.dumps([main, metadata], ensure_ascii=False).encode('utf-8')).hexdigest()
        return {'format': 'legacy', 'title': '当前人格', 'revision': revision, 'modules': rows,
                'defaultText': DEFAULT, 'categories': PERSONA_CATEGORIES}

    def effective(self):
        from .config import Settings
        from .model import ChatModel
        model = ChatModel(Settings(self.root))
        prompt = model._compiled_persona()
        return {'text': prompt.text, 'details': prompt.snapshot()}

    def save(self, expected, title, modules):
        if load_modules(self.root) is None:
            raise ValueError('当前使用基础人格，请在对应的文档中保存')
        save_modules(self.root, _module_rows(modules), title=title, expected_revision=expected)
        return self.read()

    def _legacy(self, expected):
        state = self.read()
        if state['format'] != 'legacy' or state['revision'] != expected:
            raise ValueError('人格已更新，草稿已保留，请重新核对')
        return state

    def save_document(self, expected, id, title, text, enabled, category=None):
        state = self._legacy(expected)
        row = next((item for item in state['modules'] if item['id'] == id), None)
        if not row:
            raise ValueError('该文档已不存在，请重新加载')
        if id == 'main':
            save_persona(self.root, text)
        else:
            update_persona_document(self.root, id, title=title, text=text, enabled=enabled,
                                    category=category, expected_sha256=row['digest'])
        return self.read()

    def import_text(self, expected, title, text, category):
        self._legacy(expected)
        doc = import_persona_text(self.root, text, title=title, category=category, enabled=False)
        return {'snapshot': self.read(), 'selected': doc.id}

    def import_files(self, paths, expected, category='custom'):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 48:
            raise ValueError('一次最多选择 48 份文档')
        state = self.read()
        if state['revision'] != expected:
            raise ValueError('人格已更新，草稿已保留，请重新核对')
        files = tuple(Path(path) for path in paths)
        if state['format'] == 'legacy':
            documents = [import_persona_file(self.root, path, enabled=False, category=category) for path in files]
            return {'snapshot': self.read(), 'selected': documents[-1].id, 'count': len(documents)}

        from .persona import validate_persona
        created = []
        for path in files:
            if path.suffix.lower() not in ('.json', '.md', '.markdown', '.txt') or path.stat().st_size > 3 * 1024 * 1024:
                raise ValueError('请选择 3 MB 以内的 JSON 模块备份、Markdown 或 UTF-8 纯文本')
            try:
                raw = path.read_bytes().decode('utf-8-sig')
                if path.suffix.lower() == '.json':
                    data = json.loads(raw)
                    if not isinstance(data, dict) or set(data) != {'version', 'title', 'modules'} or data['version'] != 1:
                        raise ValueError('不是受支持的人格模块备份')
                    imported = _module_rows(data['modules'])
                    _validate(data['title'], imported)
                else:
                    validate_persona(raw)
                    if '<!-- persona-module:' in raw:
                        raise ValueError('请使用 JSON 模块备份恢复各模块及启用状态')
                    imported = (PersonaModule('imported', path.stem, raw, origin='用户导入'),)
            except UnicodeDecodeError:
                raise ValueError('人格文档请另存为 UTF-8 编码后导入') from None
            created.extend(replace(row, id='custom_' + uuid4().hex) for row in imported)
            _validate(state['title'], _module_rows(state['modules']) + tuple(created))
        return {'modules': [asdict(row) for row in created], 'count': len(created)}

    def export(self, path, title, modules):
        if load_modules(self.root) is None:
            raise ValueError('当前未使用模块人格')
        rows = _module_rows(modules)
        _validate(title, rows)
        path = Path(path)
        if path.suffix.lower() == '.md':
            text = export_modules(PersonaModules(title, rows, ''), include_disabled=True)
        elif path.suffix.lower() == '.json':
            text = json.dumps({'version': 1, 'title': title, 'modules': modules}, ensure_ascii=False, indent=2)
        else:
            raise ValueError('请选择 JSON 备份或 Markdown 阅读版')
        atomic_private_write(path, text.encode('utf-8'))
        return {'saved': True}
