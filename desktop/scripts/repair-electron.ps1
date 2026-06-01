$ErrorActionPreference = "Stop"

$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$electronRoot = (Resolve-Path (Join-Path $desktopDir "node_modules\electron")).Path
$distDir = Join-Path $electronRoot "dist"
$package = Get-Content (Join-Path $electronRoot "package.json") -Raw | ConvertFrom-Json
$version = $package.version
$zipName = "electron-v$version-win32-x64.zip"
$cacheRoot = Join-Path $env:LOCALAPPDATA "electron\Cache"
$zip = Get-ChildItem -LiteralPath $cacheRoot -Recurse -Filter $zipName | Select-Object -First 1

if (-not $electronRoot.StartsWith($desktopDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Electron root fora do desktop workspace: $electronRoot"
}

if (-not $zip) {
    throw "Cache Electron ausente: $zipName. Rode npm install antes deste reparo."
}

if (Test-Path -LiteralPath $distDir) {
    $resolvedDist = (Resolve-Path -LiteralPath $distDir).Path
    if (-not $resolvedDist.StartsWith($electronRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Dist fora do electron root: $resolvedDist"
    }
    Remove-Item -LiteralPath $resolvedDist -Recurse -Force
}

New-Item -ItemType Directory -Path $distDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip.FullName, $distDir)
Set-Content -LiteralPath (Join-Path $electronRoot "path.txt") -Value "electron.exe" -NoNewline -Encoding ascii

$electronExe = Join-Path $distDir "electron.exe"
if (-not (Test-Path -LiteralPath $electronExe)) {
    throw "electron.exe nao foi extraido."
}

Write-Host "[OK] Electron $version reparado em $electronExe"
