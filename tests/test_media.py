import hashlib
import io
import struct
from datetime import datetime

import pytest
from PIL import Image
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


GROUP = 'selected@chatroom'
AT = datetime(2026, 11, 4).timestamp()
DIGEST = 'a' * 32
SEED = 123456789
ACCOUNT = 'wxid_test'


def test_repeated_media_decode_is_cached_and_changed_file_invalidates(tmp_path,monkeypatch):
    import xiaomao.media as module
    original=module._static_png;decoded=[]
    def decode(data):decoded.append(1);return original(data)
    monkeypatch.setattr(module,'_static_png',decode)
    path=emoji_path(tmp_path);path.write_bytes(picture())
    resolver=module.LocalMediaResolver(tmp_path)
    first=resolver.resolve(xml('emoji'),47,GROUP,AT)
    assert resolver.resolve(xml('emoji'),47,GROUP,AT)==first
    assert len(decoded)==1
    path.write_bytes(picture('blue'))
    assert resolver.resolve(xml('emoji'),47,GROUP,AT)!=first
    assert len(decoded)==2


def picture(color='red', size=(80, 60), fmt='PNG'):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, format=fmt)
    return out.getvalue()


def image_path(root, digest=DIGEST, suffix='.dat', group=GROUP):
    path = root / 'msg/attach' / hashlib.md5(group.encode()).hexdigest() / '2026-11/Img' / (digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def emoji_path(root, digest=DIGEST):
    path = root / 'cache/2026-11/Emoticon' / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def xml(kind='img', digest=DIGEST, extra=''):
    return f'<msg><{kind} md5="{digest}" {extra}/></msg>'


def pixels(data):
    assert data and len(data) <= 1024 * 1024
    im = Image.open(io.BytesIO(data))
    assert im.format == 'PNG' and not getattr(im, 'is_animated', False)
    assert max(im.size) <= 1024
    im.load()
    return im.convert('RGB')


def encrypted(data, key, mode):
    padder = PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), mode).encryptor()
    return cipher.update(padded) + cipher.finalize()


def v2(data, seed=SEED, account=ACCOUNT):
    # Layout fixture independently follows the observed 15-byte container header.
    aes_length = min(1024, len(data))
    tail_length = min(128, len(data) - aes_length)
    key = hashlib.md5((str(seed) + account).encode()).hexdigest()[:16].encode()
    middle_end = len(data) - tail_length
    return (b'\x07\x08V2\x08\x07' + struct.pack('<II', aes_length, tail_length) + b'\x01'
            + encrypted(data[:aes_length], key, modes.ECB()) + data[aes_length:middle_end]
            + bytes(x ^ (seed & 255) for x in data[middle_end:]))


def test_precise_group_and_cache_key_and_original_priority(tmp_path):
    from xiaomao.media import LocalMediaResolver
    key = 'b' * 32
    image_path(tmp_path, key).write_bytes(picture())
    image_path(tmp_path, key, '_t.dat').write_bytes(picture('blue'))
    resolver = LocalMediaResolver(tmp_path)
    assert resolver.resolve(xml(), 3, GROUP, AT) is None
    assert resolver.resolve(xml(), 3, 'other@chatroom', AT, cache_key=key) is None
    assert pixels(resolver.resolve(xml(), 3, GROUP, AT, cache_key=key)).getpixel((0, 0)) == (255, 0, 0)


def test_corrupt_original_falls_back_to_thumbnail(tmp_path):
    from xiaomao.media import LocalMediaResolver
    image_path(tmp_path).write_bytes(b'broken')
    image_path(tmp_path, suffix='_t.dat').write_bytes(picture('blue'))
    assert pixels(LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT)).getpixel((0, 0)) == (0, 0, 255)


def test_legacy_xor_is_decoded_and_validated(tmp_path):
    from xiaomao.media import LocalMediaResolver
    image_path(tmp_path).write_bytes(bytes(x ^ 73 for x in picture()))
    assert pixels(LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT)).size == (80, 60)


@pytest.mark.parametrize('fmt', ['GIF', 'WEBP'])
def test_first_nonempty_animation_frame_is_returned(tmp_path, fmt):
    from xiaomao.media import LocalMediaResolver
    out = io.BytesIO()
    first = Image.new('RGBA', (30, 20), (0, 0, 0, 0))
    second = Image.new('RGBA', (30, 20), (255, 0, 0, 255))
    first.save(out, format=fmt, save_all=True, append_images=[second], duration=100, loop=0, lossless=True)
    emoji_path(tmp_path).write_bytes(out.getvalue())
    assert pixels(LocalMediaResolver(tmp_path).resolve(xml('emoji'), 47, GROUP, AT)).getpixel((10, 10)) == (255, 0, 0)


def test_file_picker_animation_appmsg_uses_same_precise_local_cache(tmp_path):
    from xiaomao.media import LocalMediaResolver
    emoji_path(tmp_path).write_bytes(picture('red',fmt='GIF'))
    raw=f'<msg><appmsg><type>8</type><appattach><emoticonmd5>{DIGEST}</emoticonmd5></appattach></appmsg></msg>'
    assert pixels(LocalMediaResolver(tmp_path).resolve(raw,47,GROUP,AT)).getpixel((10,10))==(255,0,0)


def test_png_output_is_bounded_even_for_incompressible_pixels(tmp_path):
    from xiaomao.media import LocalMediaResolver
    import random
    out = io.BytesIO()
    Image.frombytes('RGB', (1100, 1100), random.Random(9).randbytes(1100 * 1100 * 3)).save(out, format='PNG')
    image_path(tmp_path).write_bytes(out.getvalue())
    result = pixels(LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT))
    assert min(result.size) >= 512


def test_v2_container_decodes_with_validated_account_seed(tmp_path):
    from xiaomao.media import LocalMediaResolver
    image_path(tmp_path).write_bytes(v2(picture(fmt='BMP')))
    resolver = LocalMediaResolver(tmp_path, account_id=ACCOUNT, seed_provider=lambda *_: SEED)
    assert pixels(resolver.resolve(xml(), 3, GROUP, AT)).getpixel((1, 1)) == (255, 0, 0)


def test_wrong_v2_seed_and_bad_container_lengths_return_none(tmp_path):
    from xiaomao.media import LocalMediaResolver
    path = image_path(tmp_path)
    path.write_bytes(v2(picture()))
    resolver = LocalMediaResolver(tmp_path, account_id=ACCOUNT, seed_provider=lambda *_: 99)
    assert resolver.resolve(xml(), 3, GROUP, AT) is None
    path.write_bytes(b'\x07\x08V2\x08\x07' + b'\xff' * 100)
    assert resolver.resolve(xml(), 3, GROUP, AT) is None


def test_account_encrypted_local_emoji_resolves_without_xml_cdn_key(tmp_path):
    from xiaomao.media import LocalMediaResolver
    key = hashlib.md5((str(SEED) + ACCOUNT + 'EMOTICON').encode()).digest()
    emoji_path(tmp_path).write_bytes(encrypted(picture('green', fmt='GIF'), key, modes.CBC(key)))
    resolver = LocalMediaResolver(tmp_path, account_id=ACCOUNT, seed_provider=lambda *_: SEED)
    assert pixels(resolver.resolve(xml('emoji'), 47, GROUP, AT)).getpixel((1, 1)) == (0, 128, 0)


@pytest.mark.parametrize('raw', [xml(digest='../outside'), '<!DOCTYPE msg [<!ENTITY x "bad">]><msg><img md5="' + DIGEST + '"/></msg>', '<msg><img', '<msg>' + 'a' * 65536 + '</msg>'])
def test_untrusted_xml_is_rejected(tmp_path, raw):
    from xiaomao.media import LocalMediaResolver
    image_path(tmp_path).write_bytes(picture())
    assert LocalMediaResolver(tmp_path).resolve(raw, 3, GROUP, AT) is None


def test_xml_paths_and_urls_never_trigger_file_or_network_reads(tmp_path):
    from xiaomao.media import LocalMediaResolver
    outside = tmp_path / 'outside.png'
    outside.write_bytes(picture())
    raw = xml(extra=f'path="{outside}" cdnthumburl="http://127.0.0.1/private"')
    assert LocalMediaResolver(tmp_path).resolve(raw, 3, GROUP, AT) is None


def test_symlink_cannot_escape_or_redirect_cache(tmp_path):
    from xiaomao.media import LocalMediaResolver
    outside = tmp_path / 'elsewhere.png'
    outside.write_bytes(picture())
    path = image_path(tmp_path)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    assert LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT) is None


def test_oversized_source_and_pixel_dimensions_are_rejected(tmp_path, monkeypatch):
    import xiaomao.media as media
    path = image_path(tmp_path)
    path.write_bytes(picture())
    monkeypatch.setattr(media, 'MAX_INPUT_BYTES', 64)
    assert media.LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT) is None
    monkeypatch.setattr(media, 'MAX_INPUT_BYTES', 16 * 1024 * 1024)
    monkeypatch.setattr(media, 'MAX_PIXELS', 4000)
    assert media.LocalMediaResolver(tmp_path).resolve(xml(), 3, GROUP, AT) is None


def test_missing_media_can_be_retried_after_cache_arrives(tmp_path):
    from xiaomao.media import LocalMediaResolver
    resolver = LocalMediaResolver(tmp_path)
    assert resolver.resolve(xml(), 3, GROUP, AT) is None
    image_path(tmp_path).write_bytes(picture())
    assert pixels(resolver.resolve(xml(), 3, GROUP, AT)).size == (80, 60)


def test_cache_key_requires_one_bounded_complete_hex_token():
    from xiaomao.media import extract_cache_key
    assert extract_cache_key(b'\x0a\x22\x12\x20' + b'a' * 32 + b'\x10\x01') == DIGEST
    assert extract_cache_key(b'a' * 33) == ''
    assert extract_cache_key(b'a' * 32 + b'\x00' + b'b' * 32) == ''
    assert extract_cache_key(b'\x00' * 65537) == ''
    assert extract_cache_key(None) == ''


def test_seed_candidates_are_verified_against_actual_container():
    from xiaomao.media import seed_from_chunks
    data = v2(picture())
    assert seed_from_chunks([b'999999\x00' + str(SEED).encode() + b'\0'], ACCOUNT, data, 3) == SEED
    assert seed_from_chunks([b'999999\0'], ACCOUNT, data, 3) is None
    assert seed_from_chunks([str(SEED).encode()], 'other_account', data, 3) is None


def test_wxgf_color_stream_first_frame_is_decoded_locally(tmp_path):
    import imageio_ffmpeg
    import subprocess
    from xiaomao.media import LocalMediaResolver
    # Real HEVC encoder/decoder integration. No WeChat content, files or network.
    process = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                              '-f', 'lavfi', '-i', 'color=c=red:s=64x48:d=0.1', '-frames:v', '1',
                              '-c:v', 'libx265', '-x265-params', 'log-level=error:pools=1',
                              '-f', 'hevc', 'pipe:1'], capture_output=True, timeout=10,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert process.returncode == 0
    # Header has big-endian dimensions; the first HEVC VPS marks the color stream.
    wrapped = b'wxgf\x0c\x00\x01\x00\x40\x00\x30\x00' + struct.pack('>I', len(process.stdout)) + process.stdout
    emoji_path(tmp_path).write_bytes(wrapped)
    result = pixels(LocalMediaResolver(tmp_path).resolve(xml('emoji'), 47, GROUP, AT))
    red, green, blue = result.getpixel((20, 20))
    assert red > 240 and green < 10 and blue < 10
    assert result.size == (64, 48)


def test_wxgf_mask_partition_is_not_returned_as_the_picture(tmp_path):
    import imageio_ffmpeg
    import subprocess
    from xiaomao.media import LocalMediaResolver
    streams = []
    for color, fmt in [('white', 'gray'), ('red', 'yuv420p')]:
        process = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                                  '-f', 'lavfi', '-i', f'color=c={color}:s=64x48:d=0.1', '-frames:v', '1',
                                  '-pix_fmt', fmt, '-c:v', 'libx265', '-x265-params', 'log-level=error:pools=1',
                                  '-f', 'hevc', 'pipe:1'], capture_output=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 0
        streams.append(struct.pack('>I', len(process.stdout)) + process.stdout)
    emoji_path(tmp_path).write_bytes(b'wxgf\x0c\x00\x02\x00\x40\x00\x30\x00' + b''.join(streams))
    frame = pixels(LocalMediaResolver(tmp_path).resolve(xml('emoji'), 47, GROUP, AT))
    red, green, blue = frame.getpixel((20, 20))
    assert red > 240 and green < 10 and blue < 10


def test_wxgf_invalid_dimensions_do_not_enter_decoder(tmp_path):
    from xiaomao.media import LocalMediaResolver
    emoji_path(tmp_path).write_bytes(b'wxgf\x0c\x00\x01\xff\xff\xff\xff\x00' + b'\x00\x00\x00\x01\x40\x01')
    assert LocalMediaResolver(tmp_path).resolve(xml('emoji'), 47, GROUP, AT) is None
