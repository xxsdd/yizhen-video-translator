import copy
import httpx
import store
import translate
from test_app import client, HEADERS


def test_legacy_settings_add_zhipu_without_overwriting(client):
    legacy = copy.deepcopy(store.DEFAULT)
    legacy['providers'] = [p for p in legacy['providers'] if p['id'] != 'zhipu']
    legacy['default_provider'] = 'custom'
    legacy['providers'][0]['key'] = store.encode_key('existing-deepseek-key')
    legacy['providers'][-1].update(base_url='https://example.com/v1', model='my-model')
    store.write_settings(legacy)
    migrated = store.settings()
    assert [p for p in migrated['providers'] if p['id'] != 'zhipu'] == legacy['providers']
    assert migrated['default_provider'] == 'custom'
    assert store.provider('deepseek')['api_key'] == 'existing-deepseek-key'
    assert store.provider('zhipu')['base_url'] == 'https://open.bigmodel.cn/api/paas/v4'
    store.write_settings(migrated)
    assert store.settings() == migrated


def test_zhipu_key_independent_and_private(client):
    config = store.settings(public=True)
    for p in config['providers']:
        if p['id'] in ('zhipu', 'deepseek'):
            p['api_key'] = p['id'] + '-test-secret'
    config['default_provider'] = 'zhipu'
    next(p for p in config['providers'] if p['id'] == 'zhipu')['model'] = 'glm-4.7-flash'
    response = client.post('/api/settings', json=config, headers=HEADERS)
    assert response.status_code == 200
    assert 'test-secret' not in response.text
    assert client.post('/api/settings', json=response.json(), headers=HEADERS).status_code == 200
    assert store.provider('zhipu')['api_key'] == 'zhipu-test-secret'
    assert store.provider('deepseek')['api_key'] == 'deepseek-test-secret'
    assert store.settings()['default_provider'] == 'zhipu'


def test_zhipu_chat_completion_protocol(monkeypatch):
    def fake_post(url, **kwargs):
        assert url == 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
        assert kwargs['headers']['Authorization'] == 'Bearer test-secret'
        assert kwargs['json'] == {
            'model': 'glm-4.7-flash', 'stream': False,
            'messages': [{'role': 'system', 'content': 'translate'}, {'role': 'user', 'content': 'hello'}],
        }
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '你好', 'reasoning_content': 'ignored'}}]})
    monkeypatch.setattr(translate.httpx, 'post', fake_post)
    config = {'base_url': 'https://open.bigmodel.cn/api/paas/v4/', 'model': 'glm-4.7-flash', 'api_key': 'test-secret'}
    assert translate.completion(config, 'translate', 'hello') == '你好'
