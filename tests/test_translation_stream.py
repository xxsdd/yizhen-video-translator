import asyncio
import json

import httpx
import pytest

import media
import pipeline
import store
import translate
from test_app import client, create_job, save_config, HEADERS


class Events(httpx.AsyncByteStream):
    def __init__(self, chunks, stall=False):
        self.chunks, self.stall, self.closed = chunks, stall, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def packet(delta=None, reason=None, usage=None):
    return ('data: ' + json.dumps({'choices': [{'delta': delta or {}, 'finish_reason': reason}], 'usage': usage}) + '\n\n').encode()


def mock_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(translate.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def config(**extra):
    return {'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'api_key': 'fake', 'model': 'glm-5.3-flash', **extra}


def test_stream_saves_complete_entries_and_reports_usage(monkeypatch):
    events, saved = [], []
    text = json.dumps({'translations': [{'id': 1, 'text': '你好，"朋友"。'}, {'id': 2, 'text': '再见。'}]}, ensure_ascii=False)
    def handler(request):
        body = json.loads(request.content)
        assert body['stream'] is True
        assert body['reasoning_effort'] == 'low'
        assert body['thinking'] == {'type': 'enabled'}
        assert request.extensions['timeout']['read'] is None
        assert request.extensions['timeout']['connect'] == 20
        # Test boundaries in the middle of JSON escapes/strings and UTF-8 bytes.
        wire = packet({'reasoning_content': 'not stored'}) + b''.join(packet({'content': c}) for c in text)
        wire += packet(reason='stop', usage={'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}) + b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Events([wire[i:i+7] for i in range(0, len(wire), 7)]))
    mock_transport(monkeypatch, handler)
    result = translate.translate_block(config(_event=events.append, _partial=lambda p: saved.append(p.copy())), [{'id': 1, 'text': 'Hello'}, {'id': 2, 'text': 'Bye'}], 'zh', {}, [], lambda: None)
    assert result == {1: '你好，"朋友"。', 2: '再见。'}
    assert any(p == {1: '你好，"朋友"。'} for p in saved)
    assert any(e.get('stage') == 'thinking' for e in events)
    assert any(e.get('usage', {}).get('total_tokens') == 130 for e in events)
    assert 'not stored' not in json.dumps(events)


def test_stalled_stream_can_be_paused_before_first_byte(monkeypatch):
    stream = Events([], stall=True)
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    checks = []
    def check():
        checks.append(True)
        if len(checks) >= 3:
            raise media.Cancelled()
    with pytest.raises(media.Cancelled):
        translate.completion(config(_event=lambda e: None), 's', 'u', check)
    assert stream.closed


def test_pause_freezes_elapsed_time(client):
    import time
    started = time.time() - 300
    item, _ = create_job(status='processing', translation_activity={'stage': 'waiting', 'started': started})
    assert client.post(f"/api/jobs/{item['id']}/pause", json={}, headers=HEADERS).status_code == 200
    activity = store.job(item['id'])['translation_activity']
    assert activity['stage'] == 'paused'
    assert activity['ended'] - activity['started'] >= 300


def test_interrupted_stream_keeps_rows_and_resumes_only_missing(client, monkeypatch):
    save_config(client)
    item, folder = create_job(translation_requested=True)
    store.atomic_json(folder / 'transcript.json', {'complete': True, 'segments': [{'id': i, 'text': f'Line {i}.', 'start': i, 'end': i+1} for i in range(3)]})
    calls = []
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert 'transcript' not in data  # No separate summary for a short transcript.
        ids = [s['id'] for s in data['translate']]
        calls.append(ids)
        if len(calls) == 1:
            chunks = [packet({'content': '{"translations":[{"id":0,"text":"已保存。"},{"id":1,"text":"未完'})]
        else:
            chunks = [packet({'content': json.dumps({'translations': [{'id': i, 'text': '补翻。'} for i in ids]})}), packet(reason='stop', usage={'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50})]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Events(chunks))
    mock_transport(monkeypatch, handler)
    with pytest.raises(translate.ProviderError, match='提前结束'):
        pipeline.execute(item['id'])
    cached = json.loads((folder / 'translated.json').read_text(encoding='utf-8'))
    assert cached['lines'] == {'0': '已保存。'}
    assert len(calls) == 1  # A dropped connection never automatically bills another request.
    store.update_job(item['id'], status='queued')
    pipeline.execute(item['id'])
    assert calls == [[0, 1, 2], [1, 2]]
    job = store.job(item['id'])
    assert job['status'] == 'completed' and job['translated_count'] == 3
    assert job['translation_usage']['requests'] == 2
    assert job['translation_usage']['reported_requests'] == 1
    assert job['translation_usage']['total_tokens'] == 50


def test_short_and_long_plans_preserve_every_source_segment():
    short = [{'id': i, 'text': 'A short sentence.'} for i in range(111)]
    blocks, direct = translate.translation_blocks(short)
    assert direct and blocks == [short]
    long = [{'id': i, 'text': 'word ' * 30} for i in range(800)]
    blocks, direct = translate.translation_blocks(long)
    assert not direct and len(blocks) > 1
    assert [s for b in blocks for s in b] == long


def test_invalid_or_duplicate_stream_ids_are_rejected():
    for value in ['{"translations":[{"id":9,"text":"bad"}]}', '{"translations":[{"id":1,"text":"ok"},{"id":1,"text":"duplicate"}]}']:
        with pytest.raises(translate.ProviderError):
            translate.complete_entries(value, {1})


def test_stream_accepts_compatible_nonstream_json(monkeypatch):
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': '你好'}, 'finish_reason': 'stop'}]}))
    assert translate.completion(config(_event=lambda e: None), 's', 'u') == '你好'


def test_done_marker_finishes_without_waiting_for_socket_close(monkeypatch):
    stream = Events([packet({'content': '你好'}), packet(reason='stop'), b'data: [DONE]\n\n'], stall=True)
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    checks = []
    def check():
        checks.append(True)
        assert len(checks) < 10, 'DONE must finish the response even if the socket stays open'
    assert translate.completion(config(_event=lambda e: None), 's', 'u', check) == '你好'
    assert stream.closed


def test_abnormal_finish_records_specific_reason_without_automatic_retry(monkeypatch):
    events, calls = [], []
    def handler(request):
        calls.append(1)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
                              stream=Events([packet(reason='sensitive')]))
    mock_transport(monkeypatch,handler)
    with pytest.raises(translate.ProviderError,match='finish_reason=sensitive') as error:
        translate.completion(config(_event=events.append),'s','u')
    assert error.value.details['finish_reason']=='sensitive'
    assert len(calls)==1
    assert any(e.get('diagnostic',{}).get('finish_reason')=='sensitive' for e in events)


def test_http_error_exposes_code_but_redacts_credentials(monkeypatch):
    mock_transport(monkeypatch,lambda request:httpx.Response(400,
        headers={'x-request-id':'request-123'},json={'error':{'code':'1210','message':'Invalid fake https://host/?secret=abc'}}))
    with pytest.raises(translate.ProviderError) as error:
        translate.completion(config(_event=lambda e:None),'s','u')
    assert error.value.details['provider_code']=='1210'
    assert error.value.details['request_id']=='request-123'
    assert 'fake' not in str(error.value) and 'secret=abc' not in str(error.value)


def test_saved_long_video_never_repeats_context_requests(client,monkeypatch):
    import hashlib
    save_config(client)
    item,folder=create_job(translation_requested=True)
    source=[{'id':i,'start':i,'end':i+1,'text':'Original.'} for i in range(250)]
    store.atomic_json(folder/'transcript.json',{'complete':True,'segments':source})
    signature=hashlib.sha256(json.dumps([item['base_url'],item['model'],item['target']]).encode()).hexdigest()
    store.atomic_json(folder/'translated.json',{'signature':signature,'lines':{str(i):'已保存' for i in range(250)}})
    monkeypatch.setattr(pipeline,'build_context',lambda *a:pytest.fail('Must reuse all saved work'))
    pipeline.execute(item['id'])
    assert store.job(item['id'])['status']=='completed'


def test_rechecked_ids_use_chronological_neighbors_and_skip_saved_ids(client,monkeypatch):
    import hashlib
    save_config(client)
    item,folder=create_job(translation_requested=True)
    ids=[i for pair in zip(range(100),range(100,200)) for i in pair]
    source=[{'id':id,'start':i,'end':i+1,'text':f'Line {id}.'} for i,id in enumerate(ids)]
    store.atomic_json(folder/'transcript.json',{'complete':True,'segments':source})
    signature=hashlib.sha256(json.dumps([item['base_url'],item['model'],item['target']]).encode()).hexdigest()
    saved={str(i):'已保存' for i in ids[:20]}
    store.atomic_json(folder/'translated.json',{'signature':signature,'lines':saved,'context_index':999,'context':{}})
    calls=[]
    def translate_missing(config,block,target,context,neighbors,check):
        assert not any(str(s['id']) in saved for s in block)
        first=ids.index(block[0]['id']);last=ids.index(block[-1]['id'])
        expected=source[max(0,first-2):first]+source[last+1:last+3]
        assert [s['text'] for s in neighbors]==[s['text'] for s in expected]
        assert len(neighbors)<=4
        calls.append(block)
        return {s['id']:'新增译文' for s in block}
    monkeypatch.setattr(pipeline,'translate_block',translate_missing)
    pipeline.execute(item['id'])
    cached=json.loads((folder/'translated.json').read_text(encoding='utf-8'))['lines']
    assert all(cached[k]==v for k,v in saved.items()) and len(cached)==200 and calls
