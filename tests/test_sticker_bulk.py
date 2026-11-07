"""Private bulk imports must retain originals and expose every valid image."""
import hashlib
import io
import json
import zipfile

from PIL import Image

from xiaomao.domain import Message
from xiaomao.sticker_import import StickerShelf
from xiaomao.stickers import StickerLibrary


def picture_bytes(fmt='PNG', *, color='pink', animated=False, size=(80, 80)):
    image = Image.new('RGB', size, color)
    output = io.BytesIO()
    if animated:
        image.save(output, format='GIF', save_all=True,
                   append_images=[Image.new('RGB', size, 'blue'), Image.new('RGB', size, 'green')],
                   duration=[40, 90, 130], loop=3)
    else:
        image.save(output, format=fmt)
    return output.getvalue()


def importer(root, source, **kwargs):
    from xiaomao.sticker_bulk import BulkStickerImporter
    return BulkStickerImporter(root, source, **kwargs)


def test_bulk_classifies_pixels_deduplicates_and_accounts_for_invalid_and_video(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes('GIF', animated=True)
    (source/'misnamed.png').write_bytes(raw)
    (source/'same.gif').write_bytes(raw)
    (source/'broken.png').write_bytes(b'broken image')
    (source/'movie.mp4').write_bytes(b'\x00\x00\x00\x18ftypisom'+b'\0'*40)
    (source/'notes.txt').write_text('not an image', encoding='utf-8')
    report = importer(tmp_path/'app', source).run()
    assert report['counts']['source_files'] == 5
    assert report['counts']['image_occurrences'] == 2
    assert report['counts']['unique_images'] == 1
    assert report['counts']['duplicate_occurrences'] == 1
    assert report['counts']['corrupt'] == 1
    assert report['counts']['excluded_video'] == 1
    assert report['counts']['excluded_non_image'] == 1
    assert report['counts']['extension_mismatches'] == 1
    assert report['assets'][0]['format'] == 'GIF'
    assert report['assets'][0]['frames'] == 3
    assert len(StickerLibrary(tmp_path/'app').items) == 1
    assert (source/'misnamed.png').read_bytes() == raw


def test_bulk_keeps_original_gif_bytes_md5_frames_durations_and_loop(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes('GIF', animated=True)
    (source/'hello.gif').write_bytes(raw)
    root = tmp_path/'app'
    report = importer(root, source).run()
    asset = report['assets'][0]
    assert (root/asset['original_path']).read_bytes() == raw
    assert asset['md5'] == hashlib.md5(raw).hexdigest()
    assert asset['frame_durations_ms'] == [40, 90, 130]
    assert asset['loop'] == 3
    library = StickerLibrary(root)
    prepared = library.prepare(library.items[0])
    assert prepared.verified_bytes() == raw
    assert prepared.md5 == hashlib.md5(raw).hexdigest()
    assert prepared.frames == 3


def test_oversized_gif_has_animated_send_variant_and_lossless_archived_original(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes('GIF', animated=True) + b' '*(8*1024*1024)
    (source/'large.gif').write_bytes(raw)
    root = tmp_path/'app'
    report = importer(root, source).run()
    asset = report['assets'][0]
    assert (root/asset['original_path']).read_bytes() == raw
    assert asset['send_sha256'] != asset['sha256']
    assert asset['send_frames'] == 3
    assert asset['send_frame_durations_ms'] == [40, 90, 130]
    assert asset['send_size'] <= 8*1024*1024
    library = StickerLibrary(root)
    prepared = library.prepare(library.items[0])
    with Image.open(prepared.path) as result:
        assert result.n_frames == 3
        assert result.info['loop'] == 3
        result.seek(2); assert result.info['duration'] == 130


def test_large_wallpaper_is_selectable_with_original_kept(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes(size=(2048, 2048))
    (source/'wallpaper.png').write_bytes(raw)
    root = tmp_path/'app'
    report = importer(root, source).run()
    asset = report['assets'][0]
    assert (root/asset['original_path']).read_bytes() == raw
    assert asset['width'] == asset['height'] == 2048
    assert asset['send_width']*asset['send_height'] <= 4_000_000
    library = StickerLibrary(root)
    assert len(library.items) == 1
    assert library.prepare(library.items[0]).frames == 1


def test_zip_members_add_missing_images_without_extracting_paths(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes()
    (source/'one.png').write_bytes(raw)
    with zipfile.ZipFile(source/'backup.zip', 'w') as archive:
        archive.writestr('nested/one.png', raw)
        archive.writestr('nested/two.jpg', picture_bytes('JPEG', color='blue'))
        archive.writestr('../../outside.png', picture_bytes(color='green'))
        archive.writestr('nested/video.mp4', b'\x00\x00\x00\x18ftypisom'+b'\0'*40)
    root = tmp_path/'app'
    report = importer(root, source).run()
    assert report['counts']['source_files'] == 2
    assert report['counts']['archive_members'] == 4
    assert report['counts']['unique_images'] == 2
    assert report['counts']['duplicate_occurrences'] == 1
    assert report['counts']['excluded_video'] == 1
    assert report['counts']['unsafe_archive_members'] == 1
    assert not (tmp_path/'outside.png').exists()
    assert len(StickerLibrary(root).items) == 2


def test_bulk_resumes_cached_metadata_and_does_not_invent_emotion(tmp_path):
    from xiaomao.vision import TextLine
    source = tmp_path/'source'; source.mkdir()
    (source/'001.png').write_bytes(picture_bytes())
    root = tmp_path/'app'
    def ocr(_image):
        return [TextLine('谢谢你', (10, 10, 100, 40), .99)]
    first = importer(root, source, ocr=ocr).run()
    assert '谢谢' in first['assets'][0]['tags']
    assert first['assets'][0]['semantic_source'] == 'local_ocr'
    def unavailable(_image):
        raise AssertionError('unchanged images must reuse OCR metadata')
    second = importer(root, source, ocr=unavailable).run()
    assert second['counts']['cached_sources'] == 1
    assert second['assets'][0]['tags'] == first['assets'][0]['tags']
    (source/'002.png').write_bytes(picture_bytes(color='blue'))
    third = importer(root, source).run()
    unknown = next(a for a in third['assets'] if a['sha256'] != first['assets'][0]['sha256'])
    assert unknown['semantic_source'] == 'unlabeled'
    assert '待标注' in unknown['tags']
    assert not any(t in unknown['tags'] for t in ('开心', '难过', '喜欢', '你好'))


def test_bulk_whale_priority_preserves_disabled_collections_and_writes_private_report(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    (source/'wave.png').write_bytes(picture_bytes())
    root = tmp_path/'app'
    shelf = StickerShelf(root)
    shelf.save_preferences({'菲比': '优先', '呆猫八条': '停用'}, True)
    job = importer(root, source)
    report = job.run()
    rows, prefs, neutral = shelf.snapshot()
    assert prefs == {'鲸鱼娘': '优先', '菲比': '标准', '呆猫八条': '停用'}
    assert not neutral
    library = StickerLibrary(root)
    assert library.preference_label == '鲸鱼娘'
    assert library.candidates([Message('发一张鲸鱼娘表情')])[0].character == '鲸鱼娘'
    on_disk = json.loads((root/'runtime/qa/whale-bulk/report.json').read_text(encoding='utf-8'))
    assert on_disk['counts']['unique_images'] == report['counts']['unique_images']
    assert all(str(root/'runtime') in str((root/a['original_path']).resolve()) for a in report['assets'])


def test_image_with_video_suffix_is_decoded_as_image(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes('WEBP')
    (source/'image.mp4').write_bytes(raw)
    root = tmp_path/'app'
    first = importer(root, source).run()
    assert first['counts']['unique_images'] == 1
    assert first['counts']['excluded_video'] == 0


def test_cached_original_with_changed_bytes_is_repaired_from_source(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    raw = picture_bytes('WEBP')
    (source/'image.webp').write_bytes(raw)
    root = tmp_path/'app'
    first = importer(root, source).run()
    asset = first['assets'][0]
    archived = root/asset['original_path']
    archived.write_bytes(b'x'*len(raw))
    second = importer(root, source).run()
    assert archived.read_bytes() == raw
    assert second['assets'][0]['selectable']
    assert StickerLibrary(root).prepare(StickerLibrary(root).items[0]).frames == 1


def test_ocr_caption_keeps_independently_supported_filename_emotion(tmp_path):
    from xiaomao.vision import TextLine
    source = tmp_path/'source'; source.mkdir()
    (source/'蓝色大肥鱼_笑.gif').write_bytes(picture_bytes('GIF', animated=True))
    report = importer(tmp_path/'app', source,
                      ocr=lambda _: [TextLine('测试', (10, 10, 100, 40), .99)]).run()
    assert report['assets'][0]['semantic_source'] == 'local_ocr'
    assert '开心' in report['assets'][0]['tags']
    assert report['assets'][0]['ocr_text'] == '测试'


def test_visual_reviews_update_searchable_shelf_and_survive_resumed_import(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    (source/'001.png').write_bytes(picture_bytes())
    root = tmp_path/'app'
    job = importer(root, source)
    initial = job.run()
    digest = initial['assets'][0]['sha256']
    assert job.review_annotations({digest: {'meaning': '挥手打招呼', 'tags': ['你好', '招手'],
                                            'evidence': '目视核对举起的手和笑脸'}}) == 1
    library = StickerLibrary(root)
    assert library.candidates([Message('你好')])[0].meaning == '挥手打招呼'
    assert library.filenames[initial['assets'][0]['send_sha256']] == '001.png'
    resumed = importer(root, source).run()
    assert resumed['assets'][0]['semantic_source'] == 'visual_review'
    assert resumed['assets'][0]['tags'] == ['你好', '招手']
    assert resumed['assets'][0]['visual_evidence'] == '目视核对举起的手和笑脸'


def test_resume_preserves_later_manual_shelf_annotations(tmp_path):
    from xiaomao.sticker_import import inspect_picture
    source = tmp_path/'source'; source.mkdir()
    (source/'001.png').write_bytes(picture_bytes())
    root = tmp_path/'app'
    initial = importer(root, source).run()
    asset = initial['assets'][0]
    StickerShelf(root).commit(inspect_picture(root/asset['send_path']),
                             dict(category='鲸鱼娘', meaning='人工补充的打招呼', tags=['你好'], source='人工修订'))
    resumed = importer(root, source).run()
    assert resumed['assets'][0]['meaning'] == '人工补充的打招呼'
    assert resumed['assets'][0]['tags'] == ['你好']
    assert resumed['assets'][0]['semantic_source'] == 'user_review'
    assert StickerLibrary(root).items[0].meaning == '人工补充的打招呼'
