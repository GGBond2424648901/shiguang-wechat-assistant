"""Guarded use of the native WeChat voice recorder in a verified chat window."""

import ctypes
from dataclasses import dataclass
import time

import cv2
import numpy as np
from PIL import Image

from .vision import header_matches as match_header, read_header
from .windows import DesktopUnavailable, _wechat_client_version
from .windows_voice_audio import prepare_voice_output


MAX_CLIP_SECONDS = 52
_RECORDING_LIMIT = 55
_MIN_CLIENT = (4, 1, 9, 0)


@dataclass(frozen=True, slots=True)
class VoiceRecordingResult:
    state: str
    detail: str


@dataclass(frozen=True, slots=True)
class RecordingControls:
    cancel: tuple[int, int]
    submit: tuple[int, int]


def supports_voice_client(version):
    return version is not None and version >= _MIN_CLIENT


def _footer(image, binding, scale, *, require_clear_editor=True):
    if (binding.window_kind not in ('main', 'detached') or image.size != (binding.width, binding.height)
            or not .75 <= scale <= 3):
        return None
    # Measurements at 96 DPI, relative to the lower right of the client.
    # The nominal text Send center must be covered by the reviewed region.
    x, y = image.width - 49 * scale, image.height - 36 * scale
    x1, y1, x2, y2 = binding.rect('send')
    left, top = round(x - 220 * scale), round(y - 31 * scale)
    right, bottom = round(x + 31 * scale), round(y + 27 * scale)
    if not (x1 <= x < x2 and y1 <= y < y2 and 0 <= left < right <= image.width
            and 0 <= top < bottom <= image.height):
        return None
    crop = image.crop((left, top, right, bottom)).convert('RGB')
    pixels = np.asarray(crop.resize((251, 58), Image.Resampling.BILINEAR), dtype=np.int16)
    # The recording row must remain clear of the editable content region.
    if require_clear_editor and top + 8 * scale < binding.rect('input')[3] - 3 * scale:
        return None
    return pixels, left, top


def _point(footer, x, y, scale):
    _, left, top = footer
    return left + round(x * scale), top + round(y * scale)


def _components(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    for index in range(1, count):
        x, y, w, h, _ = map(int, stats[index])
        yield x, y, w, h, labels[y:y + h, x:x + w] == index


def _voice_entry(footer, binding, scale):
    if footer is None:
        return None
    pixels = footer[0]
    search_left, search_top = 151, 12
    search = pixels[search_top:46, search_left:190]
    ink = (search.max(axis=2) < 196) & (np.ptp(search, axis=2) < 18)
    matches = []
    for x, y, w, h, ring in _components(ink):
        if not (13 <= w <= 23 and 13 <= h <= 23 and abs(w - h) <= 4):
            continue
        cx, cy = x + w // 2 + search_left, y + h // 2 + search_top
        if not (161 <= cx <= 178 and 22 <= cy <= 35):
            continue
        yy, xx = np.ogrid[:h, :w]
        radius2 = ((xx - (w - 1) / 2) / (w / 2)) ** 2 + ((yy - (h - 1) / 2) / (h / 2)) ** 2
        if np.count_nonzero(ring & (radius2 < .39)) > 6 or np.count_nonzero(ring & (radius2 > 1.15)) > 6:
            continue
        if not (ring[:3, w // 3:2 * w // 3].any() and ring[-3:, w // 3:2 * w // 3].any()
                and ring[h // 3:2 * h // 3, :3].any() and ring[h // 3:2 * h // 3, -3:].any()):
            continue
        center = ink[y + 3:y + h - 3, x + 3:x + w - 3]
        rows, columns = np.where(center)
        if len(rows) >= 6 and np.ptp(columns) >= 4 and np.ptp(rows) >= 3:
            matches.append(_point(footer, cx, cy, scale))
    if len(matches) != 1:
        return None
    x, y = matches[0]
    if 'voice' in binding.regions:
        x1, y1, x2, y2 = binding.rect('voice')
        if not (x1 <= x < x2 and y1 <= y < y2):
            return None
    return x, y


def locate_voice_entry(image, binding, scale=1.0):
    """Find one gray circle enclosing a waveform beside the calibrated Send."""
    return _voice_entry(_footer(image, binding, scale), binding, scale)


def propose_voice_region(image, binding, scale):
    """Suggest a tight region on a calibration preview without sending input."""
    footer = _footer(image, binding, scale, require_clear_editor=False)
    point = _voice_entry(footer, binding, scale)
    if point is None:
        return None
    x, y = point
    margin = round(12 * scale)
    return [(x - margin) / image.width, (y - margin) / image.height,
            (x + margin) / image.width, (y + margin) / image.height]


def _pill_visible(pixels, cy):
    # Sample the straight part near the left end, clear of animation and
    # the pointer halo left at the voice entry when the recorder opens.
    inside = pixels[cy - 13:cy - 9, 60:106]
    above = pixels[cy - 21:cy - 18, 60:106]
    below = pixels[cy + 19:cy + 22, 60:106]
    fill = np.median(inside, axis=(0, 1))
    return (175 <= fill.min() and fill.max() <= 249 and np.ptp(fill) <= 12
            and (abs(inside - fill).max(axis=2) <= 7).mean() >= .82
            and all(abs(np.median(sample, axis=(0, 1)) - fill).max() >= 4 for sample in (above, below)))


def _cancel_point(footer, scale):
    pixels = footer[0]
    search_left, search_top = 5, 10
    search = pixels[search_top:43, search_left:40]
    fill = np.median(search, axis=(0, 1))
    if not (155 <= fill.min() and fill.max() <= 250 and np.ptp(fill) <= 15):
        return None
    corners = (search.max(axis=2) < fill.max() - 24) | (search.min(axis=2) > fill.min() + 24)
    matches = []
    for x, y, w, h, cross in _components(corners & (np.ptp(search, axis=2) < 18)):
        if not (7 <= w <= 16 and 7 <= h <= 16 and abs(w - h) <= 4):
            continue
        cx, cy = x + w // 2 + search_left, y + h // 2 + search_top
        if not (14 <= cx <= 30 and 21 <= cy <= 33):
            continue
        corner_w, corner_h = max(2, w // 3), max(2, h // 3)
        if (all(block.any() for block in (cross[:corner_h, :corner_w], cross[:corner_h, -corner_w:],
                                          cross[-corner_h:, :corner_w], cross[-corner_h:, -corner_w:]))
                and cross[h // 2, w // 2] and cross.mean() <= .64 and _pill_visible(pixels, cy)):
            matches.append(_point(footer, cx, cy, scale))
    return matches[0] if len(matches) == 1 else None


def _submit_point(footer, scale):
    pixels = footer[0]
    search_left, search_top = 198, 6
    search = pixels[search_top:51, search_left:247]
    green = (abs(search - (7, 193, 142)) <= (35, 27, 32)).all(axis=2)
    matches = []
    for x, y, w, h, disk in _components(green):
        if not (22 <= w <= 38 and 22 <= h <= 38 and abs(w - h) <= 5):
            continue
        cx, cy = x + w // 2, y + h // 2
        if not (215 <= cx + search_left <= 231 and 19 <= cy + search_top <= 33 and .56 <= disk.mean() <= .86):
            continue
        # At native 125% scaling the thin white arrow blends with green.
        # Keep the marker inside the disk and require both wings and a shaft.
        patch = search[cy - 8:cy + 9, cx - 8:cx + 9]
        light = (patch >= (65, 204, 154)).all(axis=2)
        if (light[:6, 6:11].any() and light[3:9, 2:7].any()
                and light[3:9, 10:15].any() and light[10:16, 6:11].any()):
            matches.append(_point(footer, cx + search_left, cy + search_top, scale))
    return matches[0] if len(matches) == 1 else None


def locate_recording_controls(image, binding, scale=1.0):
    """Attest the native gray recorder and both clickable controls."""
    footer = _footer(image, binding, scale)
    if footer is None:
        return None
    cancel, submit = _cancel_point(footer, scale), _submit_point(footer, scale)
    return RecordingControls(cancel, submit) if cancel and submit else None


def _header_matches(image, binding, vision):
    lines = read_header(vision.ocr, image.crop(binding.rect('header')), binding)
    return bool(lines and all(line.score >= .92 for line in lines)
                and match_header(''.join(line.text for line in lines), binding))


def _mouse_action(desktop, point, rect, generation, mark_attempted, *, click):
    from .windows import INPUT, K, U, W, input_desktop_available

    hwnd = desktop.calibration.hwnd
    x, y = rect[0] + point[0], rect[1] + point[1]
    origin_x, origin_y = U.GetSystemMetrics(76), U.GetSystemMetrics(77)
    width, height = U.GetSystemMetrics(78), U.GetSystemMetrics(79)
    if not (0 <= x - origin_x < width and 0 <= y - origin_y < height and 0 < width <= 65536
            and 0 < height <= 65536):
        raise DesktopUnavailable('微信录音控件超出可操作桌面')
    window_point = W.POINT(x, y)
    events = (INPUT * (3 if click else 1))()
    events[0].value.mouse.flags = 0xC001  # absolute move on the virtual desktop
    events[0].value.mouse.dx = ((x - origin_x) * 65536 + width - 1) // width
    events[0].value.mouse.dy = ((y - origin_y) * 65536 + height - 1) // height
    if click:
        events[1].value.mouse.flags = 0x0002
        events[2].value.mouse.flags = 0x0004
    if (not input_desktop_available() or desktop.check()['rect'] != rect
            or U.GetAncestor(U.WindowFromPoint(window_point), 2) != hwnd
            or any(U.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4))
            or desktop.input_generation() != generation):
        raise DesktopUnavailable('微信录音控件位置或人工输入已经变化')
    tick = K.GetTickCount64() & 0xffffffff
    if not tick or tick == generation:
        raise DesktopUnavailable('无法区分本次微信操作与最近输入')
    for event in events:
        event.value.mouse.time = tick
    mark_attempted()
    if U.SendInput(len(events), events, ctypes.sizeof(INPUT)) != len(events):
        raise DesktopUnavailable('微信录音控件的鼠标操作结果未知')
    # The batch is consecutive in the input stream. Accept only its own tick.
    for _ in range(10):
        current = desktop.input_generation()
        if current == tick:
            return current
        if current != generation:
            break
        time.sleep(.002)
    raise DesktopUnavailable('微信录音控件操作后无法确认输入归属')


def _click(desktop, point, rect, generation, mark_attempted):
    return _mouse_action(desktop, point, rect, generation, mark_attempted, click=True)


def _park_pointer(desktop, rect, generation):
    binding = desktop.calibration
    point = round(binding.width * .03), round(binding.height * .5)
    return _mouse_action(desktop, point, rect, generation, lambda: None, click=False)


def _binding_identity(binding):
    return (binding.hwnd, binding.pid, binding.width, binding.height, binding.window_kind,
            binding.header_label, binding.target_group, binding.target_kind, binding.ocr_title)


def _binding_key(binding):
    return (_binding_identity(binding), *(binding.rect(region) for region in ('header', 'input', 'send')),
            binding.rect('voice') if 'voice' in binding.regions else None)


def record_wechat_voice(desktop, vision, clip, output_id, *, capture_verified, before_submit=None,
                        check_cancelled=None, idle_seconds=10, prepare_output=prepare_voice_output,
                        click=_click, clock=time.monotonic, sleep=time.sleep, header_matches=_header_matches,
                        client_version=_wechat_client_version, park_pointer=_park_pointer):
    """Attempt one recording. Call before_submit with elapsed recording milliseconds.

    The caller owns conversation authorization, desktop serialization, and any receipt.
    A submit attempt always requires independent confirmation.
    """
    started = None
    opening_owned = False
    submitted = False
    own_generation = None
    key = None

    def task_active():
        if check_cancelled is not None:
            check_cancelled()

    def guard():
        if (desktop.check()['rect'] != rect or desktop.input_generation() != own_generation
                or _binding_key(desktop.calibration) != key):
            raise DesktopUnavailable('微信录音期间检测到窗口、校准或人工输入变化')

    def recording_active():
        task_active()
        guard()
        if clock() - started >= _RECORDING_LIMIT:
            raise DesktopUnavailable('微信录音已到时间上限')

    def snapshot():
        guard()
        image = desktop.capture(adapt=False)
        if not header_matches(image, desktop.calibration, vision):
            raise DesktopUnavailable('微信当前会话标题无法核实')
        guard()
        return image

    def verify_idle_entry():
        nonlocal key
        for attempt in range(3):
            task_active()
            image = desktop.capture()
            frame = desktop.read_bound_frame(image, vision.read_controls, getattr(vision, 'ocr', None))
            current = desktop.calibration
            if _binding_identity(current) != identity:
                raise DesktopUnavailable('准备语音期间切换了校准窗口或会话')
            key = _binding_key(current)
            guard()
            if frame.draft:
                raise DesktopUnavailable('微信输入框已有草稿，已保留输入内容')
            empty = frame.draft_empty
            if frame.safe and frame.at_bottom and not empty:
                painted = desktop.confirm_empty_editor(image, vision)
                if painted is not None:
                    native = vision.read_controls(painted, current)
                    empty = native.safe and native.group == frame.group and native.draft_empty
            detail = frame.reason or '微信会话或空白输入框尚未核实，已保留输入内容'
            if frame.safe and frame.at_bottom and empty:
                entry = locate_voice_entry(image, current, scale)
                if entry and header_matches(image, current, vision):
                    return entry
                detail = ('微信当前会话标题无法核实' if entry else
                          '微信语音按钮与校准区域不符，请重新校准' if 'voice' in current.regions else
                          '未核实微信原生录音入口')
            if attempt < 2:
                sleep(.12)
        raise DesktopUnavailable(detail)

    def mark_opening():
        nonlocal started
        started = clock()

    def mark_submitting():
        nonlocal submitted
        submitted = True

    def find_recording():
        end = clock() + 1.5
        for _ in range(16):
            recording_active()
            image = snapshot()
            controls = locate_recording_controls(image, desktop.calibration, scale)
            if controls is not None:
                return controls
            if clock() >= end:
                break
            sleep(.10)
        raise DesktopUnavailable('未核实微信原生录音控件')

    def cancel_recording():
        nonlocal own_generation
        image = snapshot()
        footer = _footer(image, desktop.calibration, scale)
        cancel = _cancel_point(footer, scale) if footer is not None else None
        if cancel is None:
            return False
        own_generation = click(desktop, cancel, rect, own_generation, lambda: None)
        for _ in range(5):
            image = snapshot()
            if locate_voice_entry(image, desktop.calibration, scale) is not None:
                return True
            sleep(.10)
        return False

    try:
        task_active()
        if (clip.channels not in (1, 2) or not 0 < clip.sample_rate <= 192_000
                or len(clip.pcm) % (clip.channels * 2) or not 0 < clip.duration <= MAX_CLIP_SECONDS):
            raise ValueError(f'微信原生语音需要有效的 PCM，最长 {MAX_CLIP_SECONDS} 秒')
        own_generation = desktop.prepare_send(idle_seconds)
        binding = desktop.calibration
        identity = _binding_identity(binding)
        version = client_version(binding.pid)
        if binding.window_kind not in ('main', 'detached') or not supports_voice_client(version):
            raise DesktopUnavailable('当前微信窗口或客户端版本未通过原生录音检查')
        rect = desktop.check()['rect']
        scale = desktop._dpi_scale()
        with prepare_output(output_id, clip) as playback:
            entry = verify_idle_entry()
            task_active()
            guard()
            own_generation = click(desktop, entry, rect, own_generation, mark_opening)
            opening_owned = True
            find_recording()
            sleep(.16)
            recording_active()
            if capture_verified() is not True:
                raise DesktopUnavailable('未核实微信正在使用选定的录音设备')
            recording_active()
            playback.play(recording_active)
            sleep(max(.18, 1.1 - (clock() - started)))
            recording_active()
        recording_active()
        if capture_verified() is not True:
            raise DesktopUnavailable('提交前未核实微信仍在使用选定的录音设备')
        controls = find_recording()
        if before_submit is not None:
            before_submit(round((clock() - started) * 1000))
            controls = find_recording()
        recording_active()
        own_generation = click(desktop, controls.submit, rect, own_generation, mark_submitting)
        try:
            # SetCursorPos alone can leave the visible pointer highlight over
            # Send. A fresh, identified move clears it before the next reply.
            sleep(.03)
            guard()
            own_generation = park_pointer(desktop, rect, own_generation)
        except (DesktopUnavailable, OSError):
            pass  # Submission already needs its independent database receipt.
        remember = getattr(desktop, 'remember_send_input', None)
        if remember:
            remember(own_generation)
        return VoiceRecordingResult('submit_uncertain', '已点击微信语音发送，正在等待独立回执')
    except Exception as error:
        detail = str(error) or type(error).__name__
        if submitted:
            return VoiceRecordingResult('submit_uncertain', detail)
        if started is None:
            return VoiceRecordingResult('blocked', detail)
        try:
            if opening_owned and cancel_recording():
                return VoiceRecordingResult('cancelled', detail)
        except Exception:
            pass
        return VoiceRecordingResult('recording_left_open', f'{detail}。无法安全确认录音已取消')
