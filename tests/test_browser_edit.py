import hashlib
import json
import pytest
import app
import media
import pipeline
import store
from test_app import client, create_job, save_config, HEADERS


def seed(client, merged=False):
    save_config(client)
    item, folder = create_job(status='completed', segment_count=3, translated_count=3)
    store.atomic_json(folder/'transcript.json', {'complete':True,'segments':[
        {'id':0,'text':'Hello' if merged else 'Hello.','start':0,'end':1},
        {'id':1,'text':'world.','start':1.1,'end':2},
        {'id':2,'text':'Keep this.','start':4,'end':5}]})
    sig=hashlib.sha256(json.dumps([item['base_url'],item['model'],item['target']]).encode()).hexdigest()
    store.atomic_json(folder/'translated.json', {'signature':sig,'lines':{'0':'你好','1':'世界。','2':'保留。'},'context':{},'context_index':0})
    (folder/'versions').mkdir(exist_ok=True)
    store.atomic_json(folder/'versions'/'ja.json', {'lines':{'0':'こんにちは','1':'世界。','2':'保存。'}})
    return item,folder,f"/api/jobs/{item['id']}/subtitles/0"


def test_browser_import_is_local_explicit_deduplicated_and_never_translates(client,monkeypatch):
    monkeypatch.setattr(media,'public_url',lambda url:url)
    body={'url':'https://example.com/master.m3u8?token=abc','page_url':'https://example.com/watch',
          'title':'Demo','thumbnail':'https://example.com/image.jpg','request_id':'test-request-123456'}
    assert client.post('/api/browser/import',json=body).status_code==403
    assert client.post('/api/browser/import',json=body,headers={**HEADERS,'Origin':'https://evil.example'}).status_code==403
    first=client.post('/api/browser/import',json=body,headers=HEADERS)
    assert first.status_code==200
    second=client.post('/api/browser/import',json=body,headers=HEADERS).json()
    assert second['reused'] and len(store.jobs())==1
    assert not store.jobs()[0]['translation_requested']
    assert store.jobs()[0]['title']=='Demo'
    assert 'browser_poster_url' not in first.json()['job']


def test_browser_rejects_private_links(client):
    body={'url':'http://127.0.0.1/admin','page_url':'https://example.com/watch','request_id':'test-request-123456'}
    assert client.post('/api/browser/import',json=body,headers=HEADERS).status_code==422
    assert not store.jobs()


def test_source_edit_invalidates_only_changed_id_in_all_languages_and_backs_up(client,monkeypatch):
    item,folder,url=seed(client)
    original=(folder/'transcript.json').read_bytes()
    value=client.get(url).json()
    result=client.post(url,json={**value,'text':'Good morning.'},headers=HEADERS)
    assert result.status_code==200 and result.json()['needs_translation']
    active=json.loads((folder/'translated.json').read_text(encoding='utf-8'))
    assert active['lines']=={'1':'世界。','2':'保留。'}
    assert json.loads((folder/'versions'/'ja.json').read_text(encoding='utf-8'))['lines']=={'1':'世界。','2':'保存。'}
    assert next((folder/'backups').glob('subtitle-edit-*/transcript.json')).read_bytes()==original
    assert not store.job(item['id'])['translation_requested']
    calls=[]
    monkeypatch.setattr(pipeline,'translate_block',lambda config,block,*args:calls.extend(s['id'] for s in block) or {s['id']:'早上好。' for s in block})
    assert client.post(f"/api/jobs/{item['id']}/translate",json={},headers=HEADERS).status_code==200
    pipeline.execute(item['id'])
    assert calls==[0]


def test_manual_translation_preserves_other_rows_and_rejects_stale_edit(client):
    item,folder,url=seed(client)
    value=client.get(url).json()
    assert client.post(url,json={**value,'translation':'嗨！'},headers=HEADERS).status_code==200
    assert client.post(url,json={**value,'translation':'覆盖'},headers=HEADERS).status_code==409
    assert pipeline.subtitle_data(item['id'])[0]['translation']=='嗨！'
    assert store.job(item['id'])['status']=='completed'
    assert not store.job(item['id'])['translation_requested']


def test_merged_display_row_can_be_edited_without_duplicate_text_or_wrong_translation(client):
    item,folder,url=seed(client,merged=True)
    value=client.get(url).json()
    assert value['text']=='Hello world.' and value['translation']=='你好世界。'
    assert client.post(url,json={**value,'translation':'您好，世界。'},headers=HEADERS).status_code==200
    source=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))['segments']
    assert [s['id'] for s in source]==[0,2] and source[0]['end']==2
    assert source[0]['text']=='Hello world.'
    active=json.loads((folder/'translated.json').read_text(encoding='utf-8'))['lines']
    assert active=={'0':'您好，世界。','2':'保留。'}
    japanese=json.loads((folder/'versions'/'ja.json').read_text(encoding='utf-8'))['lines']
    assert japanese=={'0':'こんにちは世界。','2':'保存。'}


@pytest.mark.parametrize('status',['queued','processing','uploading'])
def test_edit_rejects_active_jobs(client,status):
    item,folder,url=seed(client)
    value=client.get(url).json()
    store.update_job(item['id'],status=status)
    assert client.post(url,json={**value,'text':'New.'},headers=HEADERS).status_code==409


def test_download_has_no_1080_cap_and_emits_metadata_before_bytes(monkeypatch,tmp_path):
    import yt_dlp
    monkeypatch.setattr(media,'public_url',lambda url:url)
    events=[]
    class Downloader:
        def __init__(self,opts):
            assert opts['format']=='bv*+ba/best'
            assert opts['http_headers']['Referer']=='https://example.com/watch'
            self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,*args,**kwargs):
            info={'title':'A title','duration':60,'thumbnail':'https://example.com/a.jpg'}
            self.opts['match_filter'](info,incomplete=False)
            events.append('bytes');(tmp_path/'source.mp4').write_bytes(b'media')
            return info
    monkeypatch.setattr(yt_dlp,'YoutubeDL',Downloader)
    media.download('https://example.com/watch',tmp_path,lambda:None,lambda *args:None,
                   on_metadata=lambda info:events.append(info['title']),referer='https://example.com/watch')
    assert events==['A title','bytes']
