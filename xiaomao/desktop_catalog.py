"""Desktop gallery queries and bounded image reads without a GUI dependency."""

import base64
from collections import OrderedDict, defaultdict
import hashlib
import io
from itertools import zip_longest
from pathlib import Path
from threading import RLock

from PIL import Image, ImageOps

from .sticker_import import CATEGORIES, EXTENSIONS, inspect_picture
from .stickers import StickerLibrary

MIME = {'gif': 'image/gif', 'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}


def image_result(data, mime):
    return {'mime': mime, 'data': base64.b64encode(data).decode('ascii')}


def fingerprint(file):
    try:
        stat = file.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    except FileNotFoundError:
        return None


class DesktopCatalog:
    def __init__(self, root, *, read_only=False):
        self.root = root
        self.read_only = read_only
        self.library = None
        self._base_stamp = None
        self._indexed = None
        self._items = ()
        self._by_id = {}
        self._categories = CATEGORIES
        self._revision = 0
        self._filtered = OrderedDict()
        self._thumbnails = OrderedDict()
        self.lock = RLock()

    def _refresh(self):
        source = self.root / 'assets/sticker_library/catalog.json'
        stamp = fingerprint(source)
        if self.library is None or stamp != self._base_stamp:
            self.library = StickerLibrary(self.root, read_only=self.read_only)
            self._base_stamp = stamp
        self.library.refresh()
        if self.library.items is self._indexed:
            return
        grouped = defaultdict(list)
        for item in self.library.items:
            grouped[item.character].append(item)
        order = tuple(dict.fromkeys((*CATEGORIES, *sorted(grouped))))
        groups = [grouped[key] for key in order if key in grouped]
        for group in groups:
            group.sort(key=lambda item: item.path.parent != self.library.shelf.files)
        self._items = tuple(item for row in zip_longest(*groups) for item in row if item is not None)
        self._by_id = {item.id: item for item in self._items}
        self._indexed = self.library.items
        self._categories = tuple(dict.fromkeys((*order, *self.library.preferences)))
        self._revision += 1
        self._filtered.clear()

    def query(self, category='', query='', offset=0, limit=96):
        if (not isinstance(category, str) or len(category) > 40
                or not isinstance(query, str) or len(query) > 160
                or type(offset) is not int or not 0 <= offset <= 20000
                or type(limit) is not int or not 1 <= limit <= 160):
            raise ValueError('素材筛选或分页无效')
        key = category, query.strip().casefold()
        with self.lock:
            self._refresh()
            if key not in self._filtered:
                selected, needle = key
                self._filtered[key] = tuple(
                    item for item in self._items
                    if (not selected or item.character == selected)
                    and (not needle or needle in ' '.join(
                        (item.character, item.meaning, *item.tags)).casefold()))
                if len(self._filtered) > 12:
                    self._filtered.popitem(last=False)
            self._filtered.move_to_end(key)
            matching = self._filtered[key]
            return {
                'items': [{**item.choice(), 'version': item.sha256[:16]}
                          for item in matching[offset:offset + limit]],
                'total': len(matching), 'offset': offset, 'categories': self._categories,
                'priorities': {c: self.library.preferences.get(c, '优先' if c == '菲比' else '标准')
                               for c in self._categories},
                'neutral': self.library.neutral, 'revision': self._revision,
            }

    def _lookup(self, identity):
        self._refresh()
        item = self._by_id.get(identity) if isinstance(identity, str) else None
        if item is None:
            raise ValueError('素材不存在或已移除')
        return item

    def detail(self, identity):
        with self.lock:
            item = self._lookup(identity)
            source = self.library.sources.get(item.sha256, '')
            revision = self._revision
        self._verified_bytes(item)
        picture = inspect_picture(item.path)
        if picture.digest != item.sha256:
            raise ValueError('素材已变化，请刷新')
        return {
            **item.choice(), 'source': source, 'version': item.sha256[:16], 'revision': revision,
            'size': picture.size, 'width': picture.width, 'height': picture.height,
            'frames': picture.frames, 'extension': picture.extension,
        }

    def save_detail(self, identity, expected, category, meaning, tags, source):
        with self.lock:
            item = self._lookup(identity)
            if (expected != self._revision
                    or not item.path.resolve().is_relative_to(self.root.resolve())):
                raise ValueError('素材已更新，请刷新后再保存')
            picture = inspect_picture(item.path)
            if picture.digest != item.sha256:
                raise ValueError('素材原件已变化，请刷新')
            self.library.shelf.commit(picture, dict(category=category, meaning=meaning, tags=tags, source=source))
            self._refresh()
            return {'saved': True, 'revision': self._revision}

    def save_preferences(self, expected, priorities, neutral):
        with self.lock:
            self._refresh()
            if expected != self._revision:
                raise ValueError('素材库已更新，请刷新后再保存')
            if (not isinstance(priorities, dict) or set(priorities) - set(self._categories)
                    or type(neutral) is not bool):
                raise ValueError('分类偏好无效')
            self.library.shelf.save_preferences(priorities, neutral)
            self._refresh()
            return {'saved': True, 'revision': self._revision}

    def _verified_bytes(self, item):
        path = item.path.resolve()
        allowed = self.root / 'assets/sticker_library', self.root / 'runtime/sticker-library/files'
        if not any(path.is_relative_to(folder.resolve()) for folder in allowed):
            raise ValueError('素材路径无效')
        with path.open('rb') as file:
            data = file.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != item.sha256:
            raise ValueError('素材损坏或内容已更新')
        return data

    def image(self, identity, variant='thumbnail', size=176):
        if variant not in ('thumbnail', 'original') or type(size) is not int or size not in (112, 176, 256):
            raise ValueError('素材预览参数无效')
        with self.lock:
            item = self._lookup(identity)
            key = item.sha256, size, fingerprint(item.path)
            if variant == 'thumbnail' and key in self._thumbnails:
                self._thumbnails.move_to_end(key)
                return self._thumbnails[key]
        data = self._verified_bytes(item)
        try:
            with Image.open(io.BytesIO(data)) as image:
                ext = EXTENSIONS.get(image.format)
                if not ext or image.width * image.height > 4_000_000:
                    raise ValueError('素材图片格式或尺寸无效')
                if variant == 'original':
                    return image_result(data, MIME[ext])
                thumb = ImageOps.exif_transpose(image).convert('RGBA')
                thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                thumb.save(buffer, 'PNG', compress_level=2)
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError('素材预览无法解码') from error
        result = image_result(buffer.getvalue(), 'image/png')
        with self.lock:
            self._thumbnails[key] = result
            if len(self._thumbnails) > 128:
                self._thumbnails.popitem(last=False)
        return result

    def guide(self):
        return (Path(__file__).parent / 'ui_assets/表情包收集说明.txt').read_text(encoding='utf-8')
