"""Installed edition entrypoint; keeps default setup usable on CPU-only PCs."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('VTS_DEFAULT_DEVICE', 'cpu')
os.environ['PATH'] = str(ROOT / 'tools') + os.pathsep + os.environ.get('PATH', '')

if __name__ == '__main__':
    import start
    raise SystemExit(start.main())
