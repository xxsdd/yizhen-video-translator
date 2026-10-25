$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '需要 uv。请安装 uv 后再运行本脚本。' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw '需要 FFmpeg，并将其 bin 目录加入 PATH。' }
uv venv --python 3.12 '.venv'
if ($LASTEXITCODE -ne 0) { throw 'Python 环境创建失败。' }
uv pip install --python '.venv\Scripts\python.exe' -r 'requirements-lock.txt'
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
& '.venv\Scripts\python.exe' -X utf8 prepare_dictionary.py
if ($LASTEXITCODE -ne 0) { throw '离线词典准备失败，请检查网络后重试。' }
Write-Host '安装完成，双击 启动译帧.cmd 即可使用。'
