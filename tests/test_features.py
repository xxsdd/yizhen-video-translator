import json
import sqlite3
from types import SimpleNamespace
import httpx
import pytest
import library_features as features
import pipeline
import store
from test_app import client, create_job, save_config, HEADERS


def source(folder):
    store.atomic_json(folder / "transcript.json", {"complete": True, "language": "en", "segments": [{"id": 0, "start": 0, "end": 3, "text": "Hello world."}]})


def test_configured_key_does_not_translate_import_or_retry(client, monkeypatch):
    save_config(client)
    item, folder = create_job()
    source(folder)
    monkeypatch.setattr(pipeline, "build_context", lambda *args: pytest.fail("Implicit paid request"))
    monkeypatch.setattr(pipeline, "translate_block", lambda *args: pytest.fail("Implicit paid request"))
    pipeline.execute(item["id"])
    assert store.job(item["id"])["status"] == "needs_translation"
    assert client.post(f"/api/jobs/{item['id']}/retry", json={}, headers=HEADERS).status_code == 200
    pipeline.execute(item["id"])
    assert not store.job(item["id"])["translation_requested"]


def test_explicit_translation_single_enqueue_and_cleared_media(client, monkeypatch):
    save_config(client)
    item, folder = create_job(status="needs_translation", source_language="en")
    source(folder)
    (folder / "source.mp4").unlink()
    store.update_job(item["id"], source_file=None, playable=False)
    monkeypatch.setattr(pipeline.media, "prepare", lambda *args: pytest.fail("Should reuse text without media"))
    calls=[]
    monkeypatch.setattr(pipeline,"build_context",lambda *args:{"summary":"context","terms":[]})
    monkeypatch.setattr(pipeline,"translate_block",lambda config,block,*args:calls.append(len(block)) or {s['id']:'你好世界' for s in block})
    endpoint=f"/api/jobs/{item['id']}/translate"
    assert client.post(endpoint,json={},headers=HEADERS).status_code==200
    assert client.post(endpoint,json={},headers=HEADERS).status_code==409
    pipeline.execute(item['id'])
    assert calls==[1] and not store.job(item['id'])['translation_requested']
    assert client.post(endpoint,json={},headers=HEADERS).json()['cached']
    assert store.job(item['id'])['status']=='completed'


def test_language_switch_restores_paid_version_without_queue(client):
    item,folder=create_job(status='completed',segment_count=1,translated_count=1)
    source(folder)
    store.atomic_json(folder/'translated.json',{'lines':{'0':'你好世界'},'signature':'saved-zh'})
    endpoint=f"/api/jobs/{item['id']}/target"
    assert client.post(endpoint,json={'target':'ja'},headers=HEADERS).status_code==200
    assert store.job(item['id'])['status']=='needs_translation'
    assert pipeline.subtitle_data(item['id'])[0]['translation']==''
    store.atomic_json(folder/'translated.json',{'lines':{'0':'こんにちは'},'signature':'saved-ja'})
    assert client.post(endpoint,json={'target':'zh'},headers=HEADERS).json()['cached']
    assert pipeline.subtitle_data(item['id'])[0]['translation']=='你好世界'
    assert not store.job(item['id'])['translation_requested']
    assert client.post(endpoint,json={'target':'ja'},headers=HEADERS).json()['cached']
    assert pipeline.subtitle_data(item['id'])[0]['translation']=='こんにちは'
    assert client.post(endpoint,json={'target':'../../other'},headers=HEADERS).status_code==422


@pytest.mark.parametrize('provider_id', ['deepseek', 'openai'])
def test_model_switch_preserves_paid_rows_and_only_translates_missing(client,monkeypatch,provider_id):
    import hashlib
    save_config(client)
    item,folder=create_job(status='needs_translation',segment_count=2,translated_count=1)
    store.atomic_json(folder/'transcript.json',{'complete':True,'segments':[
        {'id':0,'text':'Already saved.','start':0,'end':2},
        {'id':1,'text':'Missing.','start':3,'end':4}]})
    signature=hashlib.sha256(json.dumps([item['base_url'],item['model'],item['target']]).encode()).hexdigest()
    store.atomic_json(folder/'translated.json',{'signature':signature,'lines':{'0':'已有译文'},'context_index':0,'context':{}})
    before=(folder/'translated.json').read_bytes()
    calls=[]
    def translate(config,block,*args):
        assert config['model']=='new-model'
        assert config['id']==provider_id
        assert [s['id'] for s in block]==[1]
        calls.append(1)
        return {1:'新增译文'}
    monkeypatch.setattr(pipeline,'translate_block',translate)
    response=client.post(f"/api/jobs/{item['id']}/translate",json={'provider_id':provider_id,'model':'new-model'},headers=HEADERS)
    assert response.status_code==200 and not calls
    assert json.loads((folder/'translated.json').read_text(encoding='utf-8'))['lines']=={'0':'已有译文'}
    backup=next((folder/'backups').glob('model-switch-*/translated.json'))
    assert backup.read_bytes()==before
    pipeline.execute(item['id'])
    cached=json.loads((folder/'translated.json').read_text(encoding='utf-8'))
    assert cached['lines']=={'0':'已有译文','1':'新增译文'} and calls==[1]
    assert cached['provider_history'][0]['model']==item['model']


def test_unconfigured_switch_does_not_modify_saved_cache(client):
    item,folder=create_job(status='needs_translation',segment_count=2,translated_count=1)
    store.atomic_json(folder/'transcript.json',{'complete':True,'segments':[{'id':0,'text':'A'},{'id':1,'text':'B'}]})
    store.atomic_json(folder/'translated.json',{'lines':{'0':'已保存'}})
    before=(folder/'translated.json').read_bytes()
    assert client.post(f"/api/jobs/{item['id']}/translate",json={'provider_id':'openai','model':'model'},headers=HEADERS).status_code==400
    assert (folder/'translated.json').read_bytes()==before
    assert store.job(item['id'])['status']=='needs_translation'


def test_delete_batch_removes_owned_data_and_preserves_words(client,tmp_path):
    first,folder=create_job(status='completed')
    second,second_folder=create_job(status='queued')
    original=tmp_path/'original.mp4';original.write_bytes(b'original')
    source(folder)
    saved=client.post('/api/vocabulary',json={'word':'hello','sentence':'Hello world.','job_id':first['id'],'start':0},headers=HEADERS).json()
    result=client.post('/api/jobs/delete',json={'ids':[first['id'],second['id']]},headers=HEADERS)
    assert result.status_code==200 and len(result.json()['deleted'])==2
    assert not folder.exists() and not second_folder.exists()
    assert original.read_bytes()==b'original' and store.jobs()==[]
    words=client.get('/api/vocabulary').json()['words']
    assert len(words)==1 and words[0]['job_id'] is None and words[0]['video_deleted']
    assert client.get(f"/api/jobs/{first['id']}").status_code==404


def test_delete_waits_for_active_worker_and_validates_whole_batch(client,monkeypatch):
    first,folder=create_job(status='completed');second,second_folder=create_job(status='paused')
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',second['id'])
    assert client.post('/api/jobs/delete',json={'ids':[first['id'],'../escape']},headers=HEADERS).status_code==404
    assert folder.exists()
    result=client.post('/api/jobs/delete',json={'ids':[first['id'],second['id']]},headers=HEADERS)
    assert result.status_code==200 and not folder.exists() and second_folder.exists()
    assert result.json()['deleting']==[second['id']]
    assert client.get('/api/state').json()['jobs']==[]
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',None)
    features.cleanup_deleted()
    assert not second_folder.exists() and store.jobs()==[]


def test_dictionary_cache_saved_word_is_offline_and_ai_is_explicit(client,monkeypatch):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        return httpx.Response(200,json=[{'word':'hello','phonetic':'/hello/','meanings':[{'partOfSpeech':'noun','definitions':[{'definition':'A greeting.','example':'Hello world.'}]}],'sourceUrls':['https://en.wiktionary.org/wiki/hello'],'license':{'name':'CC BY-SA 3.0'}}],request=httpx.Request('GET',url))
    monkeypatch.setattr(features.httpx,'get',get)
    monkeypatch.setattr(features,'completion',lambda *args:pytest.fail('Dictionary lookup must not call AI'))
    assert client.get('/api/dictionary?word=hello').status_code==200
    assert client.get('/api/dictionary?word=Hello').status_code==200
    assert len(calls)==1
    body={'word':'hello','sentence':'Hello world.'}
    first=client.post('/api/vocabulary',json=body,headers=HEADERS).json()
    assert client.post('/api/vocabulary',json=body,headers=HEADERS).json()['id']==first['id']
    words=client.get('/api/vocabulary').json()['words']
    assert len(words)==1 and words[0]['dictionary']['entries'][0]['definitions'][0]['definition']=='A greeting.'
    save_config(client)
    monkeypatch.setattr(features,'completion',lambda *args:'在句中表示问候。')
    assert client.post('/api/dictionary/explain',json=body,headers=HEADERS).json()['explanation']=='在句中表示问候。'
    assert client.delete('/api/vocabulary/'+first['id'],headers=HEADERS).status_code==200
    assert client.get('/api/vocabulary').json()['words']==[]
    assert client.get('/api/dictionary?word=https://evil.example').status_code==422


def test_dictionary_unavailable_does_not_invent_definitions(client,monkeypatch):
    monkeypatch.setattr(features.httpx,'get',lambda *args,**kwargs: (_ for _ in ()).throw(httpx.ReadTimeout('offline')))
    assert client.get('/api/dictionary?word=unavailable').status_code==502


def test_bilingual_lookup_upgrades_english_cache_and_saved_words(client, monkeypatch):
    folder = store.DATA / 'dictionaries'
    folder.mkdir(exist_ok=True)
    path = folder / 'ecdict.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE entries (word TEXT PRIMARY KEY COLLATE NOCASE, phonetic TEXT, definition TEXT, translation TEXT, pos TEXT, exchange TEXT)')
    db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)', ('learn', '/lɜːn/', 'Acquire knowledge.', 'vt. 学习\\nvi. 获悉', 'v', ''))
    db.commit(); db.close()
    try:
        with store.connection() as db:
            db.execute('INSERT INTO dictionary_cache VALUES (?,?)', ('learn', json.dumps({'word':'learn','entries':[]})))
        client.post('/api/vocabulary', json={'word':'learn','sentence':'We learn English.'}, headers=HEADERS)
        monkeypatch.setattr(features.httpx,'get',lambda *a,**kw:pytest.fail('Bilingual dictionary must work offline'))
        monkeypatch.setattr(features,'completion',lambda *a:pytest.fail('Bilingual dictionary must not use tokens'))
        data=client.get('/api/dictionary?word=Learn').json()
        assert data['bilingual'] and data['entries'][0]['chinese']=='vt. 学习\nvi. 获悉'
        assert data['entries'][0]['phonetic']=='/lɜːn/'
        words=client.get('/api/vocabulary').json()['words']
        assert words[0]['dictionary']['bilingual'] and words[0]['sentence']=='We learn English.'
        assert client.get('/api/dictionary?word=日本語').status_code==422
        assert client.post('/api/vocabulary',json={'word':'日本語'},headers=HEADERS).status_code==422
    finally:
        path.unlink()
