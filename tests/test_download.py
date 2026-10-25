from pathlib import Path

import pytest
import yt_dlp
import media


@pytest.mark.parametrize('url', ['https://www.bilibili.com/video/BV1WSJA6NE43/?p=2', 'https://b23.tv/example', 'https://example.com/video'])
def test_all_sites_use_ytdlp_without_changing_selected_page(monkeypatch, tmp_path, url):
    monkeypatch.setattr(media, 'public_url', lambda u: u)
    calls = []
    class Downloader:
        def __init__(self, options):
            assert options['noplaylist'] and options['playlistend'] == 1
            assert 'cookiesfrombrowser' not in options and 'cookiefile' not in options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, source, download):
            calls.append((source, download))
            (tmp_path / 'source.mp4').write_bytes(b'complete')
            (tmp_path / 'source.mp4.part').write_bytes(b'incomplete' * 50)
            return {'title': 'Demo'}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Downloader)
    result, title = media.download(url, tmp_path, lambda: None, lambda *args: None)
    assert calls == [(url, True)] and result.name == 'source.mp4' and title == 'Demo'


def test_security_error_is_specific_and_does_not_expose_signed_url(monkeypatch, tmp_path):
    monkeypatch.setattr(media, 'public_url', lambda u: u)
    class Downloader:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs):
            raise yt_dlp.utils.DownloadError('HTTP Error 412 at https://cdn.example/file?token=private-secret')
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Downloader)
    with pytest.raises(media.DownloadFailure) as failure:
        media.download('https://example.com/video', tmp_path, lambda: None, lambda *args: None)
    assert '412' in str(failure.value) and 'private-secret' not in str(failure.value)
    assert 'cdn.example' not in str(failure.value)


def test_cancel_and_duration_limit_remain_meaningful(monkeypatch, tmp_path):
    monkeypatch.setattr(media, 'public_url', lambda u: u)
    def cancelled(): raise media.Cancelled()
    with pytest.raises(media.Cancelled):
        media.download('https://example.com/video', tmp_path, cancelled, lambda *a: None)
    class Downloader:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs):
            self.options['match_filter']({'duration': 20000}, incomplete=False)
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Downloader)
    with pytest.raises(media.DownloadFailure, match='三小时'):
        media.download('https://example.com/video', tmp_path, lambda: None, lambda *a: None)
