# Windows x64 构建

在安装了 Tk 的 **64 位 CPython 3.13**、Node.js 24、uv、Inno Setup 6 和 Electron rcedit 的 Windows 上运行。发布验证使用 CPython 3.13.15、PyInstaller 6.22.3、Inno Setup 6.7.1。可通过官方 [Inno Setup 下载页](https://jrsoftware.org/isdl.php) 获取编译器，请核对安装文件的数字签名。

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe --require-hashes --only-binary :all: -r requirements.lock -r packaging/build-requirements.lock
.\packaging\build.ps1 -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' -ResourceEditor 'C:\Tools\rcedit-x64.exe'
```

构建脚本先按 `public-files.txt` 导出新的源码树，再在内部构建。公开源码包括审核过的模块、素材和自包含测试；本机截图、历史记录、私有配置、供应商参考仓库及未授权表情图片从不进入清单。出厂人格由随包的上游适配模板生成。每次导出都必须指定一个不存在的新目录。

```powershell
.\.venv\Scripts\python.exe packaging/export_public.py build/public-review
```

`dist` 包含用户安装包、独立源码压缩包及 SHA256SUMS.txt。应用安装在当前用户目录。独立的图形程序和控制台程序共用同一套 OCR 权重及运行库。打包脚本还会校验并附带 GEOS 3.14.1 源码，以及各 wheel 的许可证和第三方声明。初次构建需要网络，已核验的 GEOS 源码缓存在 `build/release-tools`，依赖可使用本机 uv 缓存。

安装目录可用不含密钥、微信窗口或网络模型的检查验证，传入其中包含 `Xiaomao.exe` 的文件夹：

```powershell
.\.venv\Scripts\python.exe -X utf8 packaging/smoke_installed.py "$env:LOCALAPPDATA\Programs\XiaomaoAssistant"
```

检查会对合成图片运行离线 OCR，并用临时配置和模拟只读控制端点打开、关闭实际的安装版面板。可用 `--entry vbs` 和 `--entry shortcut` 分别验证启动入口。测试配置与真实微信隔离。

v0.3.4 起，已有项目的 VBS 会登记原数据目录，安装快捷方式随后沿用它；登记文件只包含目录路径。VBS 同时校验程序及 renderer 构建版本，不满足时说明原因，不再自动打开旧界面。明确使用 `--root` 可以指定另一处数据。每个 Windows 桌面会话仅运行一份真实后台助手。

四角回归可运行 `node desktop/scripts/check-corner-pixels.cjs`，检查 100%、125%、150%、200% 下主窗、失焦、小弹窗、校准窗口和调整尺寸后的四角透明过渡，以及四边金边完整性。边界由 Chromium 合成，不使用整数矩形拼接的 Windows 裁切区域。
