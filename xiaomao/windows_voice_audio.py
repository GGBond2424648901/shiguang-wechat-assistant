"""Play PCM on a specific Windows audio endpoint without changing the default."""

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from functools import lru_cache
import os
from time import monotonic, sleep

from .voice_audio import VoiceAudio, make_voice_probe, matches_voice_probe


# Windows SDK mmddk.h. Query IDs on waveform indices, never parse the returned IDs.
_QUERY_ENDPOINT_ID = 0x0811
_QUERY_ENDPOINT_ID_SIZE = 0x0812
_HEADER_DONE = 1
_SHORT = ctypes.c_uint16
_UINT = ctypes.c_uint32
_PTR = ctypes.c_void_p
_UINT_PTR = ctypes.c_size_t


class VoiceOutputError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioEndpoint:
    id: str
    name: str


class _WaveFormat(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('wFormatTag', _SHORT), ('nChannels', _SHORT), ('nSamplesPerSec', _UINT),
        ('nAvgBytesPerSec', _UINT), ('nBlockAlign', _SHORT), ('wBitsPerSample', _SHORT),
        ('cbSize', _SHORT),
    ]


class _WaveHeader(ctypes.Structure):
    _fields_ = [
        ('lpData', _PTR), ('dwBufferLength', _UINT), ('dwBytesRecorded', _UINT),
        ('dwUser', _UINT_PTR), ('dwFlags', _UINT), ('dwLoops', _UINT),
        ('lpNext', _PTR), ('reserved', _UINT_PTR),
    ]


class _InputCaps(ctypes.Structure):
    _fields_ = [
        ('wMid', _SHORT), ('wPid', _SHORT), ('vDriverVersion', _UINT),
        ('szPname', ctypes.c_wchar * 32), ('dwFormats', _UINT),
        ('wChannels', _SHORT), ('wReserved1', _SHORT),
    ]


class _OutputCaps(_InputCaps):
    _fields_ = [('dwSupport', _UINT)]


@lru_cache(maxsize=1)
def _winmm():
    if os.name != 'nt':
        raise VoiceOutputError('微信录音仅支持 Windows')
    api = ctypes.WinDLL('winmm.dll')
    for prefix, capabilities in (('waveOut', _OutputCaps), ('waveIn', _InputCaps)):
        for suffix, types in (
            ('GetNumDevs', []),
            ('GetDevCapsW', [_UINT_PTR, ctypes.POINTER(capabilities), _UINT]),
            ('Message', [_PTR, _UINT, _PTR, _UINT_PTR]),
        ):
            function = getattr(api, prefix + suffix)
            function.argtypes = types
            function.restype = _UINT
    for function in (api.waveOutOpen, api.waveInOpen):
        function.argtypes = [
            ctypes.POINTER(_PTR), _UINT, ctypes.POINTER(_WaveFormat), _UINT_PTR, _UINT_PTR, _UINT,
        ]
        function.restype = _UINT
    for function in (api.waveOutPrepareHeader, api.waveOutWrite, api.waveOutUnprepareHeader,
                     api.waveInPrepareHeader, api.waveInAddBuffer, api.waveInUnprepareHeader):
        function.argtypes = [_PTR, ctypes.POINTER(_WaveHeader), _UINT]
        function.restype = _UINT
    for function in (api.waveOutReset, api.waveOutClose, api.waveInStart, api.waveInReset, api.waveInClose):
        function.argtypes = [_PTR]
        function.restype = _UINT
    return api


def _endpoint_id(message, index):
    size = _UINT()
    if message(index, _QUERY_ENDPOINT_ID_SIZE, ctypes.byref(size), 0) != 0:
        return ''
    width = ctypes.sizeof(ctypes.c_wchar)
    if not width < size.value <= 4096 or size.value % width:
        return ''
    value = ctypes.create_unicode_buffer(size.value // width)
    if message(index, _QUERY_ENDPOINT_ID, value, size.value) != 0:
        return ''
    return value.value


def _indexed_endpoints(api, capture):
    count, capabilities, message, layout = (
        (api.waveInGetNumDevs, api.waveInGetDevCapsW, api.waveInMessage, _InputCaps) if capture else
        (api.waveOutGetNumDevs, api.waveOutGetDevCapsW, api.waveOutMessage, _OutputCaps)
    )
    for index in range(count()):
        caps = layout()
        if capabilities(index, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            endpoint_id = _endpoint_id(message, index)
            if endpoint_id:
                yield index, AudioEndpoint(endpoint_id, caps.szPname)


def list_audio_endpoints(*, capture=False) -> tuple[AudioEndpoint, ...]:
    return tuple(endpoint for _, endpoint in _indexed_endpoints(_winmm(), capture))


def _check(result, operation, *, capture=False):
    if result:
        device = '输入' if capture else '输出'
        raise VoiceOutputError(f'无法{operation} Windows 语音{device}设备（{result}）')


def _choose_device(api, endpoint_id, *, capture=False):
    selected = endpoint_id.casefold()
    for index, endpoint in _indexed_endpoints(api, capture):
        if endpoint.id.casefold() == selected:
            return index
    device = '输入' if capture else '输出'
    raise VoiceOutputError(f'找不到选定的语音{device}设备')


def _pcm_format(channels, rate):
    return _WaveFormat(1, channels, rate, rate * channels * 2, channels * 2, 16, 0)


# Keep buffers alive if a broken driver still owns them after reset and close.
_stuck_buffers = []


class _PreparedOutput:
    def __init__(self, api, handle, clip):
        self._api = api
        self._handle = handle
        self._clip_duration = clip.duration
        self._buffer = ctypes.create_string_buffer(clip.pcm, len(clip.pcm))
        self._header = _WaveHeader(ctypes.addressof(self._buffer), len(clip.pcm))
        self._size = ctypes.sizeof(self._header)
        self._started = False
        self._closed = False
        _check(api.waveOutPrepareHeader(handle, ctypes.byref(self._header), self._size), '准备')

    def play(self, check_active=lambda: None):
        """Play once. Call a lightweight cancellation guard throughout the recording."""
        if self._started or self._closed:
            raise VoiceOutputError('此语音播放任务已使用')
        check_active()
        end = monotonic() + self._clip_duration + 2
        _check(self._api.waveOutWrite(self._handle, ctypes.byref(self._header), self._size), '播放')
        self._started = True
        while not self._header.dwFlags & _HEADER_DONE:
            check_active()
            if monotonic() >= end:
                raise VoiceOutputError('Windows 语音输出超时')
            sleep(.02)
        check_active()

    def close(self):
        self._closed = True
        api, handle = self._api, self._handle
        reset = api.waveOutReset(handle) if self._started and not self._header.dwFlags & _HEADER_DONE else 0
        unprepare = api.waveOutUnprepareHeader(handle, ctypes.byref(self._header), self._size)
        close = api.waveOutClose(handle)
        if unprepare and close:
            _stuck_buffers.append(self)
        _check(reset or unprepare or close, '释放')


@contextmanager
def prepare_voice_output(endpoint_id: str, clip: VoiceAudio):
    """Open the configured output and allocate the buffer before recording starts."""
    if _stuck_buffers:
        raise VoiceOutputError('Windows 音频驱动未释放缓冲，请重新启动拾光')
    api = _winmm()
    endpoint = _choose_device(api, endpoint_id)
    fmt = _pcm_format(clip.channels, clip.sample_rate)
    handle = _PTR()
    _check(api.waveOutOpen(ctypes.byref(handle), endpoint, ctypes.byref(fmt), 0, 0, 0), '打开')
    try:
        if _endpoint_id(api.waveOutMessage, endpoint).casefold() != endpoint_id.casefold():
            raise VoiceOutputError('已选语音输出设备发生变化')
        output = _PreparedOutput(api, handle, clip)
    except BaseException:
        api.waveOutClose(handle)
        raise
    try:
        yield output
    finally:
        output.close()


def _record_voice_probe(api, playback, capture_id, clip, check_active):
    index = _choose_device(api, capture_id, capture=True)
    fmt = _pcm_format(1, clip.sample_rate)
    buffer = ctypes.create_string_buffer(round((clip.duration + 1) * clip.sample_rate) * 2)
    header = _WaveHeader(ctypes.addressof(buffer), len(buffer))
    header_size = ctypes.sizeof(header)
    handle = _PTR()
    _check(api.waveInOpen(ctypes.byref(handle), index, ctypes.byref(fmt), 0, 0, 0),
           '打开', capture=True)
    prepared = queued = False
    try:
        if _endpoint_id(api.waveInMessage, index).casefold() != capture_id.casefold():
            raise VoiceOutputError('已选语音输入设备发生变化')
        check_active()
        _check(api.waveInPrepareHeader(handle, ctypes.byref(header), header_size), '准备', capture=True)
        prepared = True
        _check(api.waveInAddBuffer(handle, ctypes.byref(header), header_size), '准备', capture=True)
        queued = True
        _check(api.waveInStart(handle), '启动', capture=True)
        sleep(.12)
        playback.play(check_active)
        sleep(.18)
        check_active()
    finally:
        reset = api.waveInReset(handle) if queued else 0
        unprepare = api.waveInUnprepareHeader(handle, ctypes.byref(header), header_size) if prepared else 0
        close = api.waveInClose(handle)
        if unprepare and close:
            _stuck_buffers.append((handle, header, buffer))
        _check(reset or unprepare or close, '释放', capture=True)
    return buffer[:header.dwBytesRecorded]


def probe_voice_route(output_id, capture_id, *, check_active=lambda: None) -> bool:
    """Test two user-selected endpoints with a local signal. Captured PCM stays in memory."""
    clip = make_voice_probe()
    with prepare_voice_output(output_id, clip) as playback:
        recording = _record_voice_probe(_winmm(), playback, capture_id, clip, check_active)
    return matches_voice_probe(recording, clip.sample_rate)
