"""Bounded VAD calls reset recurrent state; overlap protects window edges."""
from faster_whisper.vad import get_speech_timestamps, VadOptions

RATE = 16000
OPTIONS = dict(threshold=.35, min_speech_duration_ms=150,
               min_silence_duration_ms=500, speech_pad_ms=300)


def merge_windows(windows):
    result = []
    for start, end in sorted(windows):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def speech_windows(audio, check=lambda: None):
    """Recheck long gaps in smaller windows without lowering the speech gate."""
    length = len(audio)
    windows = []

    def scan(begin, end, step, threshold=.35):
        for core in range(begin, end, step * RATE):
            check()
            left, right = max(0, core - RATE), min(length, core + step * RATE + RATE)
            chunks = get_speech_timestamps(audio[left:right], VadOptions(**{**OPTIONS, 'threshold':threshold}))
            windows.extend((max(begin, left + c['start']), min(end, left + c['end'])) for c in chunks)

    scan(0, length, 60)
    first = merge_windows(windows)
    cursor = 0
    for start, end in first + [[length, length]]:
        if start - cursor >= 8 * RATE:
            # Only missed gaps get a more sensitive second look. Recognition's
            # no-speech, repetition and confidence checks still apply afterward.
            scan(cursor, start, 10, threshold=.25)
        cursor = end
    check()
    return merge_windows(windows)
