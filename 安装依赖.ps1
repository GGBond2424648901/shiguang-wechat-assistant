$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskUv = Get-Command uv -ErrorAction SilentlyContinue
if ($taskUv) {
    & $taskUv.Source sync --frozen --extra dev --link-mode copy
    if ($LASTEXITCODE -ne 0) { throw '项目依赖安装失败' }
} else {
    $taskPython = (Get-Command python -ErrorAction Stop).Source
    & $taskPython -c "import sys; assert sys.version_info >= (3,13), 'Python 3.13 or newer required'"
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.13 或更新版本' }
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & $taskPython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '无法创建项目私有环境' }
    }
    & '.venv/Scripts/python.exe' -m pip install --require-hashes -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw '项目依赖安装失败' }
}
& '.venv/Scripts/python.exe' -m xiaomao doctor
Write-Output '完成。双击 启动拾光微信聊天助手.vbs 或 启动拾光微信聊天助手.cmd。'
