import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["VTS_DATA_DIR"] = tempfile.mkdtemp(prefix="yizhen-tests-")
os.environ["VTS_DISABLE_WORKER"] = "1"
