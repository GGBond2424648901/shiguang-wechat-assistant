import struct
import wave

import pytest

from xiaomao.voice_audio import make_voice_probe, matches_voice_probe, read_voice_wav


def write_wav(tmp_path, pcm, *, width=2, rate=24_000, channels=1):
    path = tmp_path / 'clip.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm)
    return path


def test_read_pcm_preserves_stereo_samples_and_duration(tmp_path):
    pcm = struct.pack('<hh', 12_345, -12_345) * 24_000
    clip = read_voice_wav(write_wav(tmp_path, pcm, channels=2))
    assert (clip.pcm, clip.sample_rate, clip.channels, clip.duration) == (pcm, 24_000, 2, 1)


@pytest.mark.parametrize(('width', 'source', 'expected'), [
    (1, b'\x00\x80\xff', (-32_768, 0, 32_512)),
    (3, b'\x12\x34\x80\x01\x00\x00\x52\xff\x7f', (-32_716, 0, 32_767)),
    (4, struct.pack('<iii', -2**31, 65_536, 2**31-1), (-32_768, 1, 32_767)),
])
def test_other_pcm_widths_are_converted_to_signed_16_bit(tmp_path, width, source, expected):
    clip = read_voice_wav(write_wav(tmp_path, source, width=width))
    assert struct.unpack('<hhh', clip.pcm) == expected


def test_missing_empty_and_truncated_wav_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='WAV'):
        read_voice_wav(tmp_path / 'missing.wav')
    with pytest.raises(ValueError, match='为空'):
        read_voice_wav(write_wav(tmp_path, b''))
    path = write_wav(tmp_path, b'\x10\x02' * 24_000)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match='不完整'):
        read_voice_wav(path)


def test_voice_limit_is_checked_before_reading_the_audio(tmp_path):
    path = write_wav(tmp_path, b'\x10\x02' * 24_001)
    with pytest.raises(ValueError, match='超过 1 秒'):
        read_voice_wav(path, max_seconds=1)


def test_probe_is_short_clipping_free_and_audible():
    clip = make_voice_probe()
    samples = struct.unpack(f'<{len(clip.pcm) // 2}h', clip.pcm)
    assert clip.channels == 1
    assert 1 <= clip.duration <= 3
    assert 2_000 < max(samples) < 16_000
    assert min(samples) < -2_000
    assert samples[0] == samples[-1] == 0


def test_probe_match_allows_device_delay_and_lower_signal_level():
    clip = make_voice_probe()
    samples = struct.unpack(f'<{len(clip.pcm) // 2}h', clip.pcm)
    quieter = struct.pack(f'<{len(samples)}h', *(s // 8 for s in samples))
    recording = bytes(6_347 * 2) + quieter + bytes(400)
    assert matches_voice_probe(recording, clip.sample_rate)


def test_probe_match_requires_the_entire_sequence_instead_of_ambient_sound():
    clip = make_voice_probe()
    samples = struct.unpack(f'<{len(clip.pcm) // 2}h', clip.pcm)
    backwards = struct.pack(f'<{len(samples)}h', *reversed(samples))
    assert not matches_voice_probe(backwards, clip.sample_rate)
    assert not matches_voice_probe(clip.pcm[:len(clip.pcm) // 2] * 2, clip.sample_rate)
    assert not matches_voice_probe(bytes(len(clip.pcm)), clip.sample_rate)
    assert not matches_voice_probe(clip.pcm + b'\x11', clip.sample_rate)


def test_wechat_speech_recovers_quiet_syllables_without_clipping():
    import numpy as np

    from xiaomao.voice_audio import VoiceAudio, prepare_wechat_speech

    rate = 48_000
    time = np.arange(rate) / rate
    quiet = 800 * np.sin(2 * np.pi * 650 * time)
    loud = 27_000 * np.sin(2 * np.pi * 300 * time)
    loud[rate // 2] = 32_767
    source = np.r_[np.zeros(rate // 4), quiet, loud]
    pcm = np.column_stack((source, -.5 * source)).astype('<i2')

    result = prepare_wechat_speech(VoiceAudio(pcm.tobytes(), rate, 2))
    signal = np.frombuffer(result.pcm, dtype='<i2').reshape(-1, 2).astype(float)
    steady_quiet = slice(rate // 2, rate)

    assert (result.sample_rate, result.channels, len(signal)) == (rate, 2, len(source))
    assert not signal[:rate // 8].any()
    assert np.linalg.norm(signal[steady_quiet, 0]) > 1.7 * np.linalg.norm(quiet[:rate // 2])
    assert abs(signal).max() < 29_000
    assert np.max(abs(signal[:, 1] + .5 * signal[:, 0])) < 3


def test_wechat_speech_limits_an_isolated_peak_without_flattening_the_transient():
    import numpy as np

    from xiaomao.voice_audio import VoiceAudio, prepare_wechat_speech

    rate = 48_000
    source = (800 * np.sin(np.arange(rate) * (2 * np.pi * 650 / rate))).astype('<i2')
    at = rate // 2
    source[at:at + 2] = (32_767, 26_214)

    result = prepare_wechat_speech(VoiceAudio(source.tobytes(), rate, 1))
    signal = np.frombuffer(result.pcm, dtype='<i2')

    assert 25_000 < signal[at] < 29_000
    assert signal[at + 1] / signal[at] == pytest.approx(.8, abs=.01)


def test_original_humming_uses_several_pitches_from_a_voiced_sample():
    import numpy as np

    from xiaomao.voice_audio import VoiceAudio, make_hummed_melody

    rate = 24_000
    t = np.arange(rate) / rate
    sample = (8500 * np.sin(2 * np.pi * 220 * t) + 2200 * np.sin(4 * np.pi * 220 * t))
    melody = make_hummed_melody(VoiceAudio(sample.astype('<i2').tobytes(), rate, 1))
    assert 3 < melody.duration < 6 and melody.channels == 1
    signal = np.frombuffer(melody.pcm, dtype='<i2')
    pitches = set()
    span = melody.sample_rate // 5
    for at in range(0, len(signal) - span, span):
        segment = signal[at:at + span]
        if np.max(abs(segment)) > 2500:
            frequency = np.argmax(abs(np.fft.rfft(segment * np.hanning(span)))) * melody.sample_rate / span
            if frequency <= 450:
                pitches.add(frequency)
    assert len(pitches) >= 4


def test_original_humming_tracks_more_than_one_vocal_shape():
    import numpy as np

    from xiaomao.voice_audio import VoiceAudio, make_hummed_melody

    rate = 24_000
    time = np.arange(2 * rate) / rate
    second_harmonic = np.minimum(np.linspace(200, 6_000, len(time)), 5_500)
    vowel = 8_000 * np.sin(2 * np.pi * 220 * time) + second_harmonic * np.sin(4 * np.pi * 220 * time)
    melody = make_hummed_melody(VoiceAudio(vowel.astype('<i2').tobytes(), rate, 1))
    signal = np.frombuffer(melody.pcm, dtype='<i2')
    span = round(melody.sample_rate * .11)
    frequencies = np.fft.rfftfreq(span, 1 / melody.sample_rate)

    def overtone_ratio(at):
        start = round(at * melody.sample_rate)
        spectrum = abs(np.fft.rfft(signal[start:start + span] * np.hanning(span)))
        fundamental = spectrum[(frequencies > 195) & (frequencies < 245)].max()
        overtone = spectrum[(frequencies > 405) & (frequencies < 470)].max()
        return overtone / fundamental

    assert overtone_ratio(.30) - overtone_ratio(.14) > .045
    assert melody.sample_rate == 48_000


def test_original_humming_adds_a_quieter_second_voice_under_the_melody():
    import numpy as np

    from xiaomao.voice_audio import VoiceAudio, make_hummed_melody

    rate = 24_000
    time = np.arange(rate) / rate
    vowel = 8_000 * np.sin(2 * np.pi * 220 * time)
    melody = make_hummed_melody(VoiceAudio(vowel.astype('<i2').tobytes(), rate, 1))
    signal = np.frombuffer(melody.pcm, dtype='<i2')
    mid_note = signal[round(melody.sample_rate * .67):round(melody.sample_rate * .82)]
    spectrum = abs(np.fft.rfft(mid_note * np.hanning(len(mid_note))))
    frequencies = np.fft.rfftfreq(len(mid_note), 1 / melody.sample_rate)
    lead = spectrum[(frequencies > 260) & (frequencies < 295)].max()
    harmony = spectrum[(frequencies > 205) & (frequencies < 233)].max()

    assert .10 < harmony / lead < .65
    assert abs(signal.astype(float)).max() < 29_000
