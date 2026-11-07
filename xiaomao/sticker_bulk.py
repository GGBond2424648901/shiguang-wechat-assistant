"""Resumable, local-only sticker ingestion with lossless private originals.

The shelf contains send-compatible files. This module separately retains every
decoded original, its hashes, all frame timings and the source-to-send mapping.
No catalog or user media is written into distributable assets.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import zipfile

from PIL import GifImagePlugin, Image, ImageOps, UnidentifiedImageError

from .privacy import atomic_private_write, make_private, private_dir
from .sticker_import import EXTENSIONS, FILE_SUFFIXES, Picture, StickerShelf, annotation


_VERSION = 1
_MAX_BYTES = 8*1024*1024
_MAX_PIXELS = 4_000_000
_MAX_FRAMES = 400
_MAX_FRAME_PIXELS = 160_000_000
_SOURCE_BYTES = 64*1024*1024
_SOURCE_PIXELS = 50_000_000
_SOURCE_FRAMES = 1000
_SOURCE_FRAME_PIXELS = 600_000_000
_ZIP_MEMBERS = 20000
_ZIP_TOTAL = 8*1024*1024*1024
_VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.wmv', '.m4v', '.flv'}
_IMAGE_SUFFIXES = FILE_SUFFIXES | {'.bmp', '.tif', '.tiff', '.avif', '.ico', '.apng'}
_WORDS = (
    ('开心', ('开心', '好耶', '哈哈', '笑', '高兴', '快乐', '庆祝', '跳舞', '蹦迪')),
    ('晚安', ('晚安', '睡觉', '困', '休息', '睡了')),
    ('抱抱', ('抱抱', '安慰', '贴贴')),
    ('难过', ('难过', '伤心', '哭', '委屈', '流泪')),
    ('惊讶', ('惊讶', '震惊', '惊呆')),
    ('疑惑', ('疑惑', '思考', '为什么', '不懂', '问号', '疑问')),
    ('你好', ('你好', '早安', '招手', '在吗', '嗨')),
    ('喜欢', ('喜欢', '可爱', '害羞', '脸红', '爱心', '比心', '亲亲')),
    ('谢谢', ('谢谢', '感谢', '感动')),
    ('再见', ('再见', '拜拜', '挥手')),
    ('生气', ('生气', '愤怒', '气死', '不满', '怒')),
    ('吃饭', ('吃', '饿', '饭', '馋', '甜甜圈', '筷子', '刀叉')),
    ('围观', ('围观', '吃瓜', '观察')),
    ('赞同', ('点赞', '赞同', '同意', '收到', '好的', '没问题', '赞')),
    ('加油', ('加油', '努力', '奋斗', '打气')),
    ('工作', ('工作', '上班', '打工', '记录', '学习', '笔记', 'Bug')),
    ('紧张', ('紧张', '害怕', '恐惧', '瑟瑟发抖')),
    ('红包', ('红包', '钱', '硬币', '礼物')),
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _clean(value, limit):
    return ''.join(c if c.isprintable() else ' ' for c in str(value)).strip()[:limit]


def _files(source):
    """rg includes hidden/ignored files; fallback supports installations without rg."""
    try:
        result = subprocess.run(['rg', '--files', '--hidden', '--no-ignore', '-0', str(source)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode not in (0, 1):
            raise OSError('rg could not enumerate source')
        paths = [Path(p.decode('utf-8')) for p in result.stdout.split(b'\0') if p]
    except (OSError, UnicodeError):
        paths = [p for p in source.rglob('*') if p.is_file()]
    return sorted(paths, key=lambda p: str(p).casefold())


def _video(header, suffix):
    return (header[4:8] == b'ftyp' or header.startswith(b'\x1aE\xdf\xa3')
            or header.startswith(b'RIFF') and header[8:12] == b'AVI '
            or suffix in _VIDEO_SUFFIXES)


def _image_header(header):
    return (header.startswith((b'GIF87a', b'GIF89a', b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff',
                               b'BM', b'II*\0', b'MM\0*', b'\0\0\1\0'))
            or header.startswith(b'RIFF') and header[8:12] == b'WEBP'
            or header[4:8] == b'ftyp' and header[8:12] in (b'avif', b'avis', b'heic'))


def _safe_member(info):
    name = info.filename.replace('\\', '/')
    path = PurePosixPath(name)
    return (not path.is_absolute() and not any(p in ('..', '') for p in path.parts)
            and not re.match(r'^[A-Za-z]:', name) and '\0' not in name
            and (info.external_attr >> 16) & 0o170000 != 0o120000)


def _decode(data):
    """Decode every frame, so a valid header cannot hide truncated image data."""
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        frames = getattr(image, 'n_frames', 1)
        if (width*height > _SOURCE_PIXELS or frames > _SOURCE_FRAMES
                or width*height*frames > _SOURCE_FRAME_PIXELS):
            raise ValueError('image exceeds bounded local decoder limits')
        fmt = image.format
        durations = []
        loop = image.info.get('loop')
        for index in range(frames):
            image.seek(index); image.load()
            durations.append(max(0, int(image.info.get('duration', 0))))
    return dict(format=fmt, width=width, height=height, frames=frames,
                frame_durations_ms=durations, duration_ms=sum(durations), loop=loop)


def _compatible(meta, size):
    return (meta['format'] in EXTENSIONS and size <= _MAX_BYTES
            and meta['width']*meta['height'] <= _MAX_PIXELS
            and meta['frames'] <= _MAX_FRAMES
            and meta['width']*meta['height']*meta['frames'] <= _MAX_FRAME_PIXELS
            and (meta['frames'] == 1 or meta['format'] == 'GIF'))


def _full_frame_gif(frames, durations, loop):
    """Write every full frame explicitly; Pillow save_all merges equal frames."""
    info = {'duration': durations[0], 'optimize': False}
    if loop is not None:
        info['loop'] = loop
    header, _ = GifImagePlugin.getheader(frames[0], info=info)
    output = io.BytesIO()
    for part in header:
        output.write(part)
    for frame, duration in zip(frames, durations):
        for part in GifImagePlugin.getdata(frame, duration=duration, disposal=2,
                                           include_color_table=True):
            output.write(part)
    output.write(b';')
    return output.getvalue()


def _send_variant(data, meta):
    if _compatible(meta, len(data)):
        return data, meta
    if meta['frames'] > _MAX_FRAMES:
        raise ValueError('all original frames retained; sender supports at most 400 frames')
    with Image.open(io.BytesIO(data)) as original:
        if meta['frames'] == 1:
            rgb = ImageOps.exif_transpose(original).convert('RGBA')
            rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            white = Image.new('RGBA', rgb.size, 'white'); white.alpha_composite(rgb)
            output = io.BytesIO(); white.convert('RGB').save(output, format='PNG')
            result = output.getvalue()
            inspected = _decode(result)
            if not _compatible(inspected, len(result)):
                raise ValueError('static derivative exceeds sender limits')
            return result, inspected
        # All original timings and loop counts survive; no frame thinning.
        for edge in (384, 320, 256, 192, 128):
            rendered = []
            for index in range(meta['frames']):
                original.seek(index)
                rgba = original.convert('RGBA')
                rgba.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                white = Image.new('RGBA', rgba.size, 'white'); white.alpha_composite(rgba)
                rendered.append(white.convert('RGB').quantize(colors=128, method=Image.Quantize.FASTOCTREE))
            result = _full_frame_gif(rendered, meta['frame_durations_ms'], meta['loop'])
            inspected = _decode(result)
            if (inspected['frames'] != meta['frames']
                    or inspected['frame_durations_ms'] != meta['frame_durations_ms']):
                raise ValueError('animation derivative failed frame/timing verification')
            if _compatible(inspected, len(result)):
                return result, inspected
    raise ValueError('all original frames retained; animated derivative exceeds sender limits')


def load_known_metadata(root):
    """Only exact SHA256 matches may borrow existing local semantic annotations."""
    from .stickers import StickerLibrary
    root = Path(root)
    known = StickerLibrary(root).reviewed()
    path = root/'assets/sticker_library/whale_girl/archive_metadata/data/works.json'
    if path.is_file() and path.stat().st_size <= 32*1024*1024:
        try:
            rows = json.loads(path.read_text(encoding='utf-8'))
            for row in rows:
                digest = row.get('sha256', '')
                if not re.fullmatch(r'[a-f0-9]{64}', digest) or digest in known:
                    continue
                name = _clean(row.get('name', ''), 120)
                tags = [_clean(t, 20) for t in row.get('tags', []) if isinstance(t, str) and t.strip()][:8]
                if name and tags:
                    known[digest] = dict(meaning=name, tags=tags, source='本地档案精确 SHA256 匹配')
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return known


class BulkStickerImporter:
    """Call run repeatedly to resume; pass LocalOCR() for optional local tagging.

    progress receives dictionaries after bounded chunks, never source media.
    The returned report is also persisted privately at runtime/qa/whale-bulk.
    """
    def __init__(self, root, source, *, category='鲸鱼娘', ocr=None, known=None,
                 include_archives=True, progress=None):
        self.root = Path(root).resolve()
        self.source = Path(source).resolve()
        if not self.source.is_dir():
            raise ValueError('source library directory does not exist')
        if not isinstance(category, str) or not category.strip() or len(category) > 20:
            raise ValueError('invalid collection category')
        self.category = category.strip()
        self.ocr = ocr
        self.known = load_known_metadata(self.root) if known is None else known
        self.include_archives = include_archives
        self.progress = progress
        self.shelf = StickerShelf(self.root)
        self.directory = private_dir(self.shelf.directory/'bulk')
        self.originals = private_dir(self.shelf.directory/'originals')
        self.variants = private_dir(self.directory/'variants')
        self.report_directory = private_dir(self.root/'runtime/qa/whale-bulk')
        self.database = self.directory/'metadata.sqlite3'
        self._db = None
        self._assets = {}
        self._records = []
        self._counts = Counter()

    def _notify(self, phase, **details):
        if self.progress:
            self.progress(dict(phase=phase, **details))

    def _cache_source(self, identity, stamp, result):
        self._db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?)',
                         (identity, stamp, _json(result)))
        self._db.commit()

    def _store_asset(self, asset):
        self._db.execute('INSERT OR REPLACE INTO assets VALUES(?,?)',
                         (asset['sha256'], _json(asset)))
        self._db.commit()

    def _remember(self, record):
        self._records.append(record)
        if len(self._records) % 50 == 0:
            self._notify('inventory', processed=len(self._records))

    def _read_source(self, identity, stamp, name, loader, *, header=None, size=None):
        cached = self._db.execute('SELECT stamp,payload FROM sources WHERE identity=?', (identity,)).fetchone()
        if cached and cached[0] == stamp:
            record = json.loads(cached[1])
            digest = record.get('sha256')
            if not digest or self._load_asset(digest):
                self._counts['cached_sources'] += 1
                self._remember(record)
                return
        suffix = PurePosixPath(name.replace('\\', '/')).suffix.lower()
        record = dict(source=identity, filename=name, size=size)
        try:
            if header is not None and _video(header, suffix) and not _image_header(header):
                record.update(status='excluded_video', reason='video container/signature')
                self._cache_source(identity, stamp, record); self._remember(record)
                return
            if size is not None and size > _SOURCE_BYTES:
                if suffix not in _IMAGE_SUFFIXES:
                    record.update(status='excluded_non_image', reason='non-image package/file')
                else:
                    record.update(status='limited', reason='source exceeds 64 MiB decoder quota')
            else:
                data = loader()
                try:
                    with Image.open(io.BytesIO(data)):
                        pass
                except UnidentifiedImageError:
                    record.update(status='corrupt' if suffix in _IMAGE_SUFFIXES else 'excluded_non_image',
                                  reason='unrecognized image data')
                else:
                    digest = hashlib.sha256(data).hexdigest()
                    asset = self._load_asset(digest)
                    if asset is None:
                        meta = _decode(data)
                        extension = EXTENSIONS.get(meta['format'], _clean(meta['format'].lower(), 10))
                        original = self.originals/(digest+'.'+extension)
                        if not original.exists() or hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                            atomic_private_write(original, data)
                        asset = dict(sha256=digest, md5=hashlib.md5(data).hexdigest(), size=len(data),
                                     original_path=str(original.relative_to(self.root)).replace('\\', '/'),
                                     filename=name, **meta)
                        try:
                            send_data, send_meta = _send_variant(data, meta)
                            send_hash = hashlib.sha256(send_data).hexdigest()
                            send_ext = EXTENSIONS[send_meta['format']]
                            send_path = original if send_hash == digest else self.variants/(send_hash+'.'+send_ext)
                            if not send_path.exists() or hashlib.sha256(send_path.read_bytes()).hexdigest() != send_hash:
                                atomic_private_write(send_path, send_data)
                            asset.update(send_sha256=send_hash, send_md5=hashlib.md5(send_data).hexdigest(),
                                         send_path=str(send_path.relative_to(self.root)).replace('\\', '/'),
                                         send_size=len(send_data), send_extension=send_ext,
                                         **{'send_'+k: v for k, v in send_meta.items()})
                        except (ValueError, OSError) as error:
                            asset['send_error'] = _clean(error, 300)
                        self._assets[digest] = asset
                        self._store_asset(asset)
                    record.update(status='image', sha256=digest, actual_format=asset['format'],
                                  extension_mismatch=suffix not in {'.'+EXTENSIONS.get(asset['format'], ''),
                                                                    '.jpeg' if asset['format'] == 'JPEG' else ''})
        except (OSError, ValueError, EOFError, Image.DecompressionBombError, zipfile.BadZipFile) as error:
            record.update(status='corrupt', reason=_clean(type(error).__name__+': '+str(error), 300))
        self._cache_source(identity, stamp, record)
        self._remember(record)

    def _load_asset(self, digest):
        if digest in self._assets:
            return self._assets[digest]
        cached = self._db.execute('SELECT payload FROM assets WHERE digest=?', (digest,)).fetchone()
        if not cached:
            return None
        asset = json.loads(cached[0])
        original = self.root/asset['original_path']
        send = self.root/asset.get('send_path', asset['original_path'])
        if (not original.is_file() or original.stat().st_size != asset['size'] or not send.is_file()
                or hashlib.sha256(original.read_bytes()).hexdigest() != digest
                or send != original and hashlib.sha256(send.read_bytes()).hexdigest() != asset.get('send_sha256')):
            return None
        self._assets[digest] = asset
        return asset

    def _archive(self, path, relative, stamp):
        self._counts['archives'] += 1
        try:
            with zipfile.ZipFile(path) as archive:
                entries = [info for info in archive.infolist() if not info.is_dir()]
                if len(entries) > _ZIP_MEMBERS or sum(i.file_size for i in entries) > _ZIP_TOTAL:
                    self._remember(dict(source=relative, status='limited', reason='archive exceeds bounded extraction quota'))
                    return
                self._counts['archive_members'] += len(entries)
                for index, info in enumerate(entries):
                    identity = relative+'!'+info.filename+'#'+str(index)
                    if not _safe_member(info):
                        self._remember(dict(source=identity, filename=info.filename, status='unsafe_archive_member',
                                            reason='unsafe member path/link; never extracted'))
                        continue
                    member_stamp = stamp+':'+str(info.CRC)+':'+str(info.file_size)+':'+str(info.header_offset)
                    # Reading a prefix avoids loading a large video package into memory.
                    with archive.open(info) as member:
                        header = member.read(64)
                    self._read_source(identity, member_stamp, info.filename,
                                      lambda i=info: archive.read(i), header=header, size=info.file_size)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
            self._remember(dict(source=relative, status='corrupt_archive', reason=_clean(error, 300)))

    def _inventory(self):
        paths = _files(self.source)
        self._counts['source_files'] = len(paths)
        for path in paths:
            relative = str(path.relative_to(self.source)).replace('\\', '/')
            if path.is_symlink() or not path.resolve().is_relative_to(self.source):
                self._remember(dict(source=relative, status='excluded_non_image', reason='external symbolic link'))
                continue
            try:
                stat = path.stat()
                stamp = f'{stat.st_size}:{stat.st_mtime_ns}:{_VERSION}'
                if path.suffix.lower() == '.zip' and self.include_archives:
                    self._archive(path, relative, stamp)
                    continue
                with path.open('rb') as stream:
                    header = stream.read(64)
                self._read_source(relative, stamp, path.name, path.read_bytes, header=header, size=stat.st_size)
            except OSError as error:
                self._remember(dict(source=relative, status='corrupt', reason=_clean(error, 300)))

    def _filename_text(self, asset):
        candidates = []
        for name in asset['source_names']:
            stem = PurePosixPath(name.replace('\\', '/')).stem
            stem = re.sub(r'[_ ]?20\d{2}[-_]\d{2}[-_]\d{2}.*$', '', stem)
            stem = re.sub(r'^(?:蓝色大肥鱼|鲸鱼娘|DeepSeek)[_\s-]*', '', stem, flags=re.I)
            if re.fullmatch(r'[\d\W_]+', stem) or re.fullmatch(r'[a-fA-F0-9-]{16,}', stem):
                continue
            candidates.append(_clean(stem.replace('_', ' '), 100))
        return max(candidates, key=len, default='')

    def _base_annotation(self, asset):
        known = self.known.get(asset['sha256'])
        if known:
            try:
                details = annotation(self.category, _clean(known['meaning'], 120),
                                     [_clean(t, 20) for t in known['tags']][:8],
                                     _clean(known.get('source', ''), 1000))
                return dict(meaning=details['meaning'], tags=details['tags'], semantic_source='known_sha256')
            except (KeyError, TypeError, ValueError):
                pass
        name = self._filename_text(asset)
        labels = [word for word, aliases in _WORDS if any(alias.casefold() in name.casefold() for alias in aliases)]
        medium = '动图' if asset['frames'] > 1 else '静图'
        if labels:
            return dict(meaning=_clean('原文件名：'+name+'；用于'+'、'.join(labels[:3])+'话题', 120),
                        tags=(labels+[medium])[:8], semantic_source='source_filename')
        return dict(meaning=_clean('用户图库'+medium+'；原文件：'+asset['filename']+'（语义待标注，按图选用）', 120),
                    tags=['待标注', medium], semantic_source='unlabeled')

    def _ocr_batch(self, assets):
        edge = 480
        sheet = Image.new('RGB', (edge*2, edge*((len(assets)+1)//2)), 'white')
        for index, asset in enumerate(assets):
            with Image.open(self.root/asset['original_path']) as image:
                image.seek(asset['frames']//2)
                sample = ImageOps.exif_transpose(image).convert('RGBA')
                sample.thumbnail((edge-16, edge-16), Image.Resampling.LANCZOS)
                sheet.paste(sample, ((index % 2)*edge+8, (index//2)*edge+8), sample)
        texts = [[] for _ in assets]
        for line in self.ocr(sheet):
            if line.score < .92:
                continue
            left, top, right, bottom = line.box
            column, row = int(max(0, left)//edge), int(max(0, top)//edge)
            index = row*2+column
            if (column > 1 or index >= len(assets) or right > (column+1)*edge
                    or bottom > (row+1)*edge):
                continue
            text = _clean(line.text, 120)
            if text:
                texts[index].append(text)
        for asset, words in zip(assets, texts):
            caption = _clean(' '.join(dict.fromkeys(words)), 180)
            asset['ocr_text'] = caption
            asset['ocr_done'] = True
            if caption:
                labels = [word for word, aliases in _WORDS if any(alias.casefold() in caption.casefold() for alias in aliases)]
                medium = '动图' if asset['frames'] > 1 else '静图'
                if asset['semantic_source'] == 'source_filename':
                    labels = list(dict.fromkeys(labels+[t for t in asset['tags'] if t != medium]))
                # OCR is quoted evidence, not an invented description of a pose.
                asset.update(meaning=_clean('图中文字：'+caption, 120),
                             tags=(labels+[medium, '有文字'])[:8], semantic_source='local_ocr')
            self._store_asset(asset)

    def _annotate(self):
        pending = []
        rows, _, _ = self.shelf.snapshot()
        existing = {row['digest']: row for row in rows}
        for digest, asset in self._assets.items():
            aliases = [r for r in self._records if r.get('sha256') == digest]
            asset['source_names'] = list(dict.fromkeys(r['filename'] for r in aliases))
            asset['sources'] = [r['source'] for r in aliases]
            row = existing.get(asset.get('send_sha256'))
            if row and asset.get('semantic_source'):
                tags = json.loads(row['tags'])
                if (row['meaning'] != asset.get('committed_meaning', asset['meaning'])
                        or tags != asset.get('committed_tags', asset['tags'])):
                    details = annotation(self.category, row['meaning'], tags)
                    asset.update(meaning=details['meaning'], tags=details['tags'], semantic_source='user_review')
            if not asset.get('semantic_source'):
                asset.update(self._base_annotation(asset))
            if (self.ocr and not asset.get('ocr_done')
                    and asset['semantic_source'] not in ('known_sha256', 'visual_review', 'user_review')):
                pending.append(asset)
            self._store_asset(asset)
        for start in range(0, len(pending), 4):
            batch = pending[start:start+4]
            try:
                self._ocr_batch(batch)
            except Exception as error:
                # Local engine availability must not prevent a complete import.
                for asset in batch:
                    asset['ocr_error'] = _clean(type(error).__name__+': '+str(error), 300)
                    self._store_asset(asset)
            self._notify('annotation', processed=min(start+4, len(pending)), total=len(pending))

    def _commit(self):
        imported = 0
        new = 0
        for index, asset in enumerate(self._assets.values()):
            if not asset.get('send_sha256'):
                continue
            path = self.root/asset['send_path']
            picture = Picture(path, asset['send_sha256'], asset['send_extension'], asset['send_size'],
                              asset['send_width'], asset['send_height'], asset['send_frames'],
                              asset['send_duration_ms'], b'')
            source = _clean('用户提供的私有本地素材；原文件：'+asset['filename']+'；原件 SHA256：'+asset['sha256']
                            +'；语义来源：'+asset['semantic_source'], 1000)
            try:
                added = self.shelf.commit(picture, dict(category=self.category, meaning=asset['meaning'],
                                                        tags=asset['tags'], source=source))
                with closing(self.shelf.connect()) as db, db:
                    db.execute('UPDATE media SET filename=? WHERE digest=?',
                               (_clean(PurePosixPath(asset['filename'].replace('\\', '/')).name, 255), asset['send_sha256']))
                imported += 1; new += int(added)
                asset['selectable'] = True
                asset['committed_meaning'] = asset['meaning']
                asset['committed_tags'] = list(asset['tags'])
            except (OSError, ValueError, sqlite3.Error) as error:
                asset['import_error'] = _clean(type(error).__name__+': '+str(error), 300)
                asset['selectable'] = False
            self._store_asset(asset)
            if index % 100 == 0:
                self._notify('import', processed=index+1, total=len(self._assets))
        self._counts['imported_images'] = imported
        self._counts['new_shelf_entries'] = new
        # Whale becomes primary; explicit disabled categories remain disabled.
        _, prefs, _ = self.shelf.snapshot()
        for category in set(prefs) | {'菲比', '呆猫八条'}:
            if category != self.category and prefs.get(category) != '停用':
                prefs[category] = '标准'
        prefs[self.category] = '优先'
        self.shelf.save_preferences(prefs, False)

    def review_annotations(self, reviews):
        """Apply visually checked original-SHA256 annotations without reimporting.

        Each value has meaning, tags and optional evidence. Call after run has
        finished; source pixels are never inferred from the numeric filename.
        """
        if not isinstance(reviews, dict):
            raise ValueError('reviews must be an original-SHA256 mapping')
        checked = []
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            for digest, review in reviews.items():
                row = db.execute('SELECT payload FROM assets WHERE digest=?', (digest,)).fetchone()
                if not row:
                    raise ValueError('review does not refer to an imported original')
                asset = json.loads(row[0])
                details = annotation(self.category, review['meaning'], review['tags'])
                asset.update(meaning=details['meaning'], tags=details['tags'], semantic_source='visual_review',
                             visual_evidence=_clean(review.get('evidence', ''), 400),
                             visually_reviewed_at=datetime.now(timezone.utc).isoformat())
                checked.append(asset)
            for asset in checked:
                if asset.get('send_sha256'):
                    picture = Picture(self.root/asset['send_path'], asset['send_sha256'], asset['send_extension'],
                                      asset['send_size'], asset['send_width'], asset['send_height'],
                                      asset['send_frames'], asset['send_duration_ms'], b'')
                    self.shelf.commit(picture, dict(category=self.category, meaning=asset['meaning'], tags=asset['tags'],
                        source=_clean('用户提供的私有本地素材；原文件：'+asset['filename']+'；原件 SHA256：'
                                      +asset['sha256']+'；语义来源：批量目视核对', 1000)))
                    with closing(self.shelf.connect()) as shelf_db, shelf_db:
                        shelf_db.execute('UPDATE media SET filename=? WHERE digest=?',
                            (_clean(PurePosixPath(asset['filename'].replace('\\', '/')).name, 255), asset['send_sha256']))
                    asset['committed_meaning'] = asset['meaning']
                    asset['committed_tags'] = list(asset['tags'])
                db.execute('UPDATE assets SET payload=? WHERE digest=?', (_json(asset), asset['sha256']))
                db.commit()
        path = self.report_directory/'report.json'
        if path.exists():
            report = json.loads(path.read_text(encoding='utf-8'))
            updated = {a['sha256']: a for a in checked}
            report['assets'] = [updated.get(a['sha256'], a) for a in report['assets']]
            report['semantic_sources'] = dict(Counter(a['semantic_source'] for a in report['assets']))
            report['generated_at'] = datetime.now(timezone.utc).isoformat()
            atomic_private_write(path, _json(report).encode('utf-8'))
        return len(checked)

    def run(self):
        self._assets = {}; self._records = []; self._counts = Counter()
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            self._db = db
            db.executescript('CREATE TABLE IF NOT EXISTS sources(identity TEXT PRIMARY KEY,stamp TEXT,payload TEXT);'
                             'CREATE TABLE IF NOT EXISTS assets(digest TEXT PRIMARY KEY,payload TEXT);')
            make_private(self.database)
            self._inventory()
            self._annotate()
            self._commit()
            counts = self._counts
            for key in ('archives', 'archive_members', 'cached_sources', 'corrupt', 'excluded_video',
                        'excluded_non_image', 'unsafe_archive_members', 'limited', 'corrupt_archive'):
                counts[key] += sum(r['status'] == key.rstrip('s') if key == 'unsafe_archive_members'
                                   else r['status'] == key for r in self._records)
            images = [r for r in self._records if r['status'] == 'image']
            counts['image_occurrences'] = len(images)
            counts['unique_images'] = len(self._assets)
            counts['duplicate_occurrences'] = len(images)-len(self._assets)
            counts['extension_mismatches'] = sum(bool(r.get('extension_mismatch')) for r in images)
            counts['animated_images'] = sum(a['frames'] > 1 for a in self._assets.values())
            counts['static_images'] = len(self._assets)-counts['animated_images']
            counts['derivatives'] = sum(a.get('send_sha256') not in (None, a['sha256']) for a in self._assets.values())
            counts['selectable_variants'] = len({a['send_sha256'] for a in self._assets.values() if a.get('selectable')})
            report = dict(schema_version=_VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
                          source_root=str(self.source), category=self.category, private=True,
                          counts=dict(counts), formats=dict(Counter(a['format'] for a in self._assets.values())),
                          semantic_sources=dict(Counter(a['semantic_source'] for a in self._assets.values())),
                          assets=list(self._assets.values()), records=self._records)
            atomic_private_write(self.report_directory/'report.json', _json(report).encode('utf-8'))
            self._notify('complete', counts=dict(counts))
        self._db = None
        return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--no-ocr', action='store_true', help='import with exact-match/filename metadata only')
    parser.add_argument('--no-archives', action='store_true')
    args = parser.parse_args()
    ocr = None
    if not args.no_ocr:
        from .vision import LocalOCR
        ocr = LocalOCR()
    def progress(state):
        print(json.dumps(state, ensure_ascii=True), flush=True)
    report = BulkStickerImporter(args.root, args.source, ocr=ocr,
                                 include_archives=not args.no_archives, progress=progress).run()
    print(json.dumps({'counts': report['counts'], 'semantic_sources': report['semantic_sources']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
