import json
import queue
import threading
from pathlib import Path

import pytest
import codex_bridge as bridge
import media
import store
import translate
from test_app import client, create_job, HEADERS

CATALOG=[{'model':'official-a','displayName':'A','isDefault':True,'defaultReasoningEffort':'low','supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'high'}]},
         {'model':'official-b','displayName':'B','isDefault':False,'defaultReasoningEffort':'medium','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}]


class FakeRPC:
    instances=[]
    quota=True
    fail=None
    stall=False
    catalog=CATALOG
    def __init__(self,home,shared=False):
        self.home=home;self.events=queue.Queue();self.calls=[];self.closed=False;self.shared=shared
        self.account=json.loads((home/'auth.json').read_text())['tokens']['account_id'] if (home/'auth.json').exists() else None
        self.instances.append(self)
    def call(self,method,params=None,timeout=35):
        self.calls.append((method,params))
        if self.fail==method: raise bridge.CodexError('401 authentication expired')
        if method=='account/read': return {'account':{'type':'chatgpt','email':self.account+'@example.invalid','planType':'plus'}}
        if method=='model/list': return {'data':self.catalog,'nextCursor':None}
        if method=='account/rateLimits/read': return {'ordinaryUsageAllowed':self.quota,'rateLimits':{'primary':{'usedPercent':10}}}
        if method=='account/login/start': return {'authUrl':'https://auth.openai.com/official-login'}
        if method=='thread/start': return {'thread':{'id':'t'},'model':params['model']}
        if method=='turn/start':
            if not self.stall:
                text='{"translations":[{"id":1,"text":"你好"},{"id":2,"text":"再见"}]}'
                for char in text:
                    self.events.put({'method':'item/agentMessage/delta','params':{'threadId':'t','itemId':'a','delta':char}})
                self.events.put({'method':'item/completed','params':{'threadId':'t','item':{'type':'agentMessage','phase':'final_answer','text':text}}})
                self.events.put({'method':'thread/tokenUsage/updated','params':{'threadId':'t','tokenUsage':{'last':{'inputTokens':20,'outputTokens':10,'totalTokens':30}}}})
                self.events.put({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'completed'}}})
            return {'turn':{'id':'u'}}
        return {}
    def close(self): self.closed=True


@pytest.fixture(autouse=True)
def isolated(monkeypatch,tmp_path):
    home=tmp_path/'source';home.mkdir()
    monkeypatch.setattr(bridge,'source_home',lambda:home)
    monkeypatch.setattr(bridge,'RPC',FakeRPC)
    monkeypatch.setattr(bridge,'expires_soon',lambda _:False)
    monkeypatch.setattr(bridge,'CACHE',None)
    monkeypatch.setattr(bridge,'ACTIVE',None)
    monkeypatch.setattr(bridge,'LOGIN',None)
    FakeRPC.instances=[];FakeRPC.fail=None;FakeRPC.stall=False;FakeRPC.quota=True;FakeRPC.catalog=CATALOG
    (store.DATA/'codex-disconnected').unlink(missing_ok=True)
    yield home
    (store.DATA/'codex-disconnected').unlink(missing_ok=True)


def login(home,account='one'):
    data={'auth_mode':'chatgpt','OPENAI_API_KEY':None,'tokens':{'account_id':account,'id_token':'identity-'+account,'access_token':'secret-access-'+account,'refresh_token':'secret-refresh-'+account},'last_refresh':'2026-10-24T01:00:00Z'}
    (home/'auth.json').write_text(json.dumps(data))


def config(**changes): return {'id':'chatgpt','model':'official-a','reasoning_effort':'low',**changes}


def test_logged_out_never_generates_or_uses_old_cached_account(isolated):
    bridge.CACHE={'identity':'old','public':{'connected':True}}
    assert not bridge.status()['connected']
    assert not FakeRPC.instances
    with pytest.raises(bridge.CodexError): bridge.completion(config(),'s','u',lambda:None)


def test_existing_login_dynamic_models_limits_no_generation(client,isolated):
    login(isolated)
    data=client.get('/api/codex/status').json()
    assert data['connected'] and data['account']['email']=='one@example.invalid'
    assert data['models']==[{k:m.get(k) for k in ('model','displayName','supportedReasoningEfforts','defaultReasoningEffort','isDefault')} for m in CATALOG]
    assert data['limits']['ordinaryUsageAllowed']
    assert all(m not in ('thread/start','turn/start','account/login/start') for r in FakeRPC.instances for m,p in r.calls)
    assert 'secret-' not in json.dumps(data)
    assert client.post('/api/codex/login',json={},headers=HEADERS).json()=={'connected':True}


def test_account_switch_after_whole_multi_call_task(client,isolated):
    login(isolated);p=config()
    with bridge.task(p):
        first=bridge.ACTIVE
        login(isolated,'two')
        assert bridge.status()['account']['email']=='one@example.invalid'
        bridge.completion(p,'s','u',lambda:None)
        bridge.completion(p,'s','u',lambda:None)
        assert bridge.ACTIVE is first
        assert len([m for m,p in first.rpc.calls if m=='turn/start'])==2
    assert bridge.status()['account']['email']=='two@example.invalid'


def test_logout_or_corrupt_auth_stops_active_job(isolated):
    login(isolated)
    with bridge.task(config()):
        (isolated/'auth.json').unlink()
        with pytest.raises(bridge.CodexError): bridge.ACTIVE.check(lambda:None)


def test_detection_failure_invalidates_cache(client,isolated):
    login(isolated);assert bridge.status()['connected']
    FakeRPC.fail='account/read'
    assert not bridge.status(True)['connected']
    assert bridge.CACHE is None


def test_private_home_excludes_refresh_and_plugins(isolated):
    login(isolated);record,_=bridge.auth_snapshot()
    home=bridge.private_home(record)
    try:
        raw=(home/'auth.json').read_text()
        assert 'secret-refresh' not in raw and 'secret-access' in raw
        settings=(home/'config.toml').read_text()
        assert 'shell_tool=false' in settings and 'plugins=false' in settings and 'apps=false' in settings
        assert not (home/'plugins').exists()
    finally:
        import shutil
        shutil.rmtree(home)


def test_model_and_effort_revalidated_after_change_and_restart(client,isolated):
    login(isolated);s=bridge.status(True)
    assert s['selection']=={'model':'official-a','reasoning_effort':'low'}
    settings=store.settings(public=True)
    profile=next(p for p in settings['providers'] if p['id']=='chatgpt')
    profile.update(model='official-b',reasoning_effort='medium')
    assert client.post('/api/settings',json=settings,headers=HEADERS).status_code==200
    bridge.CACHE=None
    assert bridge.status(True)['selection']=={'model':'official-b','reasoning_effort':'medium'}
    FakeRPC.catalog=CATALOG[:1]
    assert bridge.status(True)['selection']=={'model':'official-a','reasoning_effort':'low'}


def test_selected_model_effort_sent_and_partials_kept(isolated):
    login(isolated);saved=[];events=[]
    result=translate.translate_block(config(model='official-b',reasoning_effort='medium',_event=events.append,_partial=lambda x:saved.append(x.copy())),
        [{'id':1,'text':'Hello'},{'id':2,'text':'Bye'}],'zh',{},[],lambda:None)
    assert result=={1:'你好',2:'再见'} and {1:'你好'} in saved
    call=next(p for r in FakeRPC.instances for m,p in r.calls if m=='turn/start')
    assert call['model']=='official-b' and call['effort']=='medium'
    assert any(e.get('usage',{}).get('total_tokens')==30 for e in events)


@pytest.mark.parametrize('quota',[False,None])
def test_quota_unavailable_stops_before_generation(isolated,quota):
    login(isolated);FakeRPC.quota=quota
    with pytest.raises(translate.ProviderError,match='额度'):
        translate.completion(config(api_key='paid-key'),'s','u')
    assert all(m!='turn/start' for r in FakeRPC.instances for m,p in r.calls)


def test_model_permission_failure_is_reported_without_fallback(isolated):
    login(isolated);FakeRPC.fail='turn/start'
    with pytest.raises(translate.ProviderError,match='401'):
        translate.completion(config(),'s','u')
    assert sum(m=='turn/start' for r in FakeRPC.instances for m,p in r.calls)==1


def test_unsupported_effort_stops_before_generation(isolated):
    login(isolated)
    with pytest.raises(bridge.CodexError,match='思考强度'):
        bridge.completion(config(reasoning_effort='invented'),'s','u',lambda:None)
    assert all(m!='turn/start' for r in FakeRPC.instances for m,p in r.calls)


def test_stalled_turn_cancel_sends_interrupt_and_releases_lock(isolated):
    login(isolated);FakeRPC.stall=True
    n=0
    def check():
        nonlocal n
        n+=1
        if n>3: raise media.Cancelled()
    with pytest.raises(media.Cancelled): bridge.completion(config(),'s','u',check)
    assert any(m=='turn/interrupt' for r in FakeRPC.instances for m,p in r.calls)
    assert bridge.ACTIVE is None and not bridge.TASK_LOCK.locked()
    assert all(r.closed for r in FakeRPC.instances)


def test_duplicate_tasks_rejected(isolated):
    login(isolated)
    with bridge.task(config()):
        with pytest.raises(bridge.CodexError,match='已有'):
            with bridge.task(config()): pass


def test_disconnect_only_app_and_persists_until_connect(client,isolated):
    login(isolated);original=(isolated/'auth.json').read_bytes()
    response=client.post('/api/codex/disconnect',json={},headers=HEADERS)
    assert response.status_code==200
    bridge.CACHE=None
    assert bridge.status()['disconnected']
    assert (isolated/'auth.json').read_bytes()==original
    assert not any(m=='account/logout' for r in FakeRPC.instances for m,p in r.calls)
    assert client.post('/api/codex/connect',json={},headers=HEADERS).json()['connected']


def test_resume_migrates_existing_paid_rows_without_resending(client,isolated):
    login(isolated);bridge.status(True)
    item,folder=create_job(status='needs_translation',segment_count=2,translated_count=1)
    store.atomic_json(folder/'transcript.json',{'complete':True,'segments':[{'id':1,'text':'Hello','start':0,'end':1},{'id':2,'text':'Bye','start':1,'end':2}]})
    store.atomic_json(folder/'translated.json',{'lines':{'1':'已保存'},'context':{},'context_index':0})
    result=client.post(f'/api/jobs/{item["id"]}/translate',json={'provider_id':'chatgpt','model':'official-a'},headers=HEADERS)
    assert result.status_code==200,result.text
    assert json.loads((folder/'translated.json').read_text('utf-8'))['lines']=={'1':'已保存'}
    assert store.job(item['id'])['reasoning_effort']=='low'
    assert client.post(f'/api/jobs/{item["id"]}/translate',json={},headers=HEADERS).status_code==409


def test_official_login_completes_and_ui_poll_updates(client,isolated):
    import time
    first=client.post('/api/codex/login',json={},headers=HEADERS)
    assert first.status_code==200 and first.json()['url'].startswith('https://auth.openai.com/')
    second=client.post('/api/codex/login',json={},headers=HEADERS)
    assert second.json()==first.json() and len(FakeRPC.instances)==1
    rpc=FakeRPC.instances[0]
    login(isolated)
    rpc.events.put({'method':'account/login/completed','params':{'success':True}})
    for _ in range(100):
        if bridge.LOGIN is None: break
        time.sleep(.01)
    assert client.get('/api/codex/status').json()['connected']
    assert all(m!='turn/start' for r in FakeRPC.instances for m,p in r.calls)


def test_expired_auth_is_not_reported_as_connected(client,isolated):
    login(isolated);FakeRPC.fail='account/rateLimits/read'
    data=client.get('/api/codex/status').json()
    assert not data['connected'] and '401' in data['message']


def test_keyring_configuration_never_falls_back_to_stale_file(isolated):
    login(isolated)
    (isolated/'config.toml').write_text('cli_auth_credentials_store="keyring"')
    assert not bridge.status(True)['connected']
    assert not FakeRPC.instances


def test_no_api_key_account_fallback(isolated):
    (isolated/'auth.json').write_text(json.dumps({'auth_mode':'apikey','OPENAI_API_KEY':'saved-paid-secret'}))
    with pytest.raises(bridge.CodexError): bridge.completion(config(),'s','u',lambda:None)
    assert not FakeRPC.instances


def test_detection_failure_never_starts_another_login(client,isolated):
    login(isolated);FakeRPC.fail='account/read'
    detected=client.get('/api/codex/status').json()
    assert not detected['connected'] and detected['login_required'] is False
    response=client.post('/api/codex/login',json={},headers=HEADERS)
    assert response.status_code==503
    assert all(method!='account/login/start' for rpc in FakeRPC.instances for method,params in rpc.calls)


def test_only_missing_login_requests_login(client,isolated):
    result=client.get('/api/codex/status').json()
    assert result['login_required'] and result['error_code']=='login_required'
    assert not FakeRPC.instances


def test_chinese_windows_username_does_not_break_sid_parse(monkeypatch,isolated):
    import os,shutil
    from types import SimpleNamespace
    if os.name!='nt': pytest.skip('Windows-specific credential-directory protection')
    login(isolated);record,_=bridge.auth_snapshot()
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='whoami':
            assert not kwargs.get('text')
            return SimpleNamespace(stdout=b'"computer\\'+bytes([0xd7,0xde,0xba,0xad])+b'","S-1-5-21-123-456-789-1001"\r\n')
        assert args[0]=='icacls'
        assert '*S-1-5-21-123-456-789-1001:(OI)(CI)F' in args
        return SimpleNamespace(stdout=b'OK')
    monkeypatch.setattr(bridge.subprocess,'run',run)
    folder=bridge.private_home(record)
    try:
        assert (folder/'auth.json').exists() and len(calls)==2
        assert 'secret-refresh' not in (folder/'auth.json').read_text()
    finally:shutil.rmtree(folder)
