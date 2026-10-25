"""Pick a local video frame as the library cover without touching the video."""
import json
import math
import random
import re
import shutil
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import media
import pipeline
import store

router = APIRouter(prefix='/api/jobs')


def item_and_folder(job_id):
    try:
        item = store.job(job_id)
        if item.get('delete_requested'):
            raise KeyError(job_id)
    except (KeyError, ValueError):
        raise HTTPException(404, '找不到这个视频。')
    folder = store.DATA / 'jobs' / job_id
    if folder.resolve().parent != (store.DATA / 'jobs').resolve() or folder.is_symlink():
        raise HTTPException(400, '视频目录异常。')
    return item, folder


def frame_times(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('无法读取视频时长。')
    # Sample across five separate portions, avoiding opening/closing black frames.
    return [duration * (.03 + .94 * (i + random.random()) / 5) for i in range(5)]


@router.post('/{job_id}/cover-candidates')
def generate(job_id: str):
    with store.LOCK:
        item, folder = item_and_folder(job_id)
        if job_id in pipeline.COVERING:
            raise HTTPException(409, '正在抽取封面，请稍等。')
        source = folder / (item.get('source_file') or 'source.mp4')
        if not source.is_file():
            source = folder / 'playback.mp4'
        if not source.is_file():
            raise HTTPException(409, '请先完成视频下载，再选择封面。')
        if source.resolve().parent != folder.resolve():
            raise HTTPException(400, '视频文件路径异常。')
        pipeline.COVERING.add(job_id)
    generation = uuid.uuid4().hex
    batch = folder / 'covers' / generation
    def check():
        item_and_folder(job_id)  # Deletion interrupts extraction; pause does not.
        if pipeline.STOP.is_set():
            raise media.Cancelled()
    try:
        batch.mkdir(parents=True)
        metadata = json.loads(media.run(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(source)], check, timeout=30))
        times = frame_times(float(metadata.get('format', {}).get('duration', 0)))
        for index, timestamp in enumerate(times):
            check()
            media.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', f'{timestamp:.6f}', '-i', str(source), '-map', '0:v:0', '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '3', str(batch / f'{index}.jpg')], check, timeout=45)
            if not (batch / f'{index}.jpg').is_file():
                raise ValueError('没有抽取到画面，请换一批重试。')
        with store.LOCK:
            check()
            store.atomic_json(batch / 'frames.json', times)
            # Bound preview storage, retaining the previous batch for other open tabs.
            batches = sorted((p for p in batch.parent.iterdir() if p.is_dir() and re.fullmatch('[0-9a-f]{32}', p.name) and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in batches[2:]:
                if old.resolve().parent == batch.parent.resolve():
                    shutil.rmtree(old, ignore_errors=True)
        return {'generation': generation, 'frames': [{'index': i, 'time': t, 'url': f'/api/jobs/{job_id}/cover-candidates/{generation}/{i}'} for i, t in enumerate(times)]}
    except HTTPException:
        raise
    except (RuntimeError, ValueError, OSError, media.Cancelled) as exc:
        raise HTTPException(400, f'封面抽取失败：{exc or "服务正在停止，请稍后重试"}')
    finally:
        with store.LOCK:
            pipeline.COVERING.discard(job_id)
        pipeline.WAKE.set()


def candidate(job_id, generation, index):
    _, folder = item_and_folder(job_id)
    if not re.fullmatch('[0-9a-f]{32}', generation) or index not in range(5):
        raise HTTPException(404, '找不到候选封面。')
    path = folder / 'covers' / generation / f'{index}.jpg'
    if not path.is_file() or not (path.parent / 'frames.json').is_file():
        raise HTTPException(404, '这批封面已过期，请换一批。')
    return folder, path


@router.get('/{job_id}/cover-candidates/{generation}/{index}')
def image(job_id: str, generation: str, index: int):
    _, path = candidate(job_id, generation, index)
    return FileResponse(path, media_type='image/jpeg')


class CoverChoice(BaseModel):
    generation: str = Field(pattern='^[0-9a-f]{32}$')
    index: int = Field(ge=0, le=4)


@router.post('/{job_id}/cover')
def choose(job_id: str, body: CoverChoice):
    with store.LOCK:
        folder, path = candidate(job_id, body.generation, body.index)
        times = json.loads((path.parent / 'frames.json').read_text(encoding='utf-8'))
        temporary = folder / 'selected-cover.tmp'
        shutil.copyfile(path, temporary)
        temporary.replace(folder / 'poster.jpg')
        revision = uuid.uuid4().hex
        store.update_job(job_id, cover_revision=revision, cover_time=times[body.index], cover_manual=True)
    return {'ok': True, 'cover_revision': revision}
