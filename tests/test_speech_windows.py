import json
from types import SimpleNamespace

import numpy as np
import pytest
import media
import pipeline
import speech_windows as sw
import store
from test_app import client, create_job, HEADERS


def test_real_silence_stays_empty_after_short_window_recheck():
    assert sw.speech_windows(np.zeros(16000*70, dtype=np.float32)) == []


def test_reset_windows_recover_tail_and_overlap_is_not_duplicated(monkeypatch):
    rate = 100
    monkeypatch.setattr(sw, 'RATE', rate)
    audio = np.arange(130*rate)
    calls = []
    def detector(chunk, options):
        start = int(chunk[0])
        calls.append((start,len(chunk)))
        # Simulate a detector that only finds these words with a short context.
        if len(chunk)>12*rate:
            return []
        result=[]
        for left,right in [(59*rate,61*rate),(125*rate,126*rate)]:
            a,b=max(start,left),min(start+len(chunk),right)
            if b>a: result.append({'start':a-start,'end':b-start})
        return result
    monkeypatch.setattr(sw, 'get_speech_timestamps', detector)
    assert sw.speech_windows(audio) == [[5900,6100],[12500,12600]]
    assert max(length for _,length in calls)<=62*rate


def test_vad_checks_cancellation_between_windows():
    def cancel(): raise media.Cancelled()
    with pytest.raises(media.Cancelled):
        sw.speech_windows(np.zeros(16000,dtype=np.float32),cancel)


def test_partially_overlapping_phrase_keeps_uncovered_words():
    segment=SimpleNamespace(words=[SimpleNamespace(start=0,end=1,word='Saved.'),
                                  SimpleNamespace(start=1.5,end=2,word=' Missed'),
                                  SimpleNamespace(start=2,end=2.5,word=' words.')])
    assert pipeline.uncovered_segments(segment,[{'start':0,'end':1}])==[(1.5,2.5,'Missed words.')]


def test_user_confirmed_dialogue_reaches_asr_even_when_vad_misses(monkeypatch):
    import faster_whisper.audio
    monkeypatch.setattr(faster_whisper.audio,'decode_audio',lambda *a,**k:np.zeros(16000*10,dtype=np.float32))
    monkeypatch.setattr(pipeline,'speech_windows',lambda *a:[])
    calls=[]
    class Model:
        def transcribe(self,audio,**options):
            calls.append(options)
            return iter([]),SimpleNamespace(language='en')
    pipeline.recognize(Model(),'unused.wav',language='en',offset=2,confirmed_ranges=[[4,8]])
    assert calls[0]['clip_timestamps']==[2,6]
    assert calls[0]['hallucination_silence_threshold']==2


def test_recheck_backs_up_appends_unique_ids_and_preserves_paid_rows(client, monkeypatch):
    item,folder=create_job(status='needs_translation')
    store.update_job(item['id'],duration=60,segment_count=2,translated_count=1)
    (folder/'audio.wav').write_bytes(b'stub')
    state={'complete':True,'language':'en','segments':[
        dict(id=0,start=0,end=2,text='Existing.'),dict(id=1,start=50,end=52,text='Ending.')]}
    store.atomic_json(folder/'transcript.json',state)
    store.atomic_json(folder/'translated.json',{'lines':{'0':'原有译文'}})
    before=(folder/'translated.json').read_bytes()
    response=client.post(f"/api/jobs/{item['id']}/recheck-speech",headers=HEADERS)
    assert response.status_code==200
    assert list((folder/'backups').glob('*/transcript.json'))
    monkeypatch.setattr(pipeline,'load_model',lambda *a:None)
    def recognize(*a,**k):
        return iter([SimpleNamespace(start=0,end=2,text='Existing.'),
                     SimpleNamespace(start=20,end=22,text='Recovered.',avg_logprob=-.1)]),SimpleNamespace(language='en')
    monkeypatch.setattr(pipeline,'recognize',recognize)
    pipeline.execute(item['id'])
    saved=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    assert [s['id'] for s in saved['segments']]==[0,2,1]
    assert (folder/'translated.json').read_bytes()==before
    assert store.job(item['id'])['translation_requested'] is False
    # Repeating the check does not append duplicates or discard translations.
    client.post(f"/api/jobs/{item['id']}/recheck-speech",headers=HEADERS)
    pipeline.execute(item['id'])
    assert len(json.loads((folder/'transcript.json').read_text(encoding='utf-8'))['segments'])==3
