"""Bounded PCM input for WeChat recording and local audio diagnostics."""

from dataclasses import dataclass
import struct
import wave


MAX_VOICE_SECONDS = 60
_PROBE_FREQUENCIES = (440, 660, 880)
_PROBE_TONE_SECONDS = .5
_PROBE_GAP_SECONDS = .1


@dataclass(frozen=True, slots=True)
class VoiceAudio:
    pcm: bytes
    sample_rate: int
    channels: int

    @property
    def duration(self) -> float:
        return len(self.pcm) / (2 * self.channels * self.sample_rate)


def read_voice_wav(path, *, max_seconds=MAX_VOICE_SECONDS) -> VoiceAudio:
    """Read up to one recording of mono or stereo integer PCM from a WAV."""
    try:
        with wave.open(str(path), 'rb') as wav:
            channels, width, rate, frames, _, _ = wav.getparams()
            if channels not in (1, 2) or width not in (1, 2, 3, 4) or not 0 < rate <= 192_000:
                raise ValueError('WAV 需要使用单声道或立体声 PCM，采样率须在 192 kHz 以内')
            if not frames:
                raise ValueError('WAV 音频为空')
            if frames > rate * max_seconds:
                raise ValueError(f'WAV 音频超过 {max_seconds:g} 秒')
            data = wav.readframes(frames + 1)
    except (EOFError, OSError, struct.error, wave.Error) as error:
        raise ValueError('无法读取 PCM WAV 音频') from error
    if len(data) != frames * channels * width:
        raise ValueError('WAV 音频数据不完整')
    if width != 2:
        import numpy as np

        samples = np.frombuffer(data, dtype=np.uint8)
        if width == 1:
            data = ((samples.astype(np.int16) - 128) << 8).astype('<i2', copy=False).tobytes()
        else:
            data = samples.reshape(-1, width)[:, -2:].tobytes()
    return VoiceAudio(data, rate, channels)


def prepare_wechat_speech(clip: VoiceAudio) -> VoiceAudio:
    """Even out short-term speech levels before WeChat records the reply."""
    import numpy as np

    samples = np.frombuffer(clip.pcm, dtype='<i2').reshape(-1, clip.channels).astype(np.float32) / 32_768
    if not len(samples):
        return clip
    hop = max(1, round(clip.sample_rate * .02))
    starts = np.arange(0, len(samples), hop)
    edges = np.r_[starts, len(samples)]
    energy = np.square(samples).mean(axis=1)
    rms = np.sqrt(np.add.reduceat(energy, starts) / np.diff(edges))
    activity = np.clip((rms - .003) / .014, 0.0, 1.0)
    target = np.clip(np.sqrt(.135 / np.maximum(rms, .003)), .55, 2.4)
    gains = 1 + (target - 1) * activity
    for index in range(1, len(gains)):
        previous = gains[index - 1]
        coefficient = .68 if gains[index] < previous else .28
        gains[index] = previous + coefficient * (gains[index] - previous)
    frames = np.arange(len(samples))
    samples *= np.interp(frames, (edges[:-1] + edges[1:]) / 2, gains)[:, None]

    # A shared, interpolated limiter preserves peaks and relative channel levels.
    ceiling = .84
    peaks = np.maximum.reduceat(np.abs(samples).max(axis=1), starts)
    limits = ceiling / np.maximum(peaks, ceiling)
    if limits.min() < 1:
        boundaries = np.r_[limits[0], np.minimum(limits[:-1], limits[1:]), limits[-1]]
        samples *= np.interp(frames, edges, boundaries)[:, None]
    return VoiceAudio(np.rint(samples * 32_768).astype('<i2').tobytes(), clip.sample_rate, clip.channels)


def _voiced_cycles(vowel: VoiceAudio):
    import numpy as np

    rate = vowel.sample_rate
    samples = np.frombuffer(vowel.pcm, dtype='<i2').reshape(-1, vowel.channels)
    mono = samples[:6 * rate].mean(axis=1)
    window_size = max(1, round(rate * .055))
    if rate < 4_000 or len(mono) < window_size:
        return [], []
    hop = round(rate * .012)
    windows = np.lib.stride_tricks.sliding_window_view(mono, window_size)[::hop]
    lags = np.arange(round(rate / 650), round(rate / 85) + 1)
    fft_size = 1 << (2 * window_size - 1).bit_length()
    source_indices = np.arange(window_size)
    cycle_size = 256
    cycle_positions = np.arange(3 * cycle_size) / cycle_size
    cycles, periods = [], []
    previous_spectrum = None
    minimum_rms = max(100, .12 * np.sqrt(np.mean(mono ** 2)))
    for window in windows:
        frame = window - window.mean()
        squares = np.r_[0., np.cumsum(frame ** 2)]
        if squares[-1] < window_size * minimum_rms ** 2:
            continue
        correlation = np.fft.irfft(abs(np.fft.rfft(frame, n=fft_size)) ** 2, n=fft_size)
        scale = np.sqrt(squares[window_size - lags] * (squares[-1] - squares[lags]))
        scores = correlation[lags] / np.maximum(scale, 1.0)
        peaks = np.flatnonzero((scores[1:-1] >= scores[:-2]) & (scores[1:-1] > scores[2:])) + 1
        if not len(peaks) or (highest := scores[peaks].max()) < .72:
            continue
        peak = peaks[scores[peaks] >= max(.70, highest * .92)][0]
        left, center, right = scores[peak - 1:peak + 2]
        curvature = left - 2 * center + right
        period = lags[peak] + .5 * (left - right) / curvature
        start = (window_size - period * 3) / 2
        average = np.interp(start + period * cycle_positions, source_indices, frame)
        spectrum = np.fft.rfft(average.reshape(3, cycle_size).mean(axis=0))
        spectrum[0] = 0
        spectrum[33:] = 0  # Keep upper harmonics below Nyquist across the melody.
        if previous_spectrum is not None:
            shift = np.fft.irfft(previous_spectrum * spectrum.conj()).argmax()
            spectrum *= np.exp(-2j * np.pi * np.arange(len(spectrum)) * shift / cycle_size)
        cycle = np.fft.irfft(spectrum)
        peak_level = abs(cycle).max()
        if peak_level >= 50:
            cycles.append(cycle / peak_level)
            periods.append(period)
            previous_spectrum = spectrum
    return cycles, periods


def _hummed_note(cycles, frequency, length, rate, *, vibrato):
    import numpy as np

    time = np.arange(length) / rate
    duration = length / rate
    depth = np.clip((time - .07) / .25, 0, 1) * .012
    pitch = frequency * (1 - .023 * np.exp(-time / .05) + depth * np.sin(2 * np.pi * vibrato * time))
    cycle_size = cycles.shape[1]
    phase = np.cumsum(pitch) * cycle_size / rate % cycle_size

    # Interpolate in phase and between neighboring source frames to retain
    # changes in the original voice without discontinuities between cycles.
    frame = np.linspace(0, len(cycles) - 1, length)
    lower = frame.astype(int)
    upper = np.minimum(lower + 1, len(cycles) - 1)
    index = phase.astype(int)
    next_index = (index + 1) % cycle_size
    current = cycles[lower, index] + (cycles[upper, index] - cycles[lower, index]) * (frame - lower)
    following = cycles[lower, next_index] + (cycles[upper, next_index] - cycles[lower, next_index]) * (frame - lower)
    tone = current + (following - current) * (phase - index)

    release = min(.17, .24 * duration)
    ramp = np.clip(np.minimum(time / .055, (time[-1] - time) / release), 0, 1)
    swell = .78 + .22 * np.sin(np.pi * time / duration)
    return tone * np.sin(np.pi / 2 * ramp) ** 2 * swell


def make_hummed_melody(vowel: VoiceAudio) -> VoiceAudio:
    """Render an original melody and quiet harmony from a voiced syllable."""
    import numpy as np

    cycles, periods = _voiced_cycles(vowel)
    if not cycles:
        raise ValueError('未能从合成音频中提取歌声音色')
    root = np.clip(vowel.sample_rate / np.median(periods), 145, 310)
    cycles = np.stack(cycles)
    rate = 48_000
    gap = bytes(round(rate * .04) * 2)
    phrase = ((0, None, .44, .81), (4, 0, .44, .94), (7, 4, .58, 1.),
              (9, 5, .44, .91), (7, 4, .44, .88), (4, 0, .44, .83), (0, -5, .8, .92))
    span = min(len(cycles), 40)
    offset = round(rate * .03)
    notes = []
    for index, (semitones, harmony, seconds, emphasis) in enumerate(phrase):
        start = round(index * (len(cycles) - span) / (len(phrase) - 1))
        accent = cycles[start:start + span]
        length = round(rate * seconds)
        note = .38 * _hummed_note(accent, root * 2 ** (semitones / 12), length, rate, vibrato=5.1)
        if harmony is not None:
            note[offset:] += .12 * _hummed_note(accent[::-1], root * 2 ** (harmony / 12),
                                                length - offset, rate, vibrato=4.4)
        notes.append(np.rint(note * (32_768 * emphasis)).astype('<i2').tobytes())
    return VoiceAudio(2 * gap + gap.join(notes) + 3 * gap, rate, 1)


def make_voice_probe() -> VoiceAudio:
    """Generate a short audible marker without using the microphone or network."""
    import numpy as np

    rate = 24_000
    span = np.arange(round(rate * _PROBE_TONE_SECONDS))
    ramp = np.minimum(np.minimum(span, len(span) - 1 - span) / (rate * 0.02), 1)
    silence = bytes(round(rate * _PROBE_GAP_SECONDS) * 2)
    tones = (
        (10_000 * ramp * np.sin(span * (2 * np.pi * frequency / rate))).astype('<i2').tobytes()
        for frequency in _PROBE_FREQUENCIES
    )
    return VoiceAudio(silence + silence.join(tones) + silence, rate, 1)


def matches_voice_probe(recording: bytes, sample_rate: int) -> bool:
    """Look for all three test tones with their original spacing in captured PCM."""
    import numpy as np

    if len(recording) % 2 or sample_rate < 8_000:
        return False
    step = round(sample_rate * .05)
    samples = np.frombuffer(recording, dtype='<i2')
    frames = samples[:len(samples) // step * step].reshape(-1, step)
    tone_frames = round(_PROBE_TONE_SECONDS * sample_rate / step)
    stride = round((_PROBE_TONE_SECONDS + _PROBE_GAP_SECONDS) * sample_rate / step)
    length = (len(_PROBE_FREQUENCIES) - 1) * stride + tone_frames
    if len(frames) < length:
        return False
    centered = frames - frames.mean(axis=1, keepdims=True)
    audible = np.mean(centered ** 2, axis=1) >= 64 ** 2
    power = abs(np.fft.rfft(centered * np.hanning(step), axis=1)) ** 2
    total = power.sum(axis=1)
    signatures = []
    for frequency in _PROBE_FREQUENCIES:
        index = round(frequency * step / sample_rate)
        signatures.append(audible & (power[:, index - 1:index + 2].sum(axis=1) > total * .65))
    return any(
        all(np.count_nonzero(signature[at + offset * stride:at + offset * stride + tone_frames])
            >= tone_frames * .7 for offset, signature in enumerate(signatures))
        for at in range(len(frames) - length + 1)
    )
