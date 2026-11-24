import ctypes

import pytest

from xiaomao.voice_audio import make_voice_probe
from xiaomao.windows_voice_audio import (
    VoiceOutputError, list_audio_endpoints, prepare_voice_output, probe_voice_route,
)


class WaveDriver:
    def __init__(self):
        self.endpoints = ('only-first', 'only-second')
        self.events = []
        self.header = None
        self.done_after_write = True
        self.prepare_error = 0
        self.recording = None
        self.connect_loopback = True

    def waveOutGetNumDevs(self):
        return len(self.endpoints)

    def waveInGetNumDevs(self):
        return 1

    def waveOutGetDevCapsW(self, index, caps, size):
        caps._obj.szPname = '同名的输出设备'
        return 0

    def waveInGetDevCapsW(self, index, caps, size):
        caps._obj.szPname = '虚拟麦克风'
        return 0

    def endpoint_message(self, endpoint_id, message, target, size):
        value = ctypes.create_unicode_buffer(endpoint_id)
        if message == 0x0812:
            target._obj.value = ctypes.sizeof(value)
        elif message == 0x0811:
            assert size == ctypes.sizeof(value)
            target.value = endpoint_id
        else:
            pytest.fail(f'unexpected message {message}')
        return 0

    def waveOutMessage(self, index, message, target, size):
        return self.endpoint_message(self.endpoints[index], message, target, size)

    def waveInMessage(self, index, message, target, size):
        return self.endpoint_message('capture', message, target, size)

    def waveOutOpen(self, handle, index, fmt, *args):
        handle._obj.value = 54
        self.events.append(('open', index, fmt._obj.nSamplesPerSec, fmt._obj.nChannels))
        return 0

    def waveOutPrepareHeader(self, handle, header, size):
        self.events.append('prepare')
        self.header = header._obj
        return self.prepare_error

    def waveOutWrite(self, handle, header, size):
        assert header._obj.dwBufferLength > 0
        self.events.append('write')
        if self.recording is not None and self.connect_loopback:
            count = header._obj.dwBufferLength
            ctypes.memmove(self.recording.lpData, header._obj.lpData, count)
            self.recording.dwBytesRecorded = count
        if self.done_after_write:
            header._obj.dwFlags |= 1
        return 0

    def waveOutReset(self, handle):
        self.events.append('reset')
        self.header.dwFlags |= 1
        return 0

    def waveOutUnprepareHeader(self, handle, header, size):
        self.events.append('unprepare')
        return 0

    def waveOutClose(self, handle):
        self.events.append('close')
        return 0

    def waveInOpen(self, handle, index, fmt, *args):
        handle._obj.value = 37
        self.events.append(('open-input', index, fmt._obj.nSamplesPerSec, fmt._obj.nChannels))
        return 0

    def waveInPrepareHeader(self, handle, header, size):
        self.events.append('prepare-input')
        return 0

    def waveInAddBuffer(self, handle, header, size):
        self.events.append('queue-input')
        self.recording = header._obj
        return 0

    def waveInStart(self, handle):
        self.events.append('start-input')
        return 0

    def waveInReset(self, handle):
        self.events.append('reset-input')
        return 0

    def waveInUnprepareHeader(self, handle, header, size):
        self.events.append('unprepare-input')
        self.recording = None
        return 0

    def waveInClose(self, handle):
        self.events.append('close-input')
        return 0


@pytest.fixture
def driver(monkeypatch):
    api = WaveDriver()
    monkeypatch.setattr('xiaomao.windows_voice_audio._winmm', lambda: api)
    return api


def test_endpoint_ids_distinguish_devices_with_the_same_display_name(driver):
    assert [(x.id, x.name) for x in list_audio_endpoints()] == [
        ('only-first', '同名的输出设备'), ('only-second', '同名的输出设备')]
    capture, = list_audio_endpoints(capture=True)
    assert (capture.id, capture.name) == ('capture', '虚拟麦克风')


def test_prepared_wav_only_plays_through_the_selected_output(driver):
    clip = make_voice_probe()
    driver.endpoints = tuple(reversed(driver.endpoints))
    with prepare_voice_output('ONLY-SECOND', clip) as playback:
        assert driver.events == [('open', 0, clip.sample_rate, 1), 'prepare']
        assert ctypes.string_at(driver.header.lpData, driver.header.dwBufferLength) == clip.pcm
        playback.play()
    assert driver.events == [('open', 0, clip.sample_rate, 1), 'prepare', 'write', 'unprepare', 'close']


def test_playback_aborts_and_releases_device_when_window_guard_fails(driver):
    driver.done_after_write = False
    calls = 0

    def check_window():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('window changed')

    with pytest.raises(RuntimeError, match='window changed'):
        with prepare_voice_output('only-first', make_voice_probe()) as playback:
            playback.play(check_window)
    assert driver.events[-4:] == ['write', 'reset', 'unprepare', 'close']


def test_playback_timeout_releases_device(driver, monkeypatch):
    driver.done_after_write = False
    values = iter((0, 300))
    monkeypatch.setattr('xiaomao.windows_voice_audio.monotonic', lambda: next(values))
    with pytest.raises(VoiceOutputError, match='超时'):
        with prepare_voice_output('only-first', make_voice_probe()) as playback:
            playback.play()
    assert driver.events[-4:] == ['write', 'reset', 'unprepare', 'close']


def test_missing_output_never_falls_back_to_windows_default(driver):
    with pytest.raises(VoiceOutputError, match='找不到'):
        with prepare_voice_output('missing', make_voice_probe()):
            pytest.fail('opened a different device')
    assert not driver.events


def test_prepare_failure_closes_handle_without_starting_output(driver):
    driver.prepare_error = 7
    with pytest.raises(VoiceOutputError, match='准备'):
        with prepare_voice_output('only-first', make_voice_probe()):
            pytest.fail('prepared a broken device')
    assert driver.events[-2:] == ['prepare', 'close']


@pytest.mark.parametrize('connected', (True, False))
def test_probe_detects_a_physically_looped_route_and_does_not_guess_by_name(driver, monkeypatch, connected):
    driver.connect_loopback = connected
    monkeypatch.setattr('xiaomao.windows_voice_audio.sleep', lambda seconds: None)
    assert probe_voice_route('only-second', 'capture') is connected
    assert driver.events.index('start-input') < driver.events.index('write')
    assert driver.events.count('reset-input') == 1
    assert 'unprepare-input' in driver.events and 'close-input' in driver.events
    assert driver.recording is None
