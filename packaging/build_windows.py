"""Stage a self-contained Windows runtime from an installed, pinned environment.

Build example: python packaging/build_windows.py --output D:/YizhenBuild/0.1.1
  --python-base <standalone Python 3.12> --site-packages <venv>/Lib/site-packages
  --ffmpeg <ffmpeg build> --dictionary <ECDICT-only directory>
No application data, credentials, models, or working files are copied.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.1'


def ignore(folder, names):
    return [n for n in names if n in ('__pycache__', '.git', '_virtualenv.py', '_virtualenv.pth', 'direct_url.json') or n.endswith(('.pyc', '.pyo'))]


def build(args):
    output = Path(args.output).resolve()
    payload = output / 'payload'
    if payload.exists():
        raise SystemExit('Use a fresh output directory; existing builds are never overwritten.')
    payload.mkdir(parents=True)
    base, packages, ffmpeg, dictionary = map(Path, (args.python_base, args.site_packages, args.ffmpeg, args.dictionary))
    if not (base / 'python312.dll').is_file():
        raise SystemExit('A standalone Windows x64 Python 3.12 runtime is required.')
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        (payload / 'runtime').mkdir(exist_ok=True)
        shutil.copy2(base / name, payload / 'runtime' / name)
    shutil.copytree(base / 'DLLs', payload / 'runtime/DLLs', ignore=ignore)
    shutil.copytree(base / 'Lib', payload / 'runtime/Lib', ignore=lambda p, n: ignore(p, n) + (['site-packages'] if Path(p) == base / 'Lib' else []))
    shutil.copytree(packages, payload / 'runtime/Lib/site-packages', ignore=ignore)
    for path in ROOT.glob('*.py'):
        shutil.copy2(path, payload / path.name)
    for name in ('static', 'assets', 'chrome-extension'):
        shutil.copytree(ROOT / name, payload / name, ignore=ignore)
    for name in ('README.md', 'THIRD_PARTY_NOTICES.md', 'requirements-lock.txt'):
        shutil.copy2(ROOT / name, payload / name)
    shutil.copy2(ROOT / 'packaging/launch.py', payload / 'launch.py')
    (payload / 'tools').mkdir()
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        shutil.copy2(ffmpeg / 'bin' / name, payload / 'tools' / name)
    for name in ('LICENSE', 'README.txt'):
        shutil.copy2(ffmpeg / name, payload / 'tools' / ('FFmpeg-' + name))
    # This database is a static dictionary, never the personal library database.
    with sqlite3.connect(f'file:{(dictionary / "ecdict.sqlite3").as_posix()}?mode=ro', uri=True) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('entries',)]
        count = db.execute('SELECT count(*) FROM entries').fetchone()[0]
        assert count > 100000
    destination = payload / 'data/dictionaries'
    destination.mkdir(parents=True)
    for name in ('ecdict.sqlite3', 'ECDICT-LICENSE.txt'):
        shutil.copy2(dictionary / name, destination / name)
    (payload / 'runtime.json').write_text(json.dumps({'python_executable': 'runtime/python.exe', 'startup_timeout_seconds': 120}), encoding='utf-8')
    (payload / '启动译帧.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0runtime\\pythonw.exe" -I -X utf8 "%~dp0launch.py"\r\n', encoding='utf-8')
    files = sorted(p for p in payload.rglob('*') if p.is_file())
    forbidden = ('library.sqlite3', 'auth.json', '.env', 'launcher.log', 'server.log')
    assert not any(p.name in forbidden or '/data/jobs/' in p.as_posix() or '/data/models/' in p.as_posix() for p in files)
    assert not list((payload / 'runtime/Lib/site-packages').glob('*.pth'))
    manifest = [{'path': p.relative_to(payload).as_posix(), 'bytes': p.stat().st_size, 'sha256': hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()} for p in files]
    report = {'version': VERSION, 'files': manifest, 'dictionary_entries': count, 'bytes': sum(x['bytes'] for x in manifest)}
    (output / 'payload-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'payload': str(payload), 'files': len(files), 'bytes': report['bytes'], 'dictionary_entries': count}), flush=True)
    python = payload / 'runtime/python.exe'
    subprocess.run([str(python), '-I', '-c', 'import fastapi,uvicorn,av,ctranslate2,faster_whisper,yt_dlp,httpx,nltk,opencc,cryptography; print("BUNDLED_IMPORTS_OK")'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('output', 'python-base', 'site-packages', 'ffmpeg', 'dictionary'):
        parser.add_argument('--' + name, required=True)
    build(parser.parse_args())
