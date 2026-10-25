import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
import store
from urllib.parse import urlparse

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Cancelled(Exception):
    pass


def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请输入完整的 http(s) 视频网页链接。")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("视频链接需要指向互联网网站；本地视频请使用文件导入。")
    except socket.gaierror as exc:
        raise ValueError("无法解析网站地址，请检查链接。") from exc
    return url


def run(args, check=lambda: None, timeout=14400):
    # No shell; argv prevents URL / filename command injection.
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW) as process:
        start = time.monotonic()
        try:
            while True:
                check()
                try:
                    out, err = process.communicate(timeout=.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - start > timeout:
                        raise RuntimeError("媒体处理超时，请重试或更换文件。")
            if process.returncode:
                raise RuntimeError("媒体处理失败，文件可能损坏或包含不支持的编码。")
            return out.decode("utf-8", errors="replace")
        except BaseException:
            process.kill()
            process.communicate()
            raise


def probe(path):
    if not shutil.which("ffprobe"):
        raise RuntimeError("未找到 FFmpeg，请重新运行安装脚本。")
    result = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30))
    duration = float(result.get("format", {}).get("duration", 0))
    streams = result.get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise RuntimeError("这个文件没有可识别的音轨。")
    if not duration or duration > 3 * 3600 + 2:
        raise RuntimeError("第一版支持时长不超过三小时的音视频文件。")
    return duration, streams


def run_conversion(cmd, duration, check, progress):
    # FFmpeg writes actual output timestamps; a file avoids blocking pipe reads
    # on Windows and keeps the existing cancellation/timeout handling in run().
    with tempfile.TemporaryDirectory(prefix="yizhen-progress-") as directory:
        report = Path(directory) / "progress.txt"
        last_read, last_seconds = 0.0, -1.0

        def poll():
            nonlocal last_read, last_seconds
            check()
            now = time.monotonic()
            if now - last_read < 1:
                return
            last_read = now
            try:
                with report.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 8192))
                    content = stream.read().decode("ascii", errors="ignore")
            except FileNotFoundError:
                return
            timestamps = re.findall(r"^out_time_us=(\d+)\r?$", content, re.MULTILINE)
            if not timestamps:
                return
            seconds = min(duration, int(timestamps[-1]) / 1_000_000)
            if seconds <= last_seconds:
                return
            last_seconds = seconds
            fraction = min(.99, seconds / duration)
            def clock(value):
                value = int(value)
                return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"
            phase = "正在完成视频封装" if seconds >= duration * .99 else "正在转换视频格式"
            progress(f"{phase} · {int(fraction * 100)}%（{clock(seconds)} / {clock(duration)}）", 19 + 5 * fraction)

        run([cmd[0], "-progress", str(report), "-stats_period", "1", "-nostats", *cmd[1:]], poll)


@lru_cache(maxsize=1)
def nvenc_available():
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-f", "lavfi", "-i", "color=c=black:s=640x360:d=0.2",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=CREATE_NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def video_encoder(device):
    if device != "cpu" and nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"], "NVIDIA GPU"
    if device == "cuda":
        raise RuntimeError("NVIDIA GPU 编码不可用，请检查显卡驱动，或在设置中选择 CPU 后继续。")
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p"], "CPU"


def prepare(source, folder, check, progress):
    duration, streams = probe(source)
    destination = folder / "playback.mp4"
    if not destination.exists():
        progress("正在准备可播放的视频", 19)
        videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        temporary = folder / "playback.tmp.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source)]
        conversion_label = "音频处理 · CPU"
        if videos:
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
            if videos[0].get("codec_name") == "h264":
                cmd += ["-c:v", "copy"]
                conversion_label = "直接封装 · 无需视频编码"
            else:
                options, conversion_label = video_encoder(store.settings()["conversion_device"])
                cmd += options
        else:
            cmd += ["-map", "0:a:0", "-vn"]
        cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temporary)]
        progress(f"正在准备可播放的视频 · {conversion_label}", 19)
        run_conversion(cmd, duration, check, lambda message, value: progress(f"{message} · {conversion_label}", value))
        temporary.replace(destination)
    progress("视频格式准备完成", 24)
    audio = folder / "audio.wav"
    if not audio.exists():
        progress("正在提取人声识别所需的音频", 24)
        temporary = folder / "audio.tmp.wav"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(temporary)], check)
        temporary.replace(audio)
    return duration


class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class DownloadFailure(RuntimeError):
    """Safe user-facing download errors; never include raw URLs or credentials."""


def download_error(exc):
    details, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        details.append(str(exc).lower())
        exc = getattr(exc, "__cause__", None) or getattr(exc, "cause", None) or getattr(exc, "__context__", None)
    message = " ".join(details)
    if re.search(r"(?:http (?:error )?|status(?: code)?[：: ]+)(412|429)\b", message):
        code = "412" if "412" in message else "429"
        return f"网站拒绝了本次请求（HTTP {code}，访问风控或频率限制）。yt-dlp 已尝试获取，请稍后重试或导入本地文件。"
    if "drm" in message:
        return "该视频使用 DRM 保护，yt-dlp 无法获取可用的媒体。"
    if any(term in message for term in ("sign in", "login", "log in", "cookies", "captcha", "登录")):
        return "网站要求登录或验证身份。当前下载未使用浏览器登录状态，请导入本地文件。"
    if any(term in message for term in ("geo", "not available in your country", "region")):
        return "网站限制了该视频的访问地区，当前无法获取。"
    if "403" in message:
        return "网站拒绝访问视频（HTTP 403）。可能需要登录或受到站点访问限制。"
    if "unsupported url" in message:
        return "yt-dlp 暂不支持这个链接。请使用单个视频的播放页链接，或导入本地文件。"
    if "requested format is not available" in message or "no video formats" in message:
        return "已识别视频，但未取得可用的音视频格式；网站可能限制了当前访问方式。"
    if any(term in message for term in ("timed out", "timeout", "connection", "resolve", "network", "certificate")):
        return "获取视频时网络连接失败。请检查网络后重试，或导入本地文件。"
    return "yt-dlp 获取失败：链接可能已失效、需要登录或网站规则发生变化。请稍后重试，或导入本地文件。"


def save_poster(url, folder, check):
    """Best-effort bounded public image fetch, stored locally before media download."""
    import httpx
    if not url or (folder / 'poster.jpg').exists():
        return
    tag = uuid.uuid4().hex
    raw = folder / f'poster-{tag}.tmp'
    output = folder / f'poster-{tag}.jpg'
    try:
        with httpx.Client(timeout=6, follow_redirects=False) as client:
            for _ in range(4):
                check()
                public_url(url)
                with client.stream('GET', url) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        url = urljoin(url, response.headers['location'])
                        continue
                    response.raise_for_status()
                    if response.headers.get('content-type', '').split(';')[0] not in ('image/jpeg', 'image/png', 'image/webp'):
                        return
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        check()
                        data.extend(chunk)
                        if len(data) > 3 * 1024 * 1024:
                            return
                    if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP')):
                        return
                    raw.write_bytes(data)
                    run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe',
                         '-i', str(raw), '-frames:v', '1', '-vf', 'scale=640:-2', str(output)], check, timeout=15)
                    with store.LOCK:
                        check()
                        if not (folder / 'poster.jpg').exists():
                            output.replace(folder / 'poster.jpg')
                    return
    except Cancelled:
        raise
    except Exception:
        pass  # A missing cover must not prevent importing a video.
    finally:
        raw.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def preview(url, check):
    import yt_dlp
    public_url(url)
    check()
    with yt_dlp.YoutubeDL({'skip_download': True, 'noplaylist': True, 'playlistend': 1,
                          'logger': QuietLogger(), 'quiet': True, 'no_warnings': True,
                          'socket_timeout': 8, 'retries': 0, 'cachedir': False}) as dl:
        result = dl.extract_info(url, download=False)
    check()
    if not result or result.get('_type') in ('playlist', 'multi_video'):
        return {}
    return {k:result.get(k) for k in ('title','duration','thumbnail')}


def download(url, folder, check, progress, on_metadata=None, referer=None):
    import yt_dlp
    public_url(url)
    check()
    progress("正在使用 yt-dlp 读取视频", 3)

    def hook(data):
        check()
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        if data["status"] == "downloading":
            ratio = min(1, data.get("downloaded_bytes", 0) / total) if total else 0
            progress("正在使用 yt-dlp 获取视频", round(3 + ratio * 14, 1))

    def filter_video(info, *, incomplete):
        check()
        if info.get("is_live"):
            raise DownloadFailure("暂不支持直播，请导入已结束的视频。")
        if info.get("duration", 0) and info["duration"] > 10802:
            raise DownloadFailure("第一版支持三小时以内的视频。")
        if on_metadata and not incomplete:
            on_metadata(info)

    opts = {"outtmpl": str(folder / "source.%(ext)s"), "format": "bv*+ba/best",
            "merge_output_format": "mp4", "noplaylist": True, "playlistend": 1,
            "progress_hooks": [hook], "match_filter": filter_video,
            "logger": QuietLogger(), "quiet": True, "no_warnings": True,
            "socket_timeout": 25, "retries": 2, "fragment_retries": 2, "cachedir": False,
            "concurrent_fragment_downloads": 2, "windowsfilenames": True}
    if referer:
        public_url(referer)
        opts['http_headers'] = {'Referer': referer}
    try:
        with yt_dlp.YoutubeDL(opts) as dl:
            info = dl.extract_info(url, download=True)
        check()
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise DownloadFailure("请粘贴单个视频的链接，暂不导入合集。")
        files = [p for p in folder.glob("source.*") if p.suffix not in (".part", ".ytdl", ".json", ".temp") and p.is_file()]
        if not files:
            raise DownloadFailure("未获得完整视频，请上传本地文件。")
        source = max(files, key=lambda p: p.stat().st_size)
        return source, info.get("title") or "网页视频"
    except (Cancelled, DownloadFailure):
        raise
    except Exception as exc:
        # Do not leak signed URLs, account data, or downloader internals into UI/logs.
        raise DownloadFailure(download_error(exc)) from exc
