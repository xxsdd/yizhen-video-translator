import shutil
import subprocess

import pytest

import media


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")


def synthetic_command():
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-re", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=4:duration=3",
            "-f", "null", "-"]


def test_real_ffmpeg_reports_intermediate_progress():
    reports = []
    media.run_conversion(synthetic_command(), 3, lambda: None,
                         lambda text, percent: reports.append((text, percent)))
    values = [value for _, value in reports]
    assert any(19 < value < 23.9 for value in values)
    assert values == sorted(values)
    # Completion is only recorded by prepare after the output is finalized.
    assert all(value < 24 for value in values)
    assert all("00:00:03" in text for text, _ in reports)


def test_cancel_stops_ffmpeg_and_cleans_progress_file(monkeypatch):
    processes, reports = [], []
    original = subprocess.Popen

    def launch(args, **kwargs):
        process = original(args, **kwargs)
        processes.append(process)
        reports.append(media.Path(args[args.index("-progress") + 1]))
        return process

    monkeypatch.setattr(media.subprocess, "Popen", launch)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 2:
            raise media.Cancelled()

    with pytest.raises(media.Cancelled):
        media.run_conversion(synthetic_command(), 3, cancel, lambda *a: None)
    assert processes[0].poll() is not None
    assert not reports[0].parent.exists()


def test_prepare_marks_complete_only_after_playback_exists(tmp_path):
    source = tmp_path / "source.wav"
    media.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-f", "lavfi",
               "-i", "sine=frequency=440:duration=0.5", str(source)])
    reports = []

    def progress(text, percent):
        if percent >= 24:
            assert (tmp_path / "playback.mp4").exists()
        reports.append(percent)

    duration = media.prepare(source, tmp_path, lambda: None, progress)
    assert duration > 0 and 24 in reports
    assert (tmp_path / "audio.wav").exists()

def test_encoder_selection_and_unavailable_gpu(monkeypatch):
    monkeypatch.setattr(media, 'nvenc_available', lambda: True)
    assert 'h264_nvenc' in media.video_encoder('auto')[0]
    assert 'libx264' in media.video_encoder('cpu')[0]
    monkeypatch.setattr(media, 'nvenc_available', lambda: False)
    assert 'libx264' in media.video_encoder('auto')[0]
    with pytest.raises(RuntimeError, match='GPU 编码不可用'):
        media.video_encoder('cuda')
