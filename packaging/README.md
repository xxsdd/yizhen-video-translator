# Windows 安装包构建

构建使用已验证的 Python 3.12 x64 独立运行时、项目虚拟环境中的锁定依赖、FFmpeg 构建和纯 ECDICT 词库。最终用户无需安装 Python、uv 或 FFmpeg。

```powershell
python packaging/build_windows.py `
  --output D:/YizhenBuild/0.1.1 `
  --python-base <独立Python312目录> `
  --site-packages .venv/Lib/site-packages `
  --ffmpeg <包含bin和LICENSE的FFmpeg目录> `
  --dictionary data/dictionaries

ISCC /DPayloadDir=D:\YizhenBuild\0.1.1\payload /DReleaseDir=D:\YizhenBuild\0.1.1\release packaging/yizhen.iss
```

需要 Inno Setup 7。构建输出目录必须是新的；构建器不会删除已有文件。安装包包含运行时各依赖的许可证、FFmpeg 构建说明和 ECDICT 许可证，不包含模型、应用数据库、字幕或账号配置。

`payload-manifest.json` 记录每个安装文件的大小和 SHA256。发布前应验证安装后的文件与该清单一致，使用安装后的独立解释器启动，验证本地网页、媒体处理和语音识别，并生成安装包校验文件。

测试可设置 `VTS_PORT` 使用另一个本地端口、设置 `VTS_DATA_DIR` 使用隔离数据目录。不要用已有的健康服务冒充新安装包启动成功。安装版 `launch.py` 对全新设置使用 CPU 默认值；不会覆盖已有设置。

默认安装位置为 `D:\Apps\Yizhen`（存在 D 盘时）或当前用户的本地 Programs 目录。安装器不请求管理员权限，可选择其他可写位置。卸载只移除安装器管理的程序文件，保留用户生成的数据和模型。
