[CmdletBinding()]
param(
    [string]$Python,
    [Parameter(Mandatory)][string]$Iscc
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
if (-not $Python) { $Python = Join-Path $repo '.venv/Scripts/python.exe' }
$py = (Resolve-Path -LiteralPath $Python).Path
$compiler = (Resolve-Path -LiteralPath $Iscc).Path
$runId = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$job = Join-Path $repo "build/release-$runId"
$stage = Join-Path $job 'source'
$dest = Join-Path $repo 'dist'
$bundle = Join-Path $job 'bundle'
New-Item -Type Directory -Force $job, $dest | Out-Null
$version = (& $py -c "import sys,tomllib;print(tomllib.load(open(sys.argv[1], 'rb'))['project']['version'])" (Join-Path $repo 'pyproject.toml')).Trim()
if ($LASTEXITCODE -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version' }
$archive = Join-Path $dest "Xiaomao-WeChat-Assistant-$version-source.zip"
& $py "$PSScriptRoot/export_public.py" $stage --archive $archive
if ($LASTEXITCODE) { throw 'Public source export failed' }
Push-Location -LiteralPath $stage
try {
    & $py -m PyInstaller --noconfirm --distpath $bundle --workpath "$job/work" 'packaging/Xiaomao.spec'
    if ($LASTEXITCODE) { throw 'Application freeze failed' }
    $payload = Join-Path $bundle 'Xiaomao'
    & $py 'packaging/collect_notices.py' $payload (Join-Path $repo 'build/release-tools')
    if ($LASTEXITCODE) { throw 'Distribution notices failed' }
    $compilerLicense = Join-Path (Split-Path $compiler) 'license.txt'
    Copy-Item -LiteralPath $compilerLicense -Destination (Join-Path $payload 'licenses/Inno-Setup.txt')
    & $compiler "/DAppVersion=$version" "/DPayload=$payload" "/DOutDir=$dest" "/DAppIcon=$stage/xiaomao/ui_assets/app.ico" 'packaging/setup.iss'
    if ($LASTEXITCODE) { throw 'Installer compilation failed' }
    $installer = Join-Path $dest "Xiaomao-WeChat-Assistant-$version-windows-x64-setup.exe"
    @($installer,$archive) | ForEach-Object {
        $item=Get-FileHash -Algorithm SHA256 -LiteralPath $_
        $item.Hash.ToLowerInvariant() + '  ' + (Split-Path $item.Path -Leaf)
    } | Set-Content -Encoding ascii -LiteralPath (Join-Path $dest 'SHA256SUMS.txt')
    Write-Host "Release output: $dest"
    Write-Host "Reviewed public source: $stage"
} finally {
    Pop-Location
}
