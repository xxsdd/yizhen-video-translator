import json
from types import SimpleNamespace
import numpy as np
import faster_whisper.audio
from faster_whisper.vad import get_speech_timestamps, VadOptions
import media
import pipeline
import store
from test_app import client, create_job
from test_app import HEADERS
import app
import pytest


@pytest.fixture(autouse=True)
def synthetic_audio_for_pipeline_mocks(monkeypatch):
    monkeypatch.setattr(faster_whisper.audio, 'decode_audio', lambda *a, **k: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(pipeline, 'speech_windows', lambda audio, check: [[0, len(audio)]])


def test_background_silence_does_not_pass_speech_gate():
    audio = np.zeros(16000 * 8, dtype=np.float32)
    assert get_speech_timestamps(audio, VadOptions(**pipeline.SPEECH_OPTIONS)) == []


def test_resume_keeps_speech_filter_and_original_word_timeline(client, monkeypatch):
    item, folder = create_job()
    store.atomic_json(folder / 'transcript.json', {
        'complete': False, 'language': 'en',
        'segments': [{'id': 0, 'start': 0, 'end': 10, 'text': 'Saved.'}],
    })
    audio = np.zeros(16000 * 30, dtype=np.float32)
    monkeypatch.setattr(faster_whisper.audio, 'decode_audio', lambda *a, **k: audio)
    monkeypatch.setattr(media, 'prepare', lambda *a: 30)
    monkeypatch.setattr(media, 'run', lambda *a, **k: '')
    class Model:
        def transcribe(self, remaining, **options):
            assert remaining.shape == (16000 * 20,)
            assert options['vad_filter'] is False  # Precomputed bounded VAD, not bypassed.
            assert options['clip_timestamps'] == [0, 20]
            segment = SimpleNamespace(start=5, end=7, text='Quiet ending.', words=[
                SimpleNamespace(start=5, end=6, word=' Quiet'),
                SimpleNamespace(start=6, end=7, word=' ending.'),
            ])
            return iter([segment]), SimpleNamespace(language='en')
    monkeypatch.setattr(pipeline, 'load_model', lambda *a: Model())
    pipeline.execute(item['id'])
    saved = json.loads((folder / 'transcript.json').read_text(encoding='utf-8'))
    assert saved['segments'][-1] == {'id': 1, 'start': 15, 'end': 17, 'text': 'Quiet ending.'}
    assert saved['processed_until'] == 30
    assert store.job(item['id'])['status'] == 'needs_translation'
    assert not (folder / 'translated.json').exists()


def test_fixed_language_and_explicit_multilingual_are_distinct():
    calls = []
    class Model:
        def transcribe(self, *args, **kwargs):
            calls.append(kwargs)
            return iter([]), SimpleNamespace(language='ru')
    pipeline.recognize(Model(), 'unused.wav', language='ru')
    pipeline.recognize(Model(), 'unused.wav', multilingual=True)
    assert calls[0]['language'] == 'ru' and calls[0]['multilingual'] is False
    assert calls[1]['language'] is None and calls[1]['multilingual'] is True


def test_uncertain_output_quarantined_and_not_translated(client, monkeypatch):
    item, folder = create_job()
    monkeypatch.setattr(media, 'prepare', lambda *a: 30)
    monkeypatch.setattr(media, 'run', lambda *a, **k: '')
    class Model:
        def transcribe(self, *a, **k):
            return iter([
                SimpleNamespace(start=0,end=2,text='Real dialogue.',avg_logprob=-0.1),
                SimpleNamespace(start=10,end=12,text='Imagined dialogue.',avg_logprob=-1.5),
            ]), SimpleNamespace(language='en')
    monkeypatch.setattr(pipeline, 'load_model', lambda *a: Model())
    pipeline.execute(item['id'])
    detail = client.get(f"/api/jobs/{item['id']}").json()
    assert [s['text'] for s in detail['segments']] == ['Real dialogue.']
    assert len(detail['review_segments']) == 1
    assert store.job(item['id'])['asr_review_count'] == 1
    assert not (folder/'translated.json').exists()


def test_language_drift_quarantined_but_multilingual_preserved():
    segment = SimpleNamespace(text='After walking through the hall they stand on the floor.')
    assert pipeline.recognition_issue(segment, 'ru') is not None
    assert pipeline.recognition_issue(segment, None) is None
    assert pipeline.recognition_issue(SimpleNamespace(text='Hello!'), 'ru') is None


def test_pause_between_words_not_shown_as_continuous_subtitle():
    segment = SimpleNamespace(words=[SimpleNamespace(start=1,end=2,word='Hello'),
                                     SimpleNamespace(start=4,end=5,word=' again')])
    assert pipeline.readable_segments(segment) == [(1,2,'Hello'),(4,5,'again')]


def test_multilingual_choice_survives_retry(client):
    item = app.new_job('test', app.options('deepseek', 'zh', 'multi'), kind='link', url='https://example.com/video')
    item.update(status='paused', source_language='ru')
    store.save_job(item)
    response = client.post(f"/api/jobs/{item['id']}/retry", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert store.job(item['id'])['recognition_language'] == 'multi'
    assert store.job(item['id'])['source_language'] == 'ru'
