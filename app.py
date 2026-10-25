import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

import media
import pipeline
import store
import library_features
import cover_features
import codex_bridge
from translate import LANGUAGES, ProviderError, completion


@asynccontextmanager
async def lifespan(app):
    pipeline.STOP.clear()
    thread = None
    if os.environ.get("VTS_DISABLE_WORKER") != "1":
        threading.Thread(target=codex_bridge.status, daemon=True, name="codex-detect").start()
        thread = threading.Thread(target=pipeline.worker, daemon=True, name="video-worker")
        thread.start()
    yield
    pipeline.STOP.set()
    pipeline.WAKE.set()
    if thread:
        thread.join(timeout=5)
        codex_bridge.shutdown()


app = FastAPI(title="译帧 · 本地视频翻译", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(codex_bridge.router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"] + (["testserver"] if os.environ.get("VTS_DISABLE_WORKER") == "1" else []))


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if request.headers.get("x-local-app") != "1" or (origin and urlparse(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "请从本机工具页面操作。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith(("/api/", "/auth/")):
        response.headers["Cache-Control"] = "no-store"
    return response


def get_job(job_id):
    try:
        item = store.job(job_id)
        if item.get('delete_requested'):
            raise KeyError(job_id)
        return item
    except KeyError:
        raise HTTPException(404, "找不到这个视频任务。")


def public_job(item):
    result = {k: v for k, v in item.items() if k not in ("source_file", "base_url", "browser_poster_url", "browser_referer")}
    result["has_poster"] = (store.DATA / "jobs" / item["id"] / "poster.jpg").exists()
    result['downloaded'] = bool(item.get('source_file')) and (store.DATA / 'jobs' / item['id'] / item['source_file']).is_file()
    return result


@app.get("/api/state")
def state():
    return {"jobs": [public_job(j) for j in store.jobs() if not j.get('delete_requested')], "settings": store.settings(public=True),
            "languages": LANGUAGES, "source_languages": {**LANGUAGES, 'ru':'俄语', 'uk':'乌克兰语', 'pt':'葡萄牙语', 'it':'意大利语', 'ar':'阿拉伯语', 'nn':'挪威语（尼诺斯克）', 'no':'挪威语'}, "model": pipeline.MODEL_STATUS, "ffmpeg": bool(shutil.which("ffmpeg")),
            "free_gb": round(shutil.disk_usage(store.DATA).free / 1024 ** 3, 1)}


@app.get("/api/health")
def health():
    return {"app": "yizhen-local", "version": "1.0.0"}


class Profile(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)
    model: str = Field(max_length=160)
    api_key: str = Field(default="", max_length=1000)
    clear_key: bool = False
    reasoning_effort: str = Field(default="", max_length=30)


class Config(BaseModel):
    providers: list[Profile] = Field(min_length=1, max_length=10)
    default_provider: str
    target: str = "zh"
    asr_model: str = "large-v3"
    device: str = "cuda"
    conversion_device: str | None = None


@app.post("/api/settings")
def update_settings(body: Config):
    if body.target not in LANGUAGES or body.device not in ("cuda", "cpu") or body.asr_model not in ("large-v3", "medium", "small"):
        raise HTTPException(422, "语言或识别模型无效。")
    if body.conversion_device not in (None, "auto", "cpu", "cuda"):
        raise HTTPException(422, "视频转换设备无效。")
    old = {p["id"]: p for p in store.settings()["providers"]}
    ids = [p.id for p in body.providers]
    if len(ids) != len(set(ids)) or body.default_provider not in ids:
        raise HTTPException(422, "AI 服务配置无效。")
    profiles = []
    for p in body.providers:
        if p.id == "chatgpt":
            profiles.append({"id": "chatgpt", "name": "Codex 订阅", "base_url": codex_bridge.RESOURCE, "model": p.model.strip(), "reasoning_effort": p.reasoning_effort, "key": ""})
            continue
        parsed = urlparse(p.base_url)
        if p.base_url and (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise HTTPException(422, "接口地址应为 http(s) 基础地址，不含密钥或查询参数。")
        if p.base_url and parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise HTTPException(422, "远程 AI 服务必须使用 HTTPS。")
        key = "" if p.clear_key else old.get(p.id, {}).get("key", "")
        if p.api_key.strip():
            key = store.encode_key(p.api_key.strip())
        profiles.append({"id": p.id, "name": p.name, "base_url": p.base_url.rstrip("/"), "model": p.model.strip(), "key": key})
    store.write_settings({"providers": profiles, "default_provider": body.default_provider, "target": body.target, "asr_model": body.asr_model, "device": body.device, "conversion_device": body.conversion_device or store.settings()["conversion_device"]})
    return store.settings(public=True)


@app.post("/api/providers/{provider_id}/models")
def provider_models(provider_id: str):
    try:
        if provider_id == "chatgpt":
            data = codex_bridge.require_ready()
            return {"models": [m["model"] for m in data["models"]], "catalog": data["models"]}
        config = store.provider(provider_id)
        if not config["api_key"] or not config["base_url"]:
            raise HTTPException(400, "请先保存接口地址和 API Key。")
        response = httpx.get(config["base_url"] + "/models", headers={"Authorization": "Bearer " + config["api_key"]}, timeout=25, follow_redirects=False)
        if not response.is_success:
            raise HTTPException(400, f"模型列表请求返回 HTTP {response.status_code}；也可以手动填写模型名称。")
        values = response.json().get("data", [])
        return {"models": sorted([v["id"] for v in values if isinstance(v, dict) and isinstance(v.get("id"), str)])}
    except HTTPException:
        raise
    except codex_bridge.CodexError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(400, "无法读取模型列表，可以手动填写服务商提供的模型名称。")


@app.post("/api/providers/{provider_id}/test")
def provider_test(provider_id: str):
    try:
        completion(store.provider(provider_id), "Translate the user's text into Chinese. Reply with only the translation.", "Hello, world.")
        return {"ok": True, "message": "连接成功，模型已返回文字。"}
    except (ProviderError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/model/prepare")
def prepare_model():
    if pipeline.MODEL_STATUS["state"] != "loading":
        threading.Thread(target=pipeline.warmup, daemon=True, name="model-prepare").start()
    return {"ok": True}


class LinkBatch(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    provider_id: str
    target: str = "zh"
    source_language: str = ""


def options(provider_id, target, source_language):
    if target not in LANGUAGES:
        raise HTTPException(422, "请选择支持的目标语言。")
    if source_language and source_language != 'multi' and not re.fullmatch(r"[a-z]{2,3}", source_language):
        raise HTTPException(422, "原语言无效。")
    try:
        p = store.provider(provider_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc))
    return {"provider_id": p["id"], "provider_name": p["name"], "model": p["model"], "base_url": p["base_url"], "target": target, "source_language": '' if source_language == 'multi' else source_language, 'recognition_language':source_language}


def new_job(title, opts, **extra):
    return {"id": uuid.uuid4().hex, "title": title, "created": time.time(), "updated": time.time(), "status": "queued", "message": "等待本地识别", "progress": 0, "duration": None, "playable": False, "position": 0, "segment_count": 0, "translated_count": 0, "error": None, "translation_requested": False, **opts, **extra}


@app.post("/api/jobs/links")
def add_links(body: LinkBatch):
    opts = options(body.provider_id, body.target, body.source_language)
    urls = list(dict.fromkeys(u.strip() for u in body.urls if u.strip()))
    if not urls:
        raise HTTPException(422, "请至少填写一个链接。")
    for url in urls:
        try:
            media.public_url(url)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    created = []
    for url in urls:
        item = new_job(urlparse(url).hostname or "网页视频", opts, url=url, kind="url", status='awaiting_download', message='待下载，点击开始下载', run_mode='download')
        store.save_job(item)
        created.append(public_job(item))
        schedule_preview(item['id'])
    pipeline.WAKE.set()
    return {"jobs": created}


class BrowserImport(BaseModel):
    url: str = Field(min_length=1, max_length=16000)
    page_url: str = Field(min_length=1, max_length=16000)
    title: str = Field(default="网页视频", max_length=500)
    thumbnail: str = Field(default="", max_length=16000)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")


def schedule_preview(job_id):
    if os.environ.get('VTS_DISABLE_WORKER') != '1':
        threading.Thread(target=pipeline.preview_link, args=(job_id,), daemon=True, name='video-preview').start()


@app.post('/api/browser/import')
def browser_import(body: BrowserImport):
    # Only the local app origin is accepted by local_only. The extension sends
    # through its own isolated content script in an app tab, never arbitrary CORS.
    try:
        media.public_url(body.url)
        media.public_url(body.page_url)
        if body.thumbnail:
            media.public_url(body.thumbnail)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with store.LOCK:
        previous = next((j for j in store.jobs() if j.get('browser_request_id') == body.request_id and not j.get('delete_requested')), None)
        if previous:
            return {'job': public_job(previous), 'reused': True}
        config = store.settings()
        opts = options(config['default_provider'], config['target'], '')
        item = new_job(body.title.strip() or '网页视频', opts, kind='url', url=body.url,
                       browser_referer=body.page_url, browser_poster_url=body.thumbnail,
                       browser_request_id=body.request_id, status='awaiting_download', message='待下载，点击开始下载', run_mode='download')
        store.save_job(item)
    schedule_preview(item['id'])
    pipeline.WAKE.set()
    return {'job': public_job(item), 'reused': False}


@app.post("/api/jobs/files")
async def add_files(files: list[UploadFile] = File(...), provider_id: str = Form(...), target: str = Form("zh"), source_language: str = Form("")):
    if not 1 <= len(files) <= 10:
        raise HTTPException(422, "一次最多导入 10 个文件。")
    opts = options(provider_id, target, source_language)
    allowed = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".ts"}
    for f in files:
        if Path(f.filename or "").suffix.lower() not in allowed:
            raise HTTPException(422, "请选择常见的视频或音频文件。")
    created = []
    for f in files:
        suffix = Path(f.filename).suffix.lower()
        item = new_job(Path(f.filename).name, opts, kind="file", source_file="source" + suffix, status="uploading", message="正在导入文件")
        folder = store.job_dir(item["id"])
        store.save_job(item)
        with store.LOCK:
            pipeline.UPLOADING.add(item['id'])
        size = 0
        try:
            with (folder / ("source" + suffix)).open("wb") as output:
                while chunk := await f.read(1024 * 1024 * 4):
                    if store.job(item['id']).get('delete_requested'):
                        raise media.Cancelled()
                    size += len(chunk)
                    if shutil.disk_usage(store.DATA).free < 2 * 1024 ** 3:
                        raise RuntimeError("磁盘空间不足，请清理缓存后重新导入。")
                    output.write(chunk)
            if not size:
                raise RuntimeError("文件内容为空，请重新选择。")
            item = store.update_job(item["id"], status="awaiting_processing", message="文件已导入，点击开始处理", bytes=size, run_mode='process')
            if not item.get('delete_requested'):
                created.append(public_job(item))
        except media.Cancelled:
            pass
        except Exception as exc:
            store.update_job(item["id"], status="failed", message="文件导入失败，请重新导入", error="文件不完整或磁盘空间不足。", source_file=None)
            raise HTTPException(400, "文件导入失败，请检查文件和磁盘空间后重新导入。") from exc
        finally:
            await f.close()
            with store.LOCK:
                pipeline.UPLOADING.discard(item['id'])
    pipeline.WAKE.set()
    return {"jobs": created}


@app.get("/api/jobs/{job_id}")
def detail(job_id: str):
    path = store.job_dir(get_job(job_id)['id']) / 'transcript.json'
    review = json.loads(path.read_text(encoding='utf-8')).get('review_segments', []) if path.exists() else []
    return {"job": public_job(get_job(job_id)), "segments": pipeline.subtitle_data(job_id), 'review_segments':review}


@app.post("/api/jobs/{job_id}/pause")
def pause(job_id: str):
    return library_features.control_job(job_id, library_features.JobControl(action='pause'))


class Retry(BaseModel):
    provider_id: str | None = None
    target: str | None = None
    resume_saved: bool = False


class SpeechUpdate(BaseModel):
    confirmed_ranges: list[tuple[float, float]] = Field(default_factory=list, max_length=20)


@app.post('/api/jobs/{job_id}/recheck-speech')
def recheck_speech(job_id: str, body: SpeechUpdate | None = None):
    item = get_job(job_id)
    if pipeline.ACTIVE_JOB == job_id or item['status'] in ('processing', 'queued', 'uploading'):
        raise HTTPException(409, '请等当前处理结束或暂停后再复检。')
    folder = store.job_dir(job_id)
    path = folder / 'transcript.json'
    if not (folder / 'audio.wav').exists() or not path.exists():
        raise HTTPException(400, '需要保留本地识别音频和原字幕才能复检。')
    if not json.loads(path.read_text(encoding='utf-8')).get('complete'):
        raise HTTPException(409, '请先完成当前识别，再复检遗漏对白。')
    ranges = list(item.get('confirmed_speech_ranges', []))
    for start,end in (body.confirmed_ranges if body else []):
        if not (0 <= start < end <= (item.get('duration') or 0)):
            raise HTTPException(422, '确认的对白区间必须在视频时长内。')
        if [start,end] not in ranges:
            ranges.append([start,end])
    backup = folder / 'backups' / f'speech-recheck-{time.time_ns()}'
    backup.mkdir(parents=True)
    for name in ('transcript.json', 'translated.json'):
        if (folder / name).exists():
            shutil.copy2(folder / name, backup / name)
    store.atomic_json(backup / 'job.json', item)
    store.update_job(job_id, status='queued', speech_recheck_requested=True, translation_requested=False,
                     error=None, progress=27, confirmed_speech_ranges=ranges,
                     message='等待更新字幕；已有字幕和译文已备份')
    pipeline.WAKE.set()
    return {'ok': True}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str, body: Retry):
    item = get_job(job_id)
    if pipeline.ACTIVE_JOB == job_id:
        raise HTTPException(409, "当前步骤仍在结束中，请稍后再继续。")
    if item["status"] in ("processing", "queued", "uploading"):
        raise HTTPException(409, "任务正在处理中。")
    opts = options(body.provider_id or item["provider_id"], body.target or item["target"], item.get('recognition_language', item.get("source_language", "")))
    opts['source_language'] = item.get('source_language', opts['source_language'])
    if body.resume_saved:
        if body.provider_id or body.target:
            raise HTTPException(422, "继续新增字幕时不能同时更换翻译服务或语言。")
        # Continue the saved version even if the user has since changed the
        # default model; preserve paid subtitles rather than silently redoing them.
        opts.update(model=item["model"], base_url=item["base_url"])
    if not item.get("source_file") and not item.get("url"):
        raise HTTPException(400, "文件导入未完成，请重新添加本地文件。")
    changed = any(opts[k] != item.get(k) for k in ("target", "model", "base_url"))
    if changed:
        # Preserve previous language version on disk; switch the active transcript only.
        path = store.job_dir(job_id) / "translated.json"
        if path.exists():
            path.replace(path.with_name(f"translated-{int(time.time() * 1000)}.json"))
    store.update_job(job_id, **opts, status="queued", translation_requested=False, message="等待继续本地识别；翻译需另行点击", error=None, translated_count=0 if changed else item.get("translated_count", 0))
    pipeline.WAKE.set()
    return {"ok": True}


class Position(BaseModel):
    position: float = Field(ge=0, le=10802)


@app.post("/api/jobs/{job_id}/position")
def position(job_id: str, body: Position):
    get_job(job_id)
    store.update_job(job_id, position=body.position)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/video")
def video(job_id: str):
    get_job(job_id)
    path = store.job_dir(job_id) / "playback.mp4"
    if not path.exists():
        raise HTTPException(404, "视频仍在准备中。")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/poster")
def poster(job_id: str):
    get_job(job_id)
    path = store.job_dir(job_id) / "poster.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


def timestamp(seconds, srt=False):
    milliseconds = max(0, int(round(seconds * 1000)))
    h, rem = divmod(milliseconds, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02}{',' if srt else '.'}{ms:03}"


@app.get("/api/jobs/{job_id}/subtitles")
def subtitles(job_id: str, mode: str = "translated", format: str = "vtt"):
    get_job(job_id)
    if mode not in ("translated", "bilingual", "original") or format not in ("vtt", "srt"):
        raise HTTPException(422, "字幕格式无效。")
    lines = [] if format == "srt" else ["WEBVTT", ""]
    for i, seg in enumerate(pipeline.subtitle_data(job_id)):
        translated = seg["translation"]
        if mode == "original":
            text = seg["text"]
        elif mode == "bilingual":
            text = (translated + "\n" if translated else "") + seg["text"]
        else:
            text = translated
        if not text:
            continue
        text = text.replace("-->", "→").replace("<", "‹").replace(">", "›")
        lines += [str(i + 1), f"{timestamp(seg['start'], format == 'srt')} --> {timestamp(seg['end'], format == 'srt')}", text, ""]
    return PlainTextResponse("\n".join(lines), media_type="text/vtt" if format == "vtt" else "application/x-subrip", headers={"Content-Disposition": f'attachment; filename="subtitles-{mode}.{format}"'})


@app.post("/api/jobs/{job_id}/clear-media")
def clear_media(job_id: str):
    item = get_job(job_id)
    if pipeline.ACTIVE_JOB == job_id:
        raise HTTPException(409, "当前步骤仍在结束中，请稍后再清理。")
    if item["status"] in ("queued", "processing", "uploading"):
        raise HTTPException(409, "请先暂停任务，再清理媒体缓存。")
    folder = store.job_dir(job_id).resolve()
    count = 0
    for path in folder.iterdir():
        # Delete only app-owned media, never originals or transcript/history.
        if path.is_file() and path.suffix.lower() not in (".json",):
            if path.resolve().parent != folder:
                continue
            count += path.stat().st_size
            path.unlink()
    store.update_job(job_id, playable=False, source_file=None, media_cleared=True)
    return {"ok": True, "bytes": count}


app.include_router(library_features.router)
app.include_router(cover_features.router)
app.mount("/", StaticFiles(directory=store.ROOT / "static", html=True), name="static")
