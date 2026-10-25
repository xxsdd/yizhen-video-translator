"""Bilibili adapter through bilibili-api-python, without reading browser cookies."""
import asyncio
import re
from urllib.parse import parse_qs, urlparse
import httpx
from media import Cancelled, public_url, run


def download_bili(url, folder, check, progress):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
    try:
        if (urlparse(url).hostname or "").endswith("b23.tv"):
            with httpx.Client(headers=headers, follow_redirects=False, timeout=25) as client:
                for _ in range(5):
                    response = client.get(public_url(url))
                    if response.is_redirect:
                        url = response.headers["location"]
                    else:
                        break
        match = re.search(r"BV[a-zA-Z0-9]{10}", url)
        if not match:
            raise RuntimeError("请使用包含 BV 编号的单个视频链接。")
        page = max(0, int(parse_qs(urlparse(url).query).get("p", ["1"])[0]) - 1)
        bvid = match.group()

        async def info_and_streams():
            from bilibili_api import video, select_client
            select_client("aiohttp")
            v = video.Video(bvid=bvid)
            info = await v.get_info()
            pages = info.get("pages", [])
            if page >= len(pages):
                raise RuntimeError("链接中的分 P 编号不存在。")
            if pages[page].get("duration", 0) > 10802:
                raise RuntimeError("第一版支持三小时以内的视频。")
            raw = await v.get_download_url(page_index=page)
            streams = video.VideoDownloadURLDataDetecter(raw).detect_best_streams(
                video_max_quality=video.VideoQuality._1080P, codecs=[video.VideoCodecs.AVC],
                no_dolby_video=True, no_dolby_audio=True, no_hdr=True, no_hires=True)
            return info, [s.url for s in streams if s is not None]

        progress("正在读取 B 站视频", 3)
        info, streams = asyncio.run(info_and_streams())
        check()
        if not streams:
            raise RuntimeError("没有可获取的视频流。")
        paths = []
        for index, stream in enumerate(streams):
            path = folder / f"bili-stream-{index}.m4s"
            with httpx.Client(headers=headers, timeout=60, follow_redirects=False) as client:
                with client.stream("GET", public_url(stream)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0))
                    received = 0
                    with path.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            check()
                            output.write(chunk)
                            received += len(chunk)
                            progress("正在获取 B 站视频", 4 + 12 * (index + (received / total if total else 0)) / len(streams))
            paths.append(path)
        destination = folder / "source.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        for path in paths:
            cmd += ["-i", str(path)]
        if len(paths) == 2:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += ["-c", "copy", str(destination)]
        run(cmd, check)
        return destination, info.get("title", bvid)
    except Cancelled:
        raise
    except Exception as exc:
        raise RuntimeError("B 站视频获取失败，可能需要登录或受到站点限制。请上传本地视频文件继续处理。") from exc
