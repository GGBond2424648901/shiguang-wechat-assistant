"""A panel-local review queue for files chosen through the native picker."""

from dataclasses import dataclass
import hashlib
from itertools import islice
from pathlib import Path
from threading import Event, RLock, Thread
import uuid

from .config import Settings
from .desktop_catalog import image_result
from .sticker_import import FILE_SUFFIXES, Picture, annotation, inspect_picture


@dataclass
class ImportRow:
    path: Path
    picture: Picture | None = None
    status: str = '待识别'
    labels: dict | None = None

    def present(self, identity):
        labels = self.labels or {}
        picture = self.picture
        return {
            'id': identity, 'name': self.path.name, 'status': self.status,
            'category': labels.get('character', ''), 'meaning': labels.get('meaning', ''),
            'tags': labels.get('tags', []), 'source': labels.get('source', ''),
            'size': picture.size if picture else 0, 'width': picture.width if picture else 0,
            'height': picture.height if picture else 0, 'frames': picture.frames if picture else 0,
        }


class DesktopImportQueue:
    def __init__(self, root, catalog, emit):
        self.root = root
        self.catalog = catalog
        self.emit = emit
        self.rows = {}
        self.job = None
        self.cancel = Event()
        self.lock = RLock()

    def snapshot(self):
        with self.lock:
            return {'rows': [row.present(key) for key, row in self.rows.items()],
                    'job': dict(self.job) if self.job else None}

    def _available(self):
        if self.job and self.job['state'] == 'running':
            raise ValueError('正在识别，请先等待或停止当前任务')

    def pick(self, paths, folder=False):
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths) or len(paths) > 100:
            raise ValueError('每批最多选择 100 张图片')
        if folder:
            if len(paths) != 1 or not Path(paths[0]).is_dir():
                raise ValueError('请选择有效文件夹')
            paths = list(islice((p for p in Path(paths[0]).rglob('*')
                                if p.is_file() and p.suffix.lower() in FILE_SUFFIXES), 101))
        paths = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
        with self.lock:
            self._available()
            existing = {row.path for row in self.rows.values()}
            new = [path for path in paths if path not in existing]
            if len(new) + len(self.rows) > 100:
                raise ValueError('每批最多 100 张，请先移出已处理素材')
            if not paths:
                raise ValueError('没有找到支持的表情图片')
            for path in new:
                row = ImportRow(path)
                try:
                    row.picture = inspect_picture(path)
                except (ValueError, OSError):
                    row.status = '图片不可用'
                self.rows[uuid.uuid4().hex] = row
            return self.snapshot()

    def _select(self, ids):
        if not isinstance(ids, list) or len(ids) > 100 or not all(isinstance(key, str) for key in ids):
            raise ValueError('素材选择无效')
        if any(key not in self.rows for key in ids):
            raise ValueError('素材队列已变化，请重新选择')
        return tuple(dict.fromkeys(ids))

    def remove(self, ids):
        with self.lock:
            self._available()
            for key in self._select(ids):
                self.rows.pop(key)
            return self.snapshot()

    def preview(self, id, variant='thumbnail'):
        if variant not in ('thumbnail', 'original'):
            raise ValueError('素材预览格式无效')
        with self.lock:
            row = self.rows.get(id)
            if row is None or row.picture is None:
                raise ValueError('素材预览不可用')
            picture = row.picture
        if variant == 'original' and picture.extension == 'gif' and picture.frames > 1:
            with picture.path.open('rb') as file:
                content = file.read(picture.size + 1)
            if len(content) != picture.size or hashlib.sha256(content).hexdigest() != picture.digest:
                raise ValueError('原件已变化，请重新导入')
            return image_result(content, 'image/gif')
        return image_result(picture.thumbnail or picture.preview, 'image/png')

    def analyze(self, ids, category=''):
        if not isinstance(category, str) or len(category) > 20 or (category and not category.isprintable()):
            raise ValueError('素材分类无效')
        with self.lock:
            self._available()
            selected = self._select(ids)
            if not selected:
                raise ValueError('请先选择图片')
            self.cancel.clear()
            self.job = {'id': uuid.uuid4().hex, 'state': 'running', 'completed': 0, 'total': len(selected)}
            Thread(target=self._analyze, args=(selected, category), daemon=True).start()
            return self.snapshot()

    def _analyze(self, keys, category):
        from .model import ChatModel
        try:
            model = ChatModel(Settings.load(self.root))
            with self.catalog.lock:
                self.catalog._refresh()
                shelf = self.catalog.library.shelf
                known = self.catalog.library.reviewed()
            for key in keys:
                if self.cancel.is_set():
                    break
                with self.lock:
                    row = self.rows[key]
                    picture = row.picture
                    row.status = '识别中'
                self.emit(self.snapshot())
                result, status = None, '图片不可用'
                if picture is not None:
                    try:
                        result, status = shelf.analyze(picture, category, model, known)
                    except Exception:
                        status = '待填写，模型识别未完成'
                with self.lock:
                    row.labels = result or row.labels or ({'character': category} if category else None)
                    row.status = status
                    self.job['completed'] += 1
                self.emit(self.snapshot())
            with self.lock:
                self.job['state'] = 'cancelled' if self.cancel.is_set() else 'done'
        except Exception:
            with self.lock:
                self.job['state'] = 'failed'
        finally:
            self.emit(self.snapshot())

    def cancel_analysis(self):
        self.cancel.set()
        return self.snapshot()

    def commit(self, selected):
        with self.lock, self.catalog.lock:
            self._available()
            if not isinstance(selected, dict) or not selected:
                raise ValueError('请选择要入库的素材')
            keys = self._select(list(selected))
            payloads = {}
            for key in keys:
                details = selected[key]
                row = self.rows[key]
                if row.picture is None or not isinstance(details, dict) or set(details) != {'category', 'meaning', 'tags', 'source'}:
                    raise ValueError('请先核对素材和标注')
                info = annotation(**details)
                payloads[key] = {**details, 'tags': info['tags']}
            self.catalog._refresh()
            saved = 0
            for key in keys:
                row = self.rows[key]
                try:
                    new = self.catalog.library.shelf.commit(row.picture, payloads[key])
                    row.labels = annotation(**payloads[key])
                    row.status = '已入库' if new else '已更新'
                    saved += 1
                except (OSError, ValueError):
                    row.status = '入库失败，请重新核对原件'
            self.catalog._refresh()
            return {**self.snapshot(), 'saved': saved, 'failed': len(keys) - saved}
