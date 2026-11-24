from contextlib import contextmanager
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from xiaomao.config import Calibration
from xiaomao.voice_audio import VoiceAudio
from xiaomao.windows import DesktopUnavailable


def binding(scale=1):
    return Calibration(100, 300, round(882 * scale), round(641 * scale), '文件传输助手', {
        'header': [.34, .028, .76, .08], 'messages': [.33, .10, .98, .74],
        'input': [.36, .80, .95, .90], 'send': [.86, .91, .98, .97],
    }, target_group='文件传输助手', target_kind='private')


def composer(*, recording=False, variant='', scale=1):
    image = Image.new('RGB', (882, 641), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((290, 474, 867, 627), fill=(247, 247, 247), outline=(209, 209, 209))
    if recording:
        draw.rounded_rectangle((657, 584, 855, 620), radius=18, fill=(236, 236, 236))
        draw.ellipse((619, 584, 655, 620), fill=(227, 227, 227))
        if variant != 'missing_cancel_mark':
            draw.line((632, 597, 642, 607), fill=(101, 101, 101), width=2)
            draw.line((642, 597, 632, 607), fill=(101, 101, 101), width=2)
        if variant != 'missing_send':
            left, right = (818, 856) if variant == 'square_send' else (821, 855)
            shape = draw.rectangle if variant == 'square_send' else draw.ellipse
            shape((left, 585, right, 619), fill=(7, 193, 142))
            if variant != 'missing_arrow':
                draw.line((833, 600, 838, 595, 843, 600), fill='white', width=2)
                draw.line((838, 596, 838, 609), fill='white', width=2)
    else:
        draw.rounded_rectangle((800, 589, 866, 621), radius=5, fill=(235, 235, 235))
        draw.ellipse((777, 597, 793, 613), outline=(88, 88, 88), width=1)
        if variant != 'hollow_ring':
            draw.line((781, 605, 783, 605, 784, 601, 786, 609, 788, 605, 789, 605),
                      fill=(88, 88, 88), width=1)
    if scale != 1:
        return image.resize((round(882 * scale), round(641 * scale)), Image.Resampling.BICUBIC)
    return image


def test_native_voice_module_exposes_guarded_adapter():
    import importlib.util

    assert importlib.util.find_spec('xiaomao.windows_wechat_voice') is not None


@pytest.mark.parametrize('scale', (1, 1.25))
def test_locates_recorder_only_in_calibrated_footer(scale):
    from xiaomao.windows_wechat_voice import locate_recording_controls, locate_voice_entry

    idle = composer(scale=scale)
    active = composer(recording=True, scale=scale)
    calibrated = binding(scale)
    assert locate_voice_entry(idle, calibrated, scale) == pytest.approx((785 * scale, 605 * scale), abs=2)
    assert locate_voice_entry(active, calibrated, scale) is None
    assert locate_recording_controls(idle, calibrated, scale) is None
    controls = locate_recording_controls(active, calibrated, scale)
    assert controls.cancel == pytest.approx((637 * scale, 602 * scale), abs=2)
    assert controls.submit == pytest.approx((838 * scale, 602 * scale), abs=2)


@pytest.mark.parametrize('variant', ('missing_send', 'square_send', 'missing_arrow', 'missing_cancel_mark'))
def test_incomplete_recording_controls_are_never_clickable(variant):
    from xiaomao.windows_wechat_voice import locate_recording_controls

    assert locate_recording_controls(composer(recording=True, variant=variant), binding(), 1) is None


def test_hollow_ring_and_misaligned_calibration_do_not_open_recording():
    from xiaomao.windows_wechat_voice import locate_voice_entry

    assert locate_voice_entry(composer(variant='hollow_ring'), binding(), 1) is None
    calibrated = binding()
    calibrated.regions['send'] = [.60, .91, .75, .97]
    assert locate_voice_entry(composer(), calibrated, 1) is None


def test_observed_display_dpi_footer_with_faint_arrow_and_pointer_halo():
    from xiaomao.windows_wechat_voice import locate_recording_controls, locate_voice_entry

    calibrated = binding()
    calibrated.width, calibrated.height = 1118, 809
    image = Image.new('RGB', (1118, 809), (250, 250, 250))
    idle = Image.new('RGB', (251, 58), (250, 250, 250))
    draw = ImageDraw.Draw(idle)
    draw.ellipse((157, 17, 173, 33), outline=(109, 109, 109), width=1)
    draw.arc((162, 21, 169, 29), -95, 95, fill=(115, 115, 115), width=1)
    draw.line((162, 25, 163, 25), fill=(115, 115, 115), width=1)
    draw.rounded_rectangle((189, 12, 238, 39), radius=6, fill=(237, 237, 237))
    image.paste(idle.resize((314, 72), Image.Resampling.BICUBIC), (782, 725))
    assert locate_voice_entry(image, calibrated, 1.25) == pytest.approx((990, 756), abs=2)

    active = Image.new('RGB', (251, 58), (250, 250, 250))
    draw = ImageDraw.Draw(active)
    draw.rounded_rectangle((37, 4, 233, 39), radius=18, fill=(237, 237, 237))
    draw.ellipse((4, 9, 30, 35), fill=(237, 237, 237))
    draw.line((13, 18, 21, 26), fill=(122, 122, 122), width=2)
    draw.line((21, 18, 13, 26), fill=(122, 122, 122), width=2)
    draw.ellipse((145, 13, 202, 47), fill=(172, 212, 249))
    draw.polygon([(164, 23), (179, 29), (174, 32), (171, 42)], fill=(35, 33, 32))
    draw.ellipse((207, 10, 231, 34), fill=(0, 194, 119))
    draw.line((215, 21, 219, 17, 223, 21), fill=(130, 221, 190), width=2)
    draw.line((219, 17, 219, 27), fill=(130, 221, 190), width=2)
    image.paste(active.resize((314, 72), Image.Resampling.BICUBIC), (782, 725))
    assert locate_voice_entry(image, calibrated, 1.25) is None
    controls = locate_recording_controls(image, calibrated, 1.25)
    assert controls.cancel == pytest.approx((803, 753), abs=2)
    assert controls.submit == pytest.approx((1056, 753), abs=2)


class Desk:
    def __init__(self):
        self.calibration = binding()
        self.rect = (50, 80, 932, 721)
        self.generation = 100
        self.recording = False
        self.foreground = True
        self.target_matches = True
        self.post_click_error = False
        self.pre_submit_error = False
        self.draft = ''
        self.controls = ''
        self.elapsed = 0
        self.events = []
        self.vision = SimpleNamespace(read_controls=self.read_controls)

    def check(self):
        if not self.foreground:
            raise DesktopUnavailable('微信已经离开前台')
        return {'pid': 300, 'width': 882, 'height': 641, 'rect': self.rect}

    def _dpi_scale(self):
        return 1

    def prepare_send(self, idle_seconds):
        self.check()
        return self.generation

    def input_generation(self):
        return self.generation

    def capture(self, *, adapt=True):
        return composer(recording=self.recording, variant=self.controls)

    def read_controls(self, image, calibrated):
        from xiaomao.domain import Frame
        return Frame('示例会话', safe=self.target_matches, draft=self.draft, draft_empty=not self.draft)

    def read_bound_frame(self, image, reader, ocr):
        return reader(image, self.calibration)

    def header_matches(self, image, calibration, vision):
        return self.target_matches

    def click(self, desktop, point, rect, generation, mark_attempted):
        assert desktop is self and rect == self.rect and generation == self.generation
        x, _ = point
        if self.recording and x > 800 and self.pre_submit_error:
            raise DesktopUnavailable('提交前定位发生变化')
        mark_attempted()
        self.generation += 1
        if not self.recording:
            assert 780 <= x <= 790
            self.events.append('open')
            self.recording = True
        elif x < 650:
            self.events.append('cancel')
            self.recording = False
        else:
            self.events.append('submit')
            self.recording = False
            if self.post_click_error:
                raise DesktopUnavailable('点击结果未知')
        return self.generation

    def wait(self, seconds):
        self.elapsed += seconds

    @contextmanager
    def prepare_audio(self, output, clip):
        assert output == 'cable'
        self.events.append('prepare')
        try:
            yield SimpleNamespace(play=self.play)
        finally:
            self.events.append('release')

    def play(self, check_active):
        check_active()
        self.events.append('play')
        self.elapsed += 1.6
        check_active()


def record(desk, **overrides):
    from xiaomao.windows_wechat_voice import record_wechat_voice

    options = dict(capture_verified=lambda: True, click=desk.click, sleep=desk.wait,
                   clock=lambda: desk.elapsed, prepare_output=desk.prepare_audio,
                   header_matches=desk.header_matches, client_version=lambda _: (4, 2, 0, 13),
                   park_pointer=lambda desktop, rect, generation: generation)
    options.update(overrides)
    clip = options.pop('clip', VoiceAudio(bytes(76_800), 24_000, 1))
    return record_wechat_voice(desk, desk.vision, clip, 'cable', **options)


def test_verified_submit_moves_only_its_own_pointer_out_of_the_footer():
    desk = Desk()
    remembered = []
    desk.remember_send_input = remembered.append

    def park(desktop, rect, generation):
        assert desktop is desk and rect == desk.rect and generation == desk.generation
        assert desk.events[-1] == 'submit' and not desk.recording
        desk.events.append('pointer-away')
        desk.generation += 1
        return desk.generation

    result = record(desk, park_pointer=park)
    assert result.state == 'submit_uncertain'
    assert desk.events[-2:] == ['submit', 'pointer-away']
    assert remembered == [desk.generation]


def test_recording_reuses_detached_send_recovery_and_holds_the_verified_geometry():
    from dataclasses import replace
    from xiaomao.domain import Frame

    desk = Desk()
    corrected = replace(desk.calibration, window_kind='detached')
    coarse = replace(corrected, regions={**corrected.regions, 'send': [.8, .89, .98, .98]})
    desk.calibration = coarse
    capture = desk.capture

    def capture_with_layout(*, adapt=True):
        if adapt:
            desk.calibration = coarse
        return capture()

    def read(image, selected):
        return Frame('示例会话', safe=selected is corrected)

    def read_bound(image, reader, ocr):
        desk.calibration = corrected
        return reader(image, corrected)

    desk.capture = capture_with_layout
    desk.vision.read_controls = read
    desk.read_bound_frame = read_bound

    assert record(desk).state == 'submit_uncertain'
    assert desk.calibration is corrected and desk.events.count('submit') == 1


def test_recording_resamples_an_unstable_idle_frame_before_clicking():
    from xiaomao.domain import Frame

    desk = Desk()
    readings = iter((False, True))
    desk.vision.read_controls = lambda image, selected: Frame('示例会话', safe=next(readings))
    assert record(desk).state == 'submit_uncertain'
    assert desk.events.count('open') == desk.events.count('submit') == 1

@pytest.mark.parametrize('post_click_error', (False, True))
@pytest.mark.parametrize('window_kind', ('main', 'detached'))
def test_submit_remains_uncertain_and_is_attempted_once(post_click_error, window_kind):
    desk = Desk()
    desk.calibration.window_kind = window_kind
    desk.post_click_error = post_click_error
    desk.events.clear()

    def verify_capture():
        assert desk.recording
        desk.events.append('capture')
        return True

    def before_submit(elapsed_ms):
        assert 1600 <= elapsed_ms < 2300
        desk.events.append('journal')

    result = record(desk, capture_verified=verify_capture, before_submit=before_submit)

    assert result.state == 'submit_uncertain'
    assert result.detail
    assert desk.events == ['prepare', 'open', 'capture', 'play', 'release', 'capture', 'journal', 'submit']


@pytest.mark.parametrize('fail_on', (1, 2))
def test_current_capture_device_proof_is_required_before_play_and_submit(fail_on):
    desk = Desk()
    calls = 0

    def verification():
        nonlocal calls
        calls += 1
        return calls != fail_on

    result = record(desk, capture_verified=verification)

    assert result.state == 'cancelled'
    assert not desk.recording
    assert ('play' in desk.events) == (fail_on == 2)
    assert desk.events[-1] == 'cancel'
    assert 'submit' not in desk.events


@pytest.mark.parametrize('fault', ('foreground', 'generation', 'header', 'rect'))
def test_changed_window_or_input_does_not_click_cancel_or_submit(fault):
    desk = Desk()

    def playback(check_active):
        check_active()
        if fault == 'foreground':
            desk.foreground = False
        elif fault == 'generation':
            desk.generation += 1
        elif fault == 'header':
            desk.target_matches = False
        else:
            desk.rect = (51, 81, 933, 722)
        check_active()

    desk.play = playback
    result = record(desk)

    assert result.state == 'recording_left_open'
    assert desk.events == ['prepare', 'open', 'release']
    assert desk.recording


def test_pre_click_failure_after_journaling_cancels_owned_recording():
    desk = Desk()
    desk.pre_submit_error = True

    result = record(desk, before_submit=lambda _: desk.events.append('journal'))

    assert result.state == 'cancelled'
    assert desk.events[-2:] == ['journal', 'cancel']


@pytest.mark.parametrize('fault', ('draft', 'version', 'prepare', 'too_long'))
def test_preflight_leaves_user_draft_and_recorder_untouched(fault):
    desk = Desk()
    options = {}
    if fault == 'draft':
        desk.draft = 'manual draft'
    elif fault == 'version':
        options['client_version'] = lambda _: (4, 1, 7, 59)
    elif fault == 'too_long':
        options['clip'] = VoiceAudio(bytes(2 * 24_000 * 56), 24_000, 1)
    else:
        @contextmanager
        def fail(*args):
            raise RuntimeError('cannot open selected endpoint')
            yield

        options['prepare_output'] = fail

    result = record(desk, **options)

    assert result.state == 'blocked'
    assert not desk.recording
    assert 'open' not in desk.events


def test_task_cancel_during_playback_releases_audio_then_cancels_recorder():
    desk = Desk()
    cancelled = False

    def check_cancelled():
        if cancelled:
            raise RuntimeError('user cancelled')

    def playback(check_active):
        nonlocal cancelled
        check_active()
        cancelled = True
        check_active()

    desk.play = playback
    assert record(desk, check_cancelled=check_cancelled).state == 'cancelled'
    assert desk.events[-2:] == ['release', 'cancel']


def test_elapsed_deadline_rejects_overlong_recording_even_for_a_short_clip():
    desk = Desk()

    def playback(check_active):
        desk.elapsed += 56
        check_active()

    desk.play = playback
    assert record(desk).state == 'cancelled'
    assert desk.events[-1] == 'cancel'


def test_binding_change_while_preparing_audio_stops_before_a_click():
    desk = Desk()

    @contextmanager
    def prepare(output, clip):
        with desk.prepare_audio(output, clip) as playback:
            desk.calibration.target_group = 'different chat'
            yield playback

    assert record(desk, prepare_output=prepare).state == 'blocked'
    assert desk.events == ['prepare', 'release']


def test_verified_cancel_can_close_our_recorder_after_send_disk_disappears():
    desk = Desk()

    def playback(check_active):
        desk.controls = 'missing_send'
        raise RuntimeError('selected endpoint failed')

    desk.play = playback
    assert record(desk).state == 'cancelled'
    assert desk.events[-1] == 'cancel'


def test_unattributed_opening_click_never_cancels_recording_using_an_old_cookie():
    desk = Desk()

    def click(*args):
        desk.click(*args)
        raise DesktopUnavailable('click ownership could not be verified')

    assert record(desk, click=click).state == 'recording_left_open'
    assert desk.events == ['prepare', 'open', 'release']


def test_reviewed_voice_box_must_contain_the_fresh_icon_before_opening():
    desk = Desk()
    desk.calibration.regions['voice'] = [690 / 882, 593 / 641, 718 / 882, 618 / 641]
    assert record(desk).state == 'blocked'
    assert desk.events == ['prepare', 'release']

    desk.calibration.regions['voice'] = [772 / 882, 592 / 641, 797 / 882, 618 / 641]
    assert record(desk).state == 'submit_uncertain'
