"""Local script normalization for Chinese ASR; no model requests."""
from functools import lru_cache
import copy
import json
import shutil
import time

import store


@lru_cache(maxsize=1)
def converter():
    from opencc import OpenCC
    return OpenCC('t2s')


def simplified(text, language):
    # Never infer Chinese from Han characters: Japanese uses them too.
    if language and language.lower().split('-')[0] in ('zh', 'yue'):
        return converter().convert(text)
    return text


def normalize_transcript(source, fallback_language=None):
    result = copy.deepcopy(source)
    language = source.get('language') or fallback_language
    for key in ('segments', 'review_segments'):
        for row in result.get(key, []):
            row['text'] = simplified(row.get('text', ''), row.get('language') or language)
    return result


def normalize_saved(path, source, fallback_language=None):
    normalized = normalize_transcript(source, fallback_language)
    if normalized != source:
        backup = path.parent / 'backups' / f'simplified-{time.time_ns()}'
        backup.mkdir(parents=True)
        shutil.copy2(path, backup / path.name)
        store.atomic_json(path, normalized)
    return normalized


def migrate_existing():
    """Idle-job migration preserves IDs, timings, translations and a raw backup."""
    import pipeline
    changed = []
    with store.LOCK:
        for item in store.jobs():
            if item.get('delete_requested') or item['id'] == pipeline.ACTIVE_JOB or item['status'] in ('queued', 'processing', 'uploading'):
                continue
            # Older mixed-language transcripts have no per-row language evidence.
            if item.get('recognition_language') == 'multi':
                continue
            path = store.DATA / 'jobs' / item['id'] / 'transcript.json'
            if not path.is_file():
                continue
            source = json.loads(path.read_text(encoding='utf-8'))
            normalized = normalize_saved(path, source, item.get('source_language'))
            if normalized == source:
                continue
            changed.append(item['id'])
    return changed
