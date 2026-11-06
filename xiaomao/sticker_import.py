"""Review new media before making it available to the resident sender."""
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .privacy import atomic_private_write, private_dir


CATEGORIES=('菲比','呆猫八条','鲸鱼娘')
PRIORITIES=('优先','标准','备用','停用')
EXTENSIONS={'GIF':'gif','JPEG':'jpg','PNG':'png','WEBP':'webp'}
FILE_SUFFIXES={'.gif','.jpg','.jpeg','.png','.webp'}


@dataclass(frozen=True)
class Picture:
    path: Path
    digest: str
    extension: str
    size: int
    width: int
    height: int
    frames: int
    duration_ms: int
    preview: bytes


def inspect_picture(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>8*1024*1024:raise ValueError('请选择 8 MiB 以内的图片')
    data=path.read_bytes()
    if len(data)>8*1024*1024:raise ValueError('文件已超过 8 MiB')
    try:
        with Image.open(io.BytesIO(data)) as im:
            ext=EXTENSIONS.get(im.format)
            if not ext or im.width*im.height>4_000_000:raise ValueError('格式不支持或超过 400 万像素')
            frames=getattr(im,'n_frames',1)
            if frames>400 or im.width*im.height*frames>160_000_000:raise ValueError('动画过大，请缩小尺寸或减少帧数')
            if frames>1 and ext!='gif':raise ValueError('动画请先导出为 GIF，避免丢失动态')
            indices=sorted({0,frames//2,frames-1})
            thumbs=[];duration=0
            for i in range(frames):
                im.seek(i);im.load()
                duration+=max(0,int(im.info.get('duration',0)))
                if i not in indices:continue
                thumb=ImageOps.exif_transpose(im).convert('RGBA')
                thumb.thumbnail((320,320),Image.Resampling.LANCZOS)
                thumbs.append(thumb)
            w,h=im.size
        sheet=Image.new('RGB',(sum(t.width for t in thumbs),max(t.height for t in thumbs)),'white')
        x=0
        for t in thumbs:
            sheet.paste(t,(x,(sheet.height-t.height)//2),t);x+=t.width
        preview=io.BytesIO();sheet.save(preview,format='PNG')
        return Picture(path,hashlib.sha256(data).hexdigest(),ext,len(data),w,h,frames,duration,preview.getvalue())
    except (OSError,Image.DecompressionBombError,EOFError) as error:
        raise ValueError('图片损坏、无法解码或尺寸过大') from error


def annotation(category,meaning,tags,source=''):
    fields=((category,20,'分类'),(meaning,120,'用途'),(source,1000,'来源'))
    for value,limit,label in fields:
        if not isinstance(value,str) or len(value.strip())>limit or (value and not value.isprintable()):
            raise ValueError(f'{label}内容无效（最多 {limit} 字）')
    if not category.strip() or not meaning.strip():raise ValueError('请填写分类和聊天用途')
    if not isinstance(tags,(tuple,list)) or not 1<=len(tags)<=8:raise ValueError('请填写 1–8 个情绪标签')
    if any(not isinstance(t,str) or not t.strip() or len(t.strip())>20 or not t.isprintable() for t in tags):
        raise ValueError('单个标签不能为空且最多 20 字')
    return dict(character=category.strip(),meaning=meaning.strip(),tags=list(dict.fromkeys(t.strip() for t in tags)),source=source.strip())


class StickerShelf:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.directory=self.root/'runtime'/'sticker-library'
        self.files=self.directory/'files'
        self.database=self.directory/'shelf.sqlite3'

    def connect(self):
        private_dir(self.directory)
        db=sqlite3.connect(self.database,timeout=5)
        db.row_factory=sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS media(digest TEXT PRIMARY KEY,ext TEXT NOT NULL,category TEXT NOT NULL,
        meaning TEXT NOT NULL,tags TEXT NOT NULL,source TEXT NOT NULL,filename TEXT NOT NULL,frames INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS prefs(category TEXT PRIMARY KEY,priority TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,value INTEGER NOT NULL);
        """)
        return db

    def snapshot(self):
        if not self.database.exists():return [],{},False
        with closing(self.connect()) as db,db:
            db.execute('BEGIN')
            rows=[dict(r) for r in db.execute('SELECT * FROM media ORDER BY rowid DESC LIMIT 10000')]
            prefs=dict(db.execute('SELECT category,priority FROM prefs'))
            flag=db.execute("SELECT value FROM settings WHERE name='neutral'").fetchone()
        return rows,prefs,bool(flag and flag[0])

    def save_preferences(self,prefs,neutral):
        if any(not isinstance(k,str) or not k.strip() or len(k)>20 or v not in PRIORITIES for k,v in prefs.items()):
            raise ValueError('分类或优先级无效')
        with closing(self.connect()) as db,db:
            db.executemany('INSERT OR REPLACE INTO prefs VALUES(?,?)',prefs.items())
            db.execute("INSERT OR REPLACE INTO settings VALUES('neutral',?)",(int(bool(neutral)),))

    def commit(self,picture,details):
        info=annotation(**details)
        data=picture.path.read_bytes()
        if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=picture.digest:
            raise ValueError('文件在识别后发生了变化，请重新选择')
        if picture.extension not in EXTENSIONS.values():raise ValueError('原件格式无效')
        dest=private_dir(self.files)/(picture.digest+'.'+picture.extension)
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            exists=db.execute('SELECT filename FROM media WHERE digest=?',(picture.digest,)).fetchone()
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=picture.digest:
                atomic_private_write(dest,data)
            filename=exists['filename'] if exists and picture.path.resolve()==dest.resolve() else picture.path.name[:255]
            db.execute('INSERT OR REPLACE INTO media VALUES(?,?,?,?,?,?,?,?)',
                (picture.digest,picture.extension,info['character'],info['meaning'],json.dumps(info['tags'],ensure_ascii=False),
                 info['source'],filename,picture.frames))
        return not bool(exists)

    def analyze(self,picture,category,model,known):
        """Trusted local matches keep source/semantics; unknown images use actual pixels."""
        row=known.get(picture.digest)
        if row:
            return annotation(category or row['character'],row['meaning'],row['tags'],row.get('source','')),'已有标注'
        result=model.analyze_sticker(picture.preview,category)
        return annotation(category or result['character'],result['meaning'],result['tags']),'AI 已识别'
