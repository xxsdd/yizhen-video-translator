import gc
import hashlib
import json
import os
import site
import threading
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import media
import store
import codex_bridge
from chinese_text import simplified, normalize_saved
from translate import ProviderError, build_context, chunks, translate_block, translation_blocks
from subtitle_sentences import sentence_rows, display_rows
from speech_windows import speech_windows, merge_windows

STOP = threading.Event()
WAKE = threading.Event()
MODEL_LOCK = threading.Lock()
MODEL = None
MODEL_TAG = None
ACTIVE_JOB = None
PREVIEWING = set()
UPLOADING = set()
COVERING = set()
MODEL_STATUS = {"state": "idle", "message": "首次使用需要准备本地识别模型"}
DLL_HANDLES = []

# A lower gate than Silero's default retains soft dialogue, while removing
# background-only windows before Whisper can invent words in them.
SPEECH_OPTIONS = {"threshold": 0.35, "min_speech_duration_ms": 150,
                  "min_silence_duration_ms": 500, "speech_pad_ms": 300}


def recognize(model, audio_path, language=None, offset=0, multilingual=False, check=lambda: None, confirmed_ranges=()):
    from faster_whisper.audio import decode_audio
    from types import SimpleNamespace
    audio = decode_audio(str(audio_path), sampling_rate=16000)[round(offset * 16000):]
    windows = speech_windows(audio, check)
    # User-confirmed dialogue is always sent to recognition, while confidence
    # checks still apply. These explicit ranges are not evidence of VAD quality.
    windows = merge_windows(windows + [[max(0, round((start-offset)*16000)),
                                       min(len(audio), round((end-offset)*16000))]
                                      for start,end in confirmed_ranges if end>offset])
    if not windows:
        return iter([]), SimpleNamespace(language=language or 'und', language_probability=1)
    # Explicit clips use the original timeline. VAD has already run locally in
    # bounded windows, so disabling the library's whole-file VAD is intentional.
    return model.transcribe(audio, beam_size=5, vad_filter=False,
                            clip_timestamps=[v / 16000 for window in windows for v in window],
                            condition_on_previous_text=False, language=language,
                            word_timestamps=True, temperature=0,
                            hallucination_silence_threshold=2.0,
                            multilingual=multilingual, language_detection_segments=5,
                            language_detection_threshold=0.8)


def recognition_issue(segment, language=None):
    """Quarantine uncertain output; probabilities are signals, not proof of speech."""
    logprob = getattr(segment, 'avg_logprob', 0)
    if logprob < -1.0:
        return '识别可信度不足'
    if getattr(segment, 'no_speech_prob', 0) > 0.6 and logprob < -0.5:
        return '可能是非对白声音'
    if getattr(segment, 'compression_ratio', 0) > 2.4:
        return '出现异常重复'
    words = getattr(segment, 'words', None) or []
    probabilities = [w.probability for w in words if hasattr(w, 'probability')]
    if probabilities and sum(probabilities) / len(probabilities) < 0.35:
        return '词语识别可信度不足'
    # Detect major script drift in fixed-language mode, not isolated names.
    letters = [c for c in segment.text if c.isalpha()]
    if language in ('ru', 'uk', 'bg') and len(letters) >= 20:
        if sum('\u0400' <= c <= '\u04ff' for c in letters) / len(letters) < 0.25:
            return '输出语言与固定原语言不一致'
    return None


def excluded_interval(start, end, ranges):
    return any(start < stop and end > begin for begin, stop in ranges)


def dll_paths():
    if os.name == "nt":
        for root in site.getsitepackages():
            for folder in (Path(root) / "nvidia").glob("*/bin"):
                if folder.exists():
                    DLL_HANDLES.append(os.add_dll_directory(str(folder)))
                    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


dll_paths()


def load_model(name="large-v3", device="cuda"):
    global MODEL, MODEL_TAG
    with MODEL_LOCK:
        if MODEL is not None and MODEL_TAG == (name, device):
            return MODEL
        MODEL_STATUS.update(state="loading", message="正在准备识别模型，首次下载约 3 GB，请保持网络连接")
        try:
            from faster_whisper import WhisperModel
            if MODEL is not None:
                MODEL = None
                gc.collect()
            MODEL = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8", download_root=str(store.DATA / "models"), cpu_threads=8, num_workers=1)
            MODEL_TAG = (name, device)
            MODEL_STATUS.update(state="ready", message=f"{name} · {'GPU 加速' if device == 'cuda' else 'CPU'} 已就绪")
            return MODEL
        except Exception as exc:
            MODEL_STATUS.update(state="error", message="识别模型准备失败，请检查网络和 GPU 运行环境，或在设置中选择 CPU。")
            raise RuntimeError(MODEL_STATUS["message"]) from exc


def warmup():
    config = store.settings()
    try:
        load_model(config["asr_model"], config["device"])
    except Exception:
        pass


def check_job(job_id):
    if STOP.is_set() or store.job(job_id)["status"] in ("paused", "cancelled", "deleting"):
        raise media.Cancelled()


def execute(job_id):
    with store.LOCK:
        item = store.job(job_id)
        if item["status"] != "queued":
            return
        store.update_job(job_id, status="processing", message="正在准备视频", error=None, translation_activity=None)
    folder = store.job_dir(job_id)
    check = lambda: check_job(job_id)

    def progress(message, percent):
        check()
        store.update_job(job_id, message=message, progress=percent)

    transcription = folder / "transcript.json"
    state = json.loads(transcription.read_text(encoding="utf-8")) if transcription.exists() else {"segments": [], "complete": False, "language": None}
    if item.get('recognition_language') != 'multi':
        state = normalize_saved(transcription, state, item.get('source_language'))
    duration = item.get("duration") or 0
    if state['complete'] and item.get('speech_recheck_requested'):
        recheck_speech(job_id, state, folder, check, progress)
        return
    if not state["complete"]:
        check()
        source = folder / item["source_file"] if item.get("source_file") else None
        if source is None or not source.exists():
            metadata_seen = False
            def metadata(info):
                nonlocal metadata_seen
                check()
                if metadata_seen:
                    return
                metadata_seen = True
                fields = {}
                if info.get('title'):
                    fields['title'] = str(info['title'])[:500]
                if isinstance(info.get('duration'), (int, float)):
                    fields['duration'] = info['duration']
                store.update_job(job_id, **fields)
                media.save_poster(info.get('thumbnail'), folder, check)
            media.save_poster(item.get('browser_poster_url'), folder, check)
            source, title = media.download(item["url"], folder, check, progress, on_metadata=metadata,
                                          referer=item.get('browser_referer'))
            store.update_job(job_id, source_file=source.name, title=title)
        check()
        if item.get('run_mode') == 'download':
            store.update_job(job_id, status='awaiting_processing', message='下载完成，点击开始处理识别字幕',
                             progress=18, translation_requested=False)
            return
        duration = media.prepare(source, folder, check, progress)
        store.update_job(job_id, duration=duration, playable=True)
        poster = folder / "poster.jpg"
        if not poster.exists():
            try:
                automatic_poster = folder / 'automatic-poster.jpg'
                media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", str(min(5, duration / 3)), "-i", str(folder / "playback.mp4"), "-frames:v", "1", "-vf", "scale=640:-2", str(automatic_poster)], check, timeout=60)
                with store.LOCK:
                    if not poster.exists():
                        automatic_poster.replace(poster)
                    else:
                        automatic_poster.unlink(missing_ok=True)
            except media.Cancelled:
                raise
            except Exception:
                pass
    if not state["complete"]:
        config = store.settings()
        progress("正在加载高精度语音识别模型", 27)
        model = load_model(config["asr_model"], config["device"])
        check()
        progress("正在识别对白", 28)
        offset = max(state.get('processed_until', 0), state["segments"][-1]["end"] if state["segments"] else 0)
        mode = item.get('recognition_language', item.get('source_language', ''))
        multilingual = mode == 'multi'
        language = None if multilingual else mode or state.get('language')
        segments, info = recognize(model, folder / "audio.wav",
                                   language, offset, multilingual, check, item.get('confirmed_speech_ranges', []))
        state["language"] = info.language
        state['language_probability'] = getattr(info, 'language_probability', 1)
        if not mode and state['language_probability'] < 0.7:
            raise RuntimeError('原语言判断不可靠，已停止生成字幕。请重新导入并明确选择原语言；真实双语视频可选择多语言模式。')
        review = state.setdefault('review_segments', [])
        last_save = time.monotonic()
        for s in segments:
            check()
            issue = recognition_issue(s, None if multilingual else info.language)
            for start, end, text in readable_segments(s):
                text = simplified(text, getattr(s, 'language', None) if multilingual else info.language)
                start, end = start + offset, end + offset
                if text and end > start and start < duration:
                    if excluded_interval(start, end, item.get('non_dialogue_ranges', [])):
                        continue
                    if issue:
                        review.append({'start':round(start,3),'end':round(min(duration,end),3),'text':text,'reason':issue})
                        continue
                    state["segments"].append({"id": state.get("id_offset",0) + len(state["segments"]), "start": round(start, 3), "end": round(min(duration, end), 3), "text": text})
            state['processed_until'] = min(duration, s.end + offset)
            if time.monotonic() - last_save > 2:
                store.atomic_json(transcription, state)
                store.update_job(job_id, source_language=info.language, segment_count=len(state["segments"]))
                progress("正在识别对白", min(69, 28 + 41 * (s.end + offset) / duration))
                last_save = time.monotonic()
        state["complete"] = True
        state["processed_until"] = duration
        state["recognition_mode"] = "speech_window_v5"
        store.atomic_json(transcription, state)
        store.update_job(job_id, source_language=info.language, segment_count=len(state["segments"]), asr_review_count=len(review))
    check()
    if not state["segments"]:
        raise RuntimeError("未识别到对白。请确认视频里有人声，或尝试关闭背景噪声后重新导入。")
    item = store.job(job_id)
    if not item.get("translation_requested", False):
        store.update_job(job_id, status="needs_translation", message="原文字幕已就绪；点击开始翻译才会使用 AI", progress=70)
        return
    config = store.provider(item["provider_id"])
    config["model"] = item["model"]
    config["reasoning_effort"] = item.get("reasoning_effort", config.get("reasoning_effort", ""))
    config["base_url"] = item["base_url"]
    if (config["id"] != "chatgpt" and not config["api_key"]) or not config["model"]:
        store.update_job(job_id, status="needs_config", message="原文字幕已就绪，请配置 AI 服务后继续翻译", progress=70)
        return
    signature = hashlib.sha256(json.dumps([config["base_url"], config["model"], item["target"]]).encode()).hexdigest()
    translated_path = folder / "translated.json"
    translated = json.loads(translated_path.read_text(encoding="utf-8")) if translated_path.exists() else {}
    if translated.get("signature") != signature:
        translated = {"signature": signature, "lines": {}, "context_index": 0, "context": {}}
    source = state["segments"]
    if all(translated['lines'].get(str(s['id'])) for s in source):
        store.update_job(job_id, status='completed', translation_requested=False, progress=100,
                         translated_count=len(source), message='已读取保存的译文，没有调用 AI')
        return
    blocks, direct = translation_blocks(source)
    history_path = folder / "translation-requests.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    activity, last_write = {}, 0
    current_request = None

    def persist_activity(force=False):
        nonlocal last_write
        now = time.monotonic()
        if not force and now - last_write < 0.5:
            return
        last_write = now
        store.atomic_json(history_path, history)
        reported = [r["usage"] for r in history if r.get("usage")]
        totals = {k: sum(u.get(k, 0) for u in reported) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")}
        totals.update(reported_requests=len(reported), requests=len(history), reasoning_reported=any("reasoning_tokens" in u for u in reported))
        store.update_job(job_id, translation_activity=activity.copy(), translation_usage=totals)

    def event(update):
        nonlocal current_request
        now = time.time()
        if update.get("stage") == "waiting":
            current_request = {"started": now, "model": config["model"], "target": item["target"], "label": activity["label"], "status": "waiting"}
            history.append(current_request)
            activity.update(started=now, last_response=None, received_chars=0)
        if update.get("stage"):
            activity.update(stage=update["stage"])
            if current_request is not None:
                current_request["status"] = update["stage"]
        if update.get("stage") in ("thinking", "receiving"):
            activity["last_response"] = now
        if "received_chars" in update:
            activity["received_chars"] = update["received_chars"]
        if update.get('diagnostic'):
            activity['diagnostic'] = update['diagnostic']
            if current_request is not None:
                current_request['diagnostic'] = update['diagnostic']
        if isinstance(update.get("usage"), dict) and current_request is not None:
            raw = update["usage"]
            usage = {k: raw[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if type(raw.get(k)) is int and raw[k] >= 0}
            details = raw.get("completion_tokens_details") or {}
            reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if type(reasoning) is int and reasoning >= 0:
                usage["reasoning_tokens"] = reasoning
            current_request["usage"] = usage
        persist_activity(force=update.get("stage") == "waiting" or "usage" in update or 'diagnostic' in update)

    def request(label, call):
        nonlocal current_request
        current_request = None
        activity.clear()
        activity.update(label=label, stage="waiting", started=time.time())
        config["_event"] = event
        try:
            result = call()
            activity["stage"] = "saved"
            return result
        except media.Cancelled:
            activity["stage"] = "paused"
            raise
        except Exception as exc:
            activity["stage"] = "failed"
            activity['error'] = str(exc)[:500]
            if current_request is not None:
                current_request['error'] = str(exc)[:500]
            if isinstance(exc, ProviderError):
                store.update_job(job_id, last_translation_error={'message':str(exc)[:500],
                                 'diagnostic':exc.details, 'label':label, 'time':time.time()})
            raise
        finally:
            if current_request is not None:
                current_request.update(status=activity["stage"], ended=time.time())
            activity["ended"] = time.time()
            store.atomic_json(translated_path, translated)
            store.update_job(job_id, translated_count=len(translated["lines"]))
            persist_activity(force=True)

    with codex_bridge.task(config):
        if config.get("_codex_session"):
            session = config["_codex_session"]
            store.update_job(job_id, codex_account=session.account, reasoning_effort=config["reasoning_effort"])
        # Only long transcripts need a separate global glossary/context pass.
        context_blocks = [] if direct else list(chunks(source, limit=18000, max_items=180))
        for index in range(translated.get("context_index", 0), len(context_blocks)):
            label = f"整理全片上下文 {index + 1}/{len(context_blocks)}"
            progress(label, 70 + 6 * index / len(context_blocks))
            translated["context"] = request(label, lambda: build_context(config, [s["text"] for s in context_blocks[index]], item["target"], translated["context"], check))
            translated["context_index"] = index + 1
            store.atomic_json(translated_path, translated)
        for index, block in enumerate(blocks):
            check()
            missing = [s for s in block if str(s["id"]) not in translated["lines"]]
            if not missing:
                continue
            done = len(translated["lines"])
            label = "全文直接翻译" if direct else f"翻译第 {index + 1}/{len(blocks)} 批"
            progress(f"{label} · 已保存 {done}/{len(source)} 段", 76 + 23 * done / len(source))
            # Rechecked rows keep old IDs and append new IDs, so IDs are not indexes.
            positions = {s['id']: i for i, s in enumerate(source)}
            first, last = positions[missing[0]['id']], positions[missing[-1]['id']]
            missing_ids = {s["id"] for s in missing}
            neighbors = source[max(0, first-2):first] + source[last+1:last+3]
            neighbors = [{"text": s["text"], "translation": translated["lines"].get(str(s["id"]), "")} for s in neighbors]
            last_partial = 0

            def partial(lines):
                nonlocal last_partial
                translated["lines"].update({str(k): v for k, v in lines.items()})
                if time.monotonic() - last_partial >= 0.5:
                    last_partial = time.monotonic()
                    store.atomic_json(translated_path, translated)
                    count = len(translated["lines"])
                    store.update_job(job_id, translated_count=count, progress=76 + 23 * count / len(source), message=f"{label} · 已保存 {count}/{len(source)} 段")

            config["_partial"] = partial
            result = request(label, lambda: translate_block(config, missing, item["target"], translated["context"], neighbors, check))
            translated["lines"].update({str(k): v for k, v in result.items()})
            store.atomic_json(translated_path, translated)
            store.update_job(job_id, translated_count=len(translated["lines"]))
        check()
        store.update_job(job_id, status="completed", translation_requested=False, message="字幕已完成，可以开始观看", progress=100, translated_count=len(source), completed=time.time(), last_translation_error=None)



def worker():
    global ACTIVE_JOB
    # Interrupted jobs are resumed only from saved checkpoints.
    for item in store.jobs():
        if item["status"] == "processing":
            store.update_job(item["id"], status="queued", message="上次处理中断，正在等待继续")
    while not STOP.is_set():
        import library_features
        library_features.cleanup_deleted()
        pending = sorted([j for j in store.jobs() if j["status"] == "queued" and not j.get('delete_requested')],
                         key=lambda j: (-j.get('queue_priority', 0), j.get('queued_at', j['created'])))
        if not pending:
            WAKE.wait(1)
            WAKE.clear()
            continue
        item = pending[0]
        with store.LOCK:
            try:
                item = store.job(item["id"])
            except KeyError:
                continue
            if item["status"] != "queued":
                continue
            ACTIVE_JOB = item["id"]
        try:
            execute(item["id"])
        except media.Cancelled:
            if not STOP.is_set() and store.job(item["id"])["status"] == "processing":
                store.update_job(item["id"], status="paused", message="翻译已取消，已完成字幕保留")
            if STOP.is_set() and store.job(item["id"])["status"] == "processing":
                store.update_job(item["id"], status="queued", message="等待下次启动继续")
        except (RuntimeError, ProviderError, ValueError) as exc:
            if store.job(item["id"])["status"] not in ("paused", "cancelled", "deleting"):
                store.update_job(item["id"], status="failed", message="处理未完成，已保存进度", error=str(exc)[:500])
        except Exception:
            store.update_job(item["id"], status="failed", message="处理未完成，已保存进度", error="本地处理发生错误。请检查磁盘空间、模型及 GPU 运行环境后重试。")
        finally:
            with store.LOCK:
                ACTIVE_JOB = None
                stopped = store.job(item['id'])
                if stopped.get('resume_after_stop') and not stopped.get('delete_requested'):
                    store.update_job(item['id'], _control=True, status='queued', resume_after_stop=False,
                                     message='已停止上一步，等待开始所选任务')


def preview_link(job_id):
    """Read title/cover only; importing is never authorization to download media."""
    with store.LOCK:
        PREVIEWING.add(job_id)
    def check():
        if STOP.is_set() or store.job(job_id).get('delete_requested'):
            raise media.Cancelled()
    try:
        item = store.job(job_id)
        folder = store.job_dir(job_id)
        media.save_poster(item.get('browser_poster_url'), folder, check)
        info = media.preview(item['url'], check)
        check()
        store.update_job(job_id, title=str(info.get('title') or item['title'])[:500],
                         duration=info.get('duration') or item.get('duration'), preview_error=None)
        media.save_poster(info.get('thumbnail'), folder, check)
    except media.Cancelled:
        pass
    except Exception:
        try:
            store.update_job(job_id, preview_error='暂未取得封面，可选择开始下载后继续尝试。')
        except KeyError:
            pass
    finally:
        with store.LOCK:
            PREVIEWING.discard(job_id)
        WAKE.set()


def recheck_speech(job_id, state, folder, check, progress):
    """Append only missing dialogue; original IDs and paid translations survive."""
    item = store.job(job_id)
    config = store.settings()
    progress('正在分段复检人声和字幕空白', 28)
    model = load_model(config['asr_model'], config['device'])
    segments, info = recognize(model, folder / 'audio.wav', state.get('language'),
                               multilingual=item.get('recognition_language') == 'multi', check=check,
                               confirmed_ranges=item.get('confirmed_speech_ranges', []))
    saved = state['segments']
    next_id = max((s['id'] for s in saved), default=-1) + 1
    reviewed = state.setdefault('review_segments', [])
    last_save = time.monotonic()
    added = 0
    duration = item['duration']
    for segment in segments:
        check()
        issue = recognition_issue(segment, None if item.get('recognition_language') == 'multi' else info.language)
        for start, end, text in uncovered_segments(segment, saved):
            text = simplified(text, getattr(segment, 'language', None) if item.get('recognition_language') == 'multi' else info.language)
            if not text or end <= start or start >= duration or excluded_interval(start, end, item.get('non_dialogue_ranges', [])):
                continue
            if any(start < old['end'] and end > old['start'] for old in saved):
                continue
            if issue:
                if not any(abs(r['start'] - start) < .2 for r in reviewed):
                    reviewed.append(dict(start=round(start,3), end=round(min(end,duration),3), text=text, reason=issue))
                continue
            saved.append(dict(id=next_id, start=round(start,3), end=round(min(end,duration),3), text=text))
            next_id += 1
            added += 1
        if time.monotonic() - last_save > 2:
            saved.sort(key=lambda s: (s['start'], s['id']))
            store.atomic_json(folder / 'transcript.json', state)
            store.update_job(job_id, segment_count=len(saved))
            progress(f'正在复检漏识别对白 · 本次补回 {added} 段', min(69, 28 + 41 * segment.end / duration))
            last_save = time.monotonic()
    check()
    saved.sort(key=lambda s: (s['start'], s['id']))
    state['recognition_mode'] = 'speech_window_v6'
    state['rechecked'] = time.time()
    store.atomic_json(folder / 'transcript.json', state)
    store.update_job(job_id, speech_recheck_requested=False, status='needs_translation', progress=70,
                     segment_count=len(saved), asr_review_count=len(reviewed), translation_requested=False,
                     message=f'复检完成，本次补回 {added} 段；已有译文保留，新增对白需点击翻译')


def subtitle_data(job_id):
    folder = store.job_dir(job_id)
    transcript = folder / "transcript.json"
    result = json.loads(transcript.read_text(encoding="utf-8")) if transcript.exists() else {"segments": [], "complete": False}
    translated = folder / "translated.json"
    lines = json.loads(translated.read_text(encoding="utf-8")).get("lines", {}) if translated.exists() else {}
    return display_rows([{**s, "translation": lines.get(str(s["id"]), "")} for s in result["segments"]])


def readable_segments(segment):
    """Prefer complete sentences while preserving real word times and pauses."""
    words = getattr(segment, "words", None)
    if not words:
        return [(segment.start, segment.end, segment.text.strip())]
    rows = sentence_rows([{'start':w.start, 'end':w.end, 'text':w.word.strip()} for w in words if w.word.strip()])
    return [(r['start'], r['end'], r['text']) for r in rows]


def uncovered_segments(segment, saved):
    """Keep new words beside existing rows instead of discarding a whole phrase."""
    from types import SimpleNamespace
    words = getattr(segment, 'words', None)
    if not words:
        return readable_segments(segment)
    uncovered = [w for w in words if not any(w.start < old['end'] and w.end > old['start'] for old in saved)]
    if not uncovered:
        return []
    return readable_segments(SimpleNamespace(words=uncovered))
