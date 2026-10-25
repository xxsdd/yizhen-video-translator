import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def launcher(monkeypatch, tmp_path):
    monkeypatch.setenv('VTS_PORT', '18765')
    monkeypatch.setenv('VTS_DATA_DIR', str(tmp_path / 'user-data'))
    spec = importlib.util.spec_from_file_location('isolated_launcher', Path(__file__).parents[1] / 'start.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'runtime/python.exe').touch()
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'runtime.json').write_text(json.dumps({'python_executable': 'runtime/python.exe', 'startup_timeout_seconds': 120}))
    module.configure_log()
    return module


def test_installed_launcher_relative_runtime_and_isolated_service(monkeypatch, tmp_path):
    import contextlib
    module = launcher(monkeypatch, tmp_path)
    monkeypatch.setattr(module, 'startup_lock', contextlib.nullcontext)
    checks = iter([False, True])
    monkeypatch.setattr(module, 'running', lambda: next(checks))
    calls = []
    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setenv('PYTHONPATH', 'unrelated-environment')
    module.ensure_service()
    args, kw = calls[0]
    assert args[0] == str((tmp_path / 'runtime/python.exe').resolve())
    assert args[args.index('--port')+1] == '18765'
    assert kw['env']['VTS_DATA_DIR'] == str((tmp_path / 'user-data').resolve())
    assert kw['env']['PATH'].startswith(str(tmp_path / 'tools'))
    assert 'PYTHONPATH' not in kw['env']
    assert module.URL == 'http://127.0.0.1:18765'


def test_existing_service_not_started_twice(monkeypatch, tmp_path):
    import contextlib
    module = launcher(monkeypatch, tmp_path)
    monkeypatch.setattr(module, 'startup_lock', contextlib.nullcontext)
    monkeypatch.setattr(module, 'running', lambda: True)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('duplicate server')))
    module.ensure_service()
