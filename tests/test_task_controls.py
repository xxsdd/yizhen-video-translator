import json
import threading
import time
import pytest
import pipeline
import media
import store
import library_features as features
from test_app import client,create_job,HEADERS


def control(client,item,action):
    return client.post(f"/api/jobs/{item['id']}/control",json={'action':action},headers=HEADERS)


def test_imports_wait_for_explicit_download_and_processing(client,monkeypatch):
    monkeypatch.setattr(media,'public_url',lambda u:u)
    res=client.post('/api/jobs/links',json={'urls':['https://example.com/watch'],'provider_id':'deepseek'},headers=HEADERS)
    item=res.json()['jobs'][0]
    assert item['status']=='awaiting_download'
    assert control(client,item,'process').status_code==400
    assert control(client,item,'download').status_code==200
    assert store.job(item['id'])['run_mode']=='download'
    monkeypatch.setattr(media,'save_poster',lambda *args:None)
    def download(url,folder,*args,**kwargs):
        path=folder/'source.mp4';path.write_bytes(b'media');return path,'Website title'
    monkeypatch.setattr(media,'download',download)
    monkeypatch.setattr(media,'prepare',lambda *args:pytest.fail('download must not start ASR/conversion'))
    pipeline.execute(item['id'])
    assert store.job(item['id'])['status']=='awaiting_processing'
    assert control(client,item,'process').status_code==200
    assert store.job(item['id'])['run_mode']=='process'
    assert not store.job(item['id'])['translation_requested']
    res=client.post('/api/jobs/files',files={'files':('test.mp4',b'media','video/mp4')},data={'provider_id':'deepseek'},headers=HEADERS)
    assert res.json()['jobs'][0]['status']=='awaiting_processing'


def test_rename_survives_background_metadata_and_download_title(client):
    item,folder=create_job(status='processing')
    assert client.post(f"/api/jobs/{item['id']}/rename",json={'title':'我的课程'},headers=HEADERS).status_code==200
    store.update_job(item['id'],title='Website title',progress=4)
    assert store.job(item['id'])['title']=='我的课程'
    assert client.post(f"/api/jobs/{item['id']}/rename",json={'title':'  '},headers=HEADERS).status_code==422


def test_pause_then_immediate_resume_waits_for_old_worker_to_stop(client,monkeypatch):
    item,folder=create_job(status='processing')
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',item['id'])
    assert control(client,item,'pause').status_code==200
    assert control(client,item,'process').status_code==200
    data=store.job(item['id'])
    assert data['status']=='paused' and data['resume_after_stop']
    with pytest.raises(media.Cancelled):pipeline.check_job(item['id'])
    store.update_job(item['id'],status='completed',message='late write')
    assert store.job(item['id'])['status']=='paused'
    control(client,item,'pause')
    assert not store.job(item['id'])['resume_after_stop']


def test_priority_pauses_active_job_and_wins_over_older_queued_job(client,monkeypatch):
    active,_=create_job(status='processing',translation_requested=True)
    older,_=create_job(status='queued')
    chosen,_=create_job(status='paused')
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',active['id'])
    assert control(client,chosen,'priority').status_code==200
    assert store.job(active['id'])['status']=='paused' and not store.job(active['id'])['translation_requested']
    assert store.job(chosen['id'])['queue_priority']>0
    assert store.job(older['id']).get('queue_priority',0)==0


def test_delete_inflight_does_not_resurrect_and_retries_locked_file(client,monkeypatch):
    item,folder=create_job(status='processing')
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',item['id'])
    response=client.post('/api/jobs/delete',json={'ids':[item['id']]},headers=HEADERS)
    assert response.status_code==200 and response.json()['deleting']==[item['id']]
    assert client.get('/api/state').json()['jobs']==[]
    with pytest.raises(media.Cancelled):pipeline.check_job(item['id'])
    store.update_job(item['id'],status='completed',title='late worker')
    assert store.job(item['id'])['status']=='deleting'
    monkeypatch.setattr(pipeline,'ACTIVE_JOB',None)
    original=features.shutil.rmtree
    def locked(path):raise PermissionError('file stream closing')
    monkeypatch.setattr(features.shutil,'rmtree',locked)
    assert features.cleanup_deleted()==[]
    monkeypatch.setattr(features.shutil,'rmtree',original)
    assert features.cleanup_deleted()==[item['id']]
    assert not folder.exists()


def test_worker_switches_and_cleans_up_after_cooperative_cancel(client,monkeypatch):
    first,_=create_job(status='queued')
    second,_=create_job(status='paused')
    entered=threading.Event();done=threading.Event();calls=[]
    def execute(job_id):
        calls.append(job_id)
        store.update_job(job_id,status='processing')
        if job_id==first['id']:
            entered.set()
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                pipeline.check_job(job_id);time.sleep(.01)
            raise AssertionError('cancel did not arrive')
        store.update_job(job_id,status='awaiting_processing')
        done.set();pipeline.STOP.set()
    monkeypatch.setattr(pipeline,'execute',execute)
    pipeline.STOP.clear();thread=threading.Thread(target=pipeline.worker);thread.start()
    try:
        assert entered.wait(2)
        assert control(client,second,'priority').status_code==200
        assert done.wait(3)
        assert calls==[first['id'],second['id']]
        assert store.job(first['id'])['status']=='paused'
    finally:
        pipeline.STOP.set();pipeline.WAKE.set();thread.join(5);pipeline.STOP.clear()
