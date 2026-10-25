import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import media
import pipeline
import store
import cover_features as covers
import chinese_text as chinese
from test_app import client, create_job, HEADERS


def test_five_real_frames_and_selection_preserves_video(client):
    item, folder = create_job(status='awaiting_processing')
    media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=12', '-t', '3', '-c:v', 'libx264', str(folder / 'source.mp4')])
    original = (folder / 'source.mp4').read_bytes()
    url = f"/api/jobs/{item['id']}"
    result = client.post(url + '/cover-candidates', json={}, headers=HEADERS)
    assert result.status_code == 200, result.text
    batch = result.json()
    assert len(batch['frames']) == 5
    assert sorted(f['time'] for f in batch['frames']) == [f['time'] for f in batch['frames']]
    for f in batch['frames']:
        assert 0 < f['time'] < 3
        assert client.get(f['url']).content.startswith(b'\xff\xd8')
    image = client.get(batch['frames'][2]['url']).content
    result = client.post(url + '/cover', json={'generation': batch['generation'], 'index': 2}, headers=HEADERS)
    assert result.status_code == 200
    assert (folder / 'poster.jpg').read_bytes() == image
    assert (folder / 'source.mp4').read_bytes() == original
    saved = store.job(item['id'])
    assert saved['cover_manual'] and saved['cover_revision']
    assert saved['status'] == 'awaiting_processing' and not saved['translation_requested']
    assert client.post(url + '/cover', json={'generation': '../outside', 'index': 8}, headers=HEADERS).status_code == 422


def test_cover_requires_download_and_serializes_extraction(client, monkeypatch):
    item, folder = create_job(status='awaiting_download')
    (folder / 'source.mp4').unlink()
    url=f"/api/jobs/{item['id']}/cover-candidates"
    assert client.post(url, json={}, headers=HEADERS).status_code == 409
    monkeypatch.setattr(pipeline, 'COVERING', {item['id']})
    assert client.post(url, json={}, headers=HEADERS).status_code == 409
    import library_features
    client.post('/api/jobs/delete', json={'ids':[item['id']]}, headers=HEADERS)
    assert folder.exists()
    pipeline.COVERING.clear()
    assert item['id'] in library_features.cleanup_deleted()
    assert client.post(url, json={}, headers=HEADERS).status_code == 404


def test_simplification_language_guard_and_backup(client):
    assert chinese.simplified('時間還早，等於十，明天見。', 'zh') == '时间还早，等于十，明天见。'
    assert chinese.simplified('日本の図書館と體育', 'ja') == '日本の図書館と體育'
    assert chinese.simplified('Hello world.', 'en') == 'Hello world.'
    item, folder = create_job(status='needs_translation', source_language='zh')
    data={'language':'zh','complete':True,'segments':[{'id':7,'start':2,'end':5,'text':'沒想到這麼快。'}], 'review_segments':[{'text':'時間'}]}
    store.atomic_json(folder/'transcript.json', data)
    paid=b'{"lines":{"7":"Already paid translation"}}'
    (folder/'translated.json').write_bytes(paid)
    assert chinese.migrate_existing() == [item['id']]
    result=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    assert result['segments']==[{'id':7,'start':2,'end':5,'text':'没想到这么快。'}]
    assert result['review_segments'][0]['text']=='时间'
    backups=list((folder/'backups').glob('simplified-*/transcript.json'))
    assert len(backups)==1 and json.loads(backups[0].read_text(encoding='utf-8'))==data
    assert chinese.migrate_existing()==[]
    assert (folder/'translated.json').read_bytes()==paid


def test_main_asr_and_recheck_save_simplified(client,monkeypatch):
    item,folder=create_job()
    monkeypatch.setattr(media, 'prepare', lambda *args:10)
    monkeypatch.setattr(media, 'run', lambda *args,**kwargs:'')
    monkeypatch.setattr(pipeline,'load_model',lambda *args:object())
    monkeypatch.setattr(pipeline,'recognize',lambda *args,**kwargs:(iter([SimpleNamespace(start=0,end=2,text='大家明天見。')]),SimpleNamespace(language='zh')))
    pipeline.execute(item['id'])
    data=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    assert data['segments'][0]['text']=='大家明天见。'
    monkeypatch.setattr(pipeline,'recognize',lambda *args,**kwargs:(iter([SimpleNamespace(start=5,end=7,text='時間還早。')]),SimpleNamespace(language='zh')))
    pipeline.recheck_speech(item['id'],data,folder,lambda:None,lambda *args:None)
    data=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    assert data['segments'][-1]['text']=='时间还早。'
    assert store.job(item['id'])['status']=='needs_translation'
