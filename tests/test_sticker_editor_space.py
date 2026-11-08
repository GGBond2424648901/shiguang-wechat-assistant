from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from xiaomao.config import Calibration
from xiaomao.windows import WindowsDesktop
from xiaomao.layout import adapt_calibration, default_calibration_regions
from test_bubble_delivery import delivery
from test_engine import system


def scene(toolbar=514, kind='main'):
    width, height = (1038, 727) if kind == 'main' else (612, 727)
    image = Image.new('RGB', (width, height), (237, 237, 237))
    draw = ImageDraw.Draw(image)
    left = 319 if kind == 'main' else 0
    if left:
        draw.rectangle((68, 15, left - 1, height - 8), fill=(247, 247, 247))
        draw.line((left, 15, left, height - 8), fill=(213, 213, 213))
    draw.line((max(8, left + 1), 100, width - 8, 100), fill=(213, 213, 213))
    draw.line((max(8, left + 1), toolbar, width - 8, toolbar), fill=(213, 213, 213))
    binding = Calibration(1, 1, width, height, '测试', default_calibration_regions(kind), True,
                          target_group='测试', window_kind=kind)
    return image, adapt_calibration(image, binding)


def sticker(tmp_path):
    path = tmp_path / 'test.gif'
    Image.new('RGB', (240, 240), 'orange').save(path)
    return SimpleNamespace(verified_bytes=path.read_bytes)


def test_footer_space_is_not_counted_as_editable_sticker_viewport(tmp_path):
    _, binding = scene()
    desktop = WindowsDesktop(binding)
    desktop._dpi_scale = lambda: 1
    # 101 pixels of editor + blank Send footer used to pass the 70% check.
    assert not desktop.sticker_preview_fits(sticker(tmp_path))


@pytest.mark.parametrize('kind', ['main', 'detached'])
@pytest.mark.parametrize('manual', [False, True])
def test_editor_expands_before_paste_and_rebinds_live_geometry(tmp_path, kind, manual, monkeypatch):
    from xiaomao import vision
    image, binding = scene(kind=kind)
    desktop = WindowsDesktop(replace(binding, manual_regions=manual))
    desktop._dpi_scale = lambda: 1
    desktop.capture = lambda: image
    desktop.check = lambda: {'rect': (0, 0, binding.width, binding.height)}
    desktop.validate_target = lambda frame: None
    desktop.input_generation = lambda: 7
    desktop.idle_for = lambda: 60
    monkeypatch.setattr(vision, 'calibration_send_matches', lambda *args: True)
    desktop._sidebar_ocr = object()
    drags = []

    def drag(start, end, generation):
        nonlocal image
        drags.append((start, end, generation))
        image, _ = scene(toolbar=end[1], kind=kind)
        return 7

    desktop._drag_editor_separator = drag
    assert desktop.ensure_sticker_preview_space(sticker(tmp_path), 10)
    assert len(drags) == 1 and drags[0][1][1] < drags[0][0][1]
    assert desktop.sticker_preview_fits(sticker(tmp_path))
    assert desktop.calibration.rect('input')[1] < binding.rect('input')[1]
    assert binding.rect('input')[1] == 564  # persisted calibration is untouched


def test_ambiguous_separator_never_drags_or_accepts_clipped_preview(tmp_path):
    _, binding = scene()
    desktop = WindowsDesktop(binding)
    desktop._dpi_scale = lambda: 1
    desktop.capture = lambda: Image.new('RGB', (1038, 727), 'white')
    desktop.input_generation = lambda: 7
    desktop.idle_for = lambda: 60
    desktop._drag_editor_separator = lambda *args: pytest.fail('cannot guess a splitter')
    assert not desktop.ensure_sticker_preview_space(sticker(tmp_path), 10)


def test_sticker_waits_without_pasting_when_editor_cannot_expand(delivery, tmp_path):
    from test_stickers import library
    from xiaomao.stickers import StickerLibrary
    from xiaomao.domain import ReplyTask
    engine, feed, _, clock, store = delivery
    library(tmp_path)
    lib = StickerLibrary(tmp_path)
    prepared = lib.prepare(lib.items[0])
    task = ReplyTask(store.create('small-editor', clock()), clock(), clock(), '',
                     state='generated', reply=prepared.description, sticker=prepared)
    engine.tasks.append(task)
    engine.desktop.ensure_sticker_preview_space = lambda *args: None
    engine.desktop.type_sticker = lambda *args: pytest.fail('must expand before paste')
    engine.observe_once()
    assert task.state == 'generated' and task.wait_reason == 'sticker_space'
    assert engine.mode == 'auto' and not feed.sent


def test_resumed_partial_sticker_expands_then_proves_original_without_repasting(delivery, tmp_path):
    from test_sticker_interruption import interrupted
    engine, feed, clock, store, task, pastes = interrupted(delivery, tmp_path)
    checks = []
    def expand(*args):
        checks.append('expand')
        return engine.desktop.input_generation()
    engine.desktop.ensure_sticker_preview_space = expand
    engine.desktop.verify_owned_sticker_input = lambda item: checks == ['expand']
    clock.advance(4)
    engine.observe_once()
    assert checks == ['expand'] and len(pastes) == 1
    assert feed.sent == ['[表情]'] and store.get(task.id)['state'] == 'verified'


def test_manual_input_during_expansion_cannot_become_an_accepted_generation(tmp_path, monkeypatch):
    from xiaomao import vision
    image, binding = scene()
    desktop = WindowsDesktop(binding)
    desktop._dpi_scale = lambda: 1
    desktop.idle_for = lambda: 60
    generation = 7
    desktop.input_generation = lambda: generation
    desktop.capture = lambda: image
    desktop.check = lambda: {'rect': (0, 0, binding.width, binding.height)}
    desktop.validate_target = lambda frame: None
    desktop._sidebar_ocr = object()
    monkeypatch.setattr(vision, 'calibration_send_matches', lambda *args: True)
    def interrupted_drag(*args):
        nonlocal generation
        generation = 8  # user activity after our drag, before the recheck
        return 7
    desktop._drag_editor_separator = interrupted_drag
    assert desktop.ensure_sticker_preview_space(sticker(tmp_path), 10) is None
