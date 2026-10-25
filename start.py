"""Standard-library Windows launcher, run by an independent Python install."""
import contextlib
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"
DATA = ROOT / "data"
LOG = logging.getLogger("yizhen-launcher")


def configure_log():
    DATA.mkdir(exist_ok=True)
    handler = logging.FileHandler(DATA / "launcher.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    LOG.handlers[:] = [handler]
    LOG.setLevel(logging.INFO)


def running():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + "/api/health", timeout=1) as response:
            return json.load(response).get("app") == "yizhen-local"
    except Exception:
        return False


@contextlib.contextmanager
def startup_lock():
    """Serialize simultaneous desktop clicks without keeping a server lock."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Local\\YizhenLauncher-" + hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 90000)
        if result not in (0, 0x80):
            raise RuntimeError("另一个启动器仍在启动服务，请稍后重试。")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def runtime():
    path = ROOT / "runtime.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"无法读取启动配置 {path}：{exc}") from exc
    value = config.get("python_executable")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("runtime.json 缺少 python_executable（原有虚拟环境的 python.exe）。")
    executable = Path(os.path.expandvars(value)).expanduser()
    executable = (ROOT / executable).resolve() if not executable.is_absolute() else executable.resolve()
    if not executable.is_file():
        raise RuntimeError(f"配置的后台解释器不存在：{executable}")
    timeout = float(config.get("startup_timeout_seconds", 60))
    if not 1 <= timeout <= 300:
        raise RuntimeError("startup_timeout_seconds 必须在 1–300 秒之间。")
    return executable, timeout


def server_error(path, offset):
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read()[-12000:]
        text = raw.decode("utf-8", errors="replace").strip()
        text = re.sub(r"(?i)Bearer\s+\S+|sk-[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+", "[redacted]", text)
        return "\n".join(text.splitlines()[-30:])[-4000:]
    except OSError:
        return "无法读取后台启动日志。"


def ensure_service():
    with startup_lock():
        if running():
            LOG.info("READY mode=already_running url=%s", URL)
            return
        executable, timeout = runtime()
        LOG.info("SERVER_INTERPRETER path=%s", executable)
        env = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__", "_PYVENV_LAUNCHER", "VIRTUAL_ENV"):
            env.pop(key, None)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
        server_log = DATA / "server.log"
        offset = server_log.stat().st_size if server_log.exists() else 0
        with server_log.open("ab", buffering=0) as output:
            proc = subprocess.Popen(
                [str(executable), "-X", "utf8", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"],
                cwd=ROOT, env=env, stdout=output, stderr=output,
                stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        LOG.info("SERVER_START pid=%s interpreter=%s", proc.pid, executable)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if running():
                LOG.info("READY mode=started pid=%s url=%s", proc.pid, URL)
                return
            code = proc.poll()
            if code is not None:
                details = server_error(server_log, offset)
                raise RuntimeError(f"后台服务启动失败，退出码 {code}。\n解释器：{executable}\n{details}")
            time.sleep(0.25)
        details = server_error(server_log, offset)
        raise RuntimeError(f"后台服务启动超过 {timeout:g} 秒，健康检查仍未通过。\n解释器：{executable}\n{details}")


def main():
    try:
        configure_log()
        LOG.info("START pid=%s interpreter=%s base_prefix=%s cwd=%s", os.getpid(), sys.executable, sys.base_prefix, Path.cwd())
        ensure_service()
        if "--no-browser" not in sys.argv:
            os.startfile(URL)
            LOG.info("BROWSER_OPEN url=%s", URL)
        return 0
    except Exception as exc:
        LOG.exception("EXCEPTION %s", exc)
        message = f"{exc}\n\n启动日志：{DATA / 'launcher.log'}\n后台日志：{DATA / 'server.log'}"
        ctypes.windll.user32.MessageBoxW(None, message, "译帧启动失败", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
