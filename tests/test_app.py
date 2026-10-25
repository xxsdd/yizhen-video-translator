import json
import uuid
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import app
import media
import pipeline
import store
import translate
import numpy as np
import faster_whisper.audio

HEADERS = {"X-Local-App": "1"}


@pytest.fixture(autouse=True)
def synthetic_audio_for_pipeline_mocks(monkeypatch):
    monkeypatch.setattr(faster_whisper.audio, 'decode_audio', lambda *a, **k: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(pipeline, 'speech_windows', lambda audio, check: [[0, len(audio)]])


@pytest.fixture()
def client():
    with store.connection() as db:
        db.execute("DELETE FROM jobs")
        db.execute("DELETE FROM settings")
        db.execute("DELETE FROM vocabulary")
        db.execute("DELETE FROM dictionary_cache")
    with TestClient(app.app) as client:
        yield client


def create_job(**updates):
    item = app.new_job("test.mp4", app.options("deepseek", "zh", ""), kind="file", source_file="source.mp4", **updates)
    store.save_job(item)
    folder = store.job_dir(item["id"])
    (folder / "source.mp4").write_bytes(b"test")
    return item, folder


def save_config(client):
    settings = store.settings(public=True)
    for p in settings["providers"]:
        p.pop("has_key", None)
        p["api_key"] = "not-a-real-key"
        p["model"] = "test-model"
    assert client.post("/api/settings", json=settings, headers=HEADERS).status_code == 200


def test_secret_encrypted_and_never_returned(client):
    save_config(client)
    raw = store.settings()["providers"][0]["key"]
    assert raw and "not-a-real-key" not in raw
    assert store.provider("deepseek")["api_key"] == "not-a-real-key"
    response = client.get("/api/state")
    assert "not-a-real-key" not in response.text
    assert '"key"' not in response.text
    config = response.json()["settings"]
    assert client.post("/api/settings", json=config, headers=HEADERS).status_code == 200
    assert store.provider("deepseek")["api_key"] == "not-a-real-key"


def test_cross_origin_and_missing_header_rejected(client):
    assert client.post("/api/model/prepare").status_code == 403
    assert client.post("/api/model/prepare", headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_private_url_batch_rejected_without_jobs(client):
    response = client.post("/api/jobs/links", json={"urls": ["http://127.0.0.1/private"], "provider_id": "deepseek"}, headers=HEADERS)
    assert response.status_code == 422
    assert store.jobs() == []


def test_batch_limit_and_upload_failure(client):
    response = client.post("/api/jobs/links", json={"urls": ["https://example.com"] * 11, "provider_id": "deepseek"}, headers=HEADERS)
    assert response.status_code == 422
    response = client.post("/api/jobs/files", files=[("files", ("test.m3u8", b"#EXTM3U"))], data={"provider_id":"deepseek"}, headers=HEADERS)
    assert response.status_code == 422
    response = client.post("/api/jobs/files", files=[("files", ("test.mp4", b""))], data={"provider_id":"deepseek"}, headers=HEADERS)
    assert response.status_code == 400
    failed = store.jobs()[0]
    assert failed["status"] == "failed" and not failed["source_file"]


def test_subtitles_range_and_retained_history(client):
    item, folder = create_job(status="completed", playable=True)
    (folder / "playback.mp4").write_bytes(b"0123456789")
    store.atomic_json(folder / "transcript.json", {"segments": [{"id": 0, "start": 1.25, "end": 3.5, "text": "Hello"}], "complete": True})
    store.atomic_json(folder / "translated.json", {"lines": {"0": "你好"}})
    response = client.get(f"/api/jobs/{item['id']}/video", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    response = client.get(f"/api/jobs/{item['id']}/subtitles?format=srt&mode=bilingual")
    assert "00:00:01,250 --> 00:00:03,500" in response.text
    assert "你好\nHello" in response.text
    assert client.post(f"/api/jobs/{item['id']}/clear-media", headers=HEADERS).status_code == 200
    assert (folder / "transcript.json").exists()
    assert not (folder / "playback.mp4").exists()
    assert store.job(item["id"])["playable"] is False


def test_no_clear_during_processing_and_no_double_retry(client):
    item, _ = create_job(status="processing")
    assert client.post(f"/api/jobs/{item['id']}/clear-media", headers=HEADERS).status_code == 409
    assert client.post(f"/api/jobs/{item['id']}/retry", json={}, headers=HEADERS).status_code == 409


def test_translation_rejects_missing_duplicate_and_unknown_ids(monkeypatch):
    block = [{"id": 0, "text": "A"}, {"id": 1, "text": "B"}]
    for rows in ([{"id": 0, "text": "甲"}], [{"id": 0, "text": "甲"}, {"id": 0, "text": "乙"}], [{"id": 0, "text": "甲"}, {"id": 2, "text": "乙"}]):
        monkeypatch.setattr(translate, "completion", lambda *args: json.dumps({"translations": rows}))
        with pytest.raises(translate.ProviderError):
            translate.translate_block({}, block, "zh", {}, [], lambda: None)


def test_pipeline_waits_for_key_then_resumes_without_retranscribing(client, monkeypatch):
    item, folder = create_job()
    model_calls = []
    def fake_prepare(*args):
        (folder / "playback.mp4").write_bytes(b"fake-media")
        return 10
    class Model:
        def transcribe(self, *args, **kwargs):
            model_calls.append(1)
            return iter([SimpleNamespace(start=0, end=4, text="Hello world."), SimpleNamespace(start=5, end=9, text="This is a test.")]), SimpleNamespace(language="en")
    monkeypatch.setattr(media, "prepare", fake_prepare)
    monkeypatch.setattr(media, "run", lambda *args, **kwargs: "")
    monkeypatch.setattr(pipeline, "load_model", lambda *args: Model())
    pipeline.execute(item["id"])
    assert store.job(item["id"])["status"] == "needs_translation"
    assert len(pipeline.subtitle_data(item["id"])) == 2
    save_config(client)
    assert client.post(f"/api/jobs/{item['id']}/translate", json={}, headers=HEADERS).status_code == 200
    monkeypatch.setattr(pipeline, "build_context", lambda *args: {"summary":"test", "terms":[]})
    calls = []
    def fake_translate(config, block, *args):
        calls.append(len(block))
        return {s["id"]: "测试译文" for s in block}
    monkeypatch.setattr(pipeline, "translate_block", fake_translate)
    pipeline.execute(item["id"])
    assert store.job(item["id"])["status"] == "completed"
    assert model_calls == [1]
    assert calls == [2]
    store.update_job(item["id"], status="queued", translation_requested=True)
    pipeline.execute(item["id"])
    assert calls == [2]  # Paid work must not be repeated on a completed checkpoint.
    assert all(s["translation"] for s in pipeline.subtitle_data(item["id"]))


def test_context_chunking_preserves_all_lines():
    source=[{"id":i,"text":"a"*100} for i in range(1200)]
    grouped=list(translate.chunks(source))
    assert [s["id"] for block in grouped for s in block] == list(range(1200))
    assert all(len(block)<=45 for block in grouped)


def test_recognition_processes_dialogue_in_video_tail(client, monkeypatch):
    item, folder = create_job()
    monkeypatch.setattr(media, "prepare", lambda *args: 663)
    monkeypatch.setattr(media, "run", lambda *args, **kwargs: "")
    class Model:
        def transcribe(self, *args, **kwargs):
            # Long gaps without dialogue must not truncate processing at 5 minutes.
            parts = [SimpleNamespace(start=0, end=4, text="Opening dialogue."),
                     SimpleNamespace(start=600, end=642, text="Quiet closing dialogue.")]
            return iter(parts), SimpleNamespace(language="en")
    monkeypatch.setattr(pipeline, "load_model", lambda *args: Model())
    pipeline.execute(item["id"])
    saved = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert saved["complete"] and saved["processed_until"] == 663
    assert saved["segments"][-1]["end"] == 642
    assert len(pipeline.subtitle_data(item["id"])) == 2
    assert store.job(item["id"])["status"] == "needs_translation"


def test_newly_recognized_tail_does_not_retranslate_saved_lines(client, monkeypatch):
    save_config(client)
    item, folder = create_job()
    monkeypatch.setattr(media, "prepare", lambda *args: 663)
    monkeypatch.setattr(media, "run", lambda *args, **kwargs: "")
    store.atomic_json(folder / "transcript.json", {"complete": True, "segments": [
        {"id": 0, "start": 0, "end": 4, "text": "Opening."},
        {"id": 1, "start": 600, "end": 642, "text": "Closing."}]})
    import hashlib
    config = store.provider(item["provider_id"])
    signature = hashlib.sha256(json.dumps([item["base_url"], item["model"], item["target"]]).encode()).hexdigest()
    store.atomic_json(folder / "translated.json", {"signature": signature, "lines": {"0": "原有译文"}, "context_index": 0, "context": {}})
    calls = []
    monkeypatch.setattr(pipeline, "build_context", lambda *args: {"summary": "", "terms": []})
    def translate_tail(config, missing, *args):
        calls.append([s["id"] for s in missing])
        return {s["id"]: "新增译文" for s in missing}
    monkeypatch.setattr(pipeline, "translate_block", translate_tail)
    store.update_job(item["id"], translation_requested=True)
    pipeline.execute(item["id"])
    assert calls == [[1]]
    assert [s["translation"] for s in pipeline.subtitle_data(item["id"])] == ["原有译文", "新增译文"]


def test_resume_new_subtitles_preserves_model_after_default_changes(client):
    save_config(client)
    item, folder = create_job(status="needs_translation")
    store.atomic_json(folder / "translated.json", {"lines": {"0": "已付费译文"}})
    settings = client.get("/api/state").json()["settings"]
    settings["providers"][0]["model"] = "different-model"
    assert client.post("/api/settings", json=settings, headers=HEADERS).status_code == 200
    response = client.post(f"/api/jobs/{item['id']}/retry", json={"resume_saved": True}, headers=HEADERS)
    assert response.status_code == 200
    assert store.job(item["id"])["model"] == item["model"]
    assert (folder / "translated.json").exists()
    assert json.loads((folder / "translated.json").read_text(encoding="utf-8"))["lines"]["0"] == "已付费译文"


def test_subtitle_split_uses_word_timestamps():
    segment=SimpleNamespace(start=0,end=14,text="",words=[SimpleNamespace(start=i,end=i+.8,word=" example") for i in range(14)])
    lines=pipeline.readable_segments(segment)
    assert len(lines)==3  # One-line captions take priority over unbounded sentences.
    assert lines[0][0]==0 and lines[-1][1]==13.8
    assert all(end-start<=6 and len(text)<=48 for start,end,text in lines)
    assert " ".join(text for _,_,text in lines).split()==["example"]*14


def test_pause_cannot_race_cache_deletion_or_retry(client,monkeypatch):
    item,_=create_job(status="paused")
    monkeypatch.setattr(pipeline,"ACTIVE_JOB",item["id"])
    assert client.post(f"/api/jobs/{item['id']}/clear-media",headers=HEADERS).status_code==409
    assert client.post(f"/api/jobs/{item['id']}/retry",json={},headers=HEADERS).status_code==409


def test_compatible_api_over_real_http():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            assert self.path=='/v1/chat/completions'
            assert self.headers['Authorization']=='Bearer test-only-key'
            payload=json.loads(body['messages'][1]['content'])
            result=({'summary':'Context is consistent.','terms':[]} if 'transcript' in payload
                    else {'translations':[{'id':s['id'],'text':'你好世界'} for s in payload['translate']]})
            response=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        config={'base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':'test-only-key','model':'mock-model'}
        context=translate.build_context(config,['Hello world'],'zh',{},lambda:None)
        result=translate.translate_block(config,[{'id':7,'text':'Hello world'}],'zh',context,[],lambda:None)
        assert result=={7:'你好世界'}
        assert len(requests)==2
        assert requests[1]['model']=='mock-model'
    finally:
        server.shutdown();server.server_close();thread.join()


def test_timeout_is_not_automatically_rebilled(monkeypatch):
    import httpx
    calls=[]
    def timeout(*args,**kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('test')
    monkeypatch.setattr(translate.httpx,'post',timeout)
    with pytest.raises(translate.ProviderError,match='超时'):
        translate.completion({'base_url':'https://example.com','model':'test','api_key':'test'},'system','test')
    assert len(calls)==1
