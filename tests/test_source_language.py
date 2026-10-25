import json
from types import SimpleNamespace

import pytest
import pipeline
import store
from test_app import client,create_job,HEADERS


def prepared_job():
    item,folder=create_job(status='completed',recognition_language='en',source_language='en',segment_count=1,translated_count=1,progress=100,playable=True)
    (folder/'audio.wav').write_bytes(b'local-audio')
    store.atomic_json(folder/'transcript.json',{'segments':[{'id':3,'start':0,'end':1,'text':'Hello'}],'complete':True,'language':'en'})
    store.atomic_json(folder/'translated.json',{'lines':{'3':'你好'},'context':{'old':'glossary'}})
    (folder/'versions').mkdir(exist_ok=True)
    store.atomic_json(folder/'versions'/'ja.json',{'lines':{'3':'こんにちは'}})
    return item,folder


def test_source_change_backs_up_all_versions_and_never_starts_translation(client):
    item,folder=prepared_job()
    response=client.post(f'/api/jobs/{item["id"]}/source',json={'source_language':'ru'},headers=HEADERS)
    assert response.status_code==200,response.text
    saved=store.job(item['id'])
    assert saved['recognition_language']=='ru' and saved['status']=='queued'
    assert not saved['translation_requested'] and saved['translated_count']==0
    transcript=json.loads((folder/'transcript.json').read_text('utf-8'))
    assert transcript['id_offset']==4 and not transcript['complete'] and not transcript['segments']
    assert json.loads((folder/'translated.json').read_text())=={}
    backup=next((folder/'backups').glob('source-language-*'))
    assert json.loads((backup/'translated.json').read_text('utf-8'))['lines']=={'3':'你好'}
    assert (backup/'versions'/'ja.json').exists() and not (folder/'versions').exists()


@pytest.mark.parametrize('mode,status', [('xx',422),('ru',409)])
def test_invalid_or_busy_source_change_preserves_existing_subtitles(client,mode,status):
    item,folder=prepared_job()
    if status==409:store.update_job(item['id'],status='processing')
    original=(folder/'transcript.json').read_bytes()
    response=client.post(f'/api/jobs/{item["id"]}/source',json={'source_language':mode},headers=HEADERS)
    assert response.status_code==status
    assert (folder/'transcript.json').read_bytes()==original
    assert not (folder/'backups').exists()


def test_changed_language_recognition_uses_new_id_range(client,monkeypatch):
    item,folder=prepared_job()
    assert client.post(f'/api/jobs/{item["id"]}/source',json={'source_language':'ru'},headers=HEADERS).status_code==200
    monkeypatch.setattr(pipeline.media,'prepare',lambda *a:20)
    monkeypatch.setattr(pipeline.media,'run',lambda *a,**k:'')
    monkeypatch.setattr(pipeline,'load_model',lambda *a:object())
    def recognize(model,path,language,offset,multi,check,ranges):
        assert language=='ru' and not multi and offset==0
        return iter([SimpleNamespace(start=1,end=2,text='Привет.')]),SimpleNamespace(language='ru',language_probability=.99)
    monkeypatch.setattr(pipeline,'recognize',recognize)
    pipeline.execute(item['id'])
    source=json.loads((folder/'transcript.json').read_text('utf-8'))
    assert source['segments'][0]['id']==4 and source['language']=='ru'
    assert store.job(item['id'])['status']=='needs_translation'
    assert json.loads((folder/'translated.json').read_text())=={}
