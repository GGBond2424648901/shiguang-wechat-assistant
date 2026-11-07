param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Payload,
    [Parameter(Mandatory)][string]$Version,
    [string]$ElectronDist,
    [Parameter(Mandatory)][string]$ResourceEditor
)
$ErrorActionPreference = 'Stop'
$desktopSource = Join-Path $Source 'desktop'
$resourceEditorPath = (Resolve-Path -LiteralPath $ResourceEditor).Path
Push-Location -LiteralPath $desktopSource
try {
    $oldSkip = $env:ELECTRON_SKIP_BINARY_DOWNLOAD
    if ($ElectronDist) { $env:ELECTRON_SKIP_BINARY_DOWNLOAD = '1' }
    try {
        npm.cmd ci --no-audit --no-fund --prefer-offline
        if ($LASTEXITCODE) { throw 'Desktop dependency installation failed' }
    } finally { $env:ELECTRON_SKIP_BINARY_DOWNLOAD = $oldSkip }
    npm.cmd run build
    if ($LASTEXITCODE) { throw 'Desktop renderer build failed' }
} finally {
    Pop-Location
}
if (-not $ElectronDist) { $ElectronDist = Join-Path $desktopSource 'node_modules/electron/dist' }
$electronRuntime = (Resolve-Path -LiteralPath $ElectronDist).Path
$actual = (Get-Content -LiteralPath (Join-Path $electronRuntime 'version') -Raw).Trim()
$expected = (Get-Content -LiteralPath (Join-Path $desktopSource 'package.json') -Raw | ConvertFrom-Json).devDependencies.electron
if ($actual -ne $expected) { throw ('Electron version mismatch. Expected '+$expected+' but received '+$actual) }
$desktopPayload = Join-Path $Payload 'desktop'
New-Item -ItemType Directory -Path $desktopPayload | Out-Null
Copy-Item -Path (Join-Path $electronRuntime '*') -Destination $desktopPayload -Recurse
$runtimeExecutable = Join-Path $desktopPayload 'electron.exe'
Rename-Item -LiteralPath $runtimeExecutable -NewName 'ShiguangDesktop.exe'
$runtimeExecutable = Join-Path $desktopPayload 'ShiguangDesktop.exe'
$icon = Join-Path $Source 'xiaomao/ui_assets/app.ico'
& $resourceEditorPath $runtimeExecutable --set-icon $icon --set-file-version $Version --set-product-version $Version --set-version-string ProductName 'Shiguang WeChat Assistant' --set-version-string FileDescription 'Shiguang WeChat Assistant Desktop'
if ($LASTEXITCODE) { throw 'Failed to apply the original desktop app icon' }
$app = Join-Path $desktopPayload 'resources/app'
New-Item -ItemType Directory -Path $app | Out-Null
Copy-Item -LiteralPath (Join-Path $desktopSource 'electron'), (Join-Path $desktopSource 'dist') -Destination $app -Recurse
Copy-Item -LiteralPath $icon -Destination (Join-Path $app 'icon.ico')
[IO.File]::WriteAllText((Join-Path $app 'package.json'), ('{"name":"shiguang-desktop","productName":"拾光微信聊天助手","version":"'+$Version+'","main":"electron/main.cjs"}'), [Text.UTF8Encoding]::new($false))
$defaultApp = Join-Path $desktopPayload 'resources/default_app.asar'
if (Test-Path -LiteralPath $defaultApp) { Remove-Item -LiteralPath $defaultApp }
