param(
    [string]$MsysRoot = 'C:\msys64'
)

$ErrorActionPreference = 'Stop'

function Find-OFRoot {
    param([string]$offRootDir)
    if (-not (Test-Path $offRootDir)) {
        throw "OFF-ROOT non trovato: $offRootDir"
    }
    $candidates = Get-ChildItem -Path $offRootDir -Directory | Where-Object {
        $_.Name -match '^of_v[0-9]+\.[0-9]+\.[0-9]+_msys2_mingw64_release$'
    } | Sort-Object Name -Descending
    if ($candidates.Count -eq 0) {
        throw "Nessuna release MSYS2 trovata in $offRootDir (attesa *_msys2_mingw64_release)"
    }
    return $candidates[0].FullName
}

function Convert-ToMsysPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = $full.Substring(0,1).ToLower()
    $rest  = $full.Substring(2).TrimStart('\\')
    $rest  = $rest -replace '\\', '/'
    return "/$drive/$rest"
}

function Invoke-BashBuild {
    param(
        [string]$MsysRoot,
        [string]$RepoRoot,
        [string]$OFRoot,
        [int]$Jobs = 1,
        [ValidateSet('Debug','Release')] [string]$Config = 'Release'
    )
    $bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
    if (-not (Test-Path $bash)) { throw "bash non trovato: $bash" }

    $env:PATH = "$MsysRoot\\mingw64\\bin;$MsysRoot\\usr\\bin;" + $env:PATH

    $wsU = Convert-ToMsysPath -Path $RepoRoot
    $ofU = Convert-ToMsysPath -Path $OFRoot

    $makeCmd = "export MSYSTEM=MINGW64; export PATH=/mingw64/bin:/usr/bin:$PATH; cd '$wsU/OFF-player'; make -j$Jobs $Config OF_ROOT='$ofU'"

    & $bash -lc $makeCmd
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$offRootDir = Join-Path $repoRoot 'OFF-ROOT'
$ofRoot = Find-OFRoot -offRootDir $offRootDir

$cpu = [Environment]::ProcessorCount
$jobs = [Math]::Max(1, $cpu - 1)

Write-Host "[1/1] Compilazione OFF-player ($jobs job) usando OF_ROOT: $ofRoot" -ForegroundColor Cyan
Invoke-BashBuild -MsysRoot $MsysRoot -RepoRoot $repoRoot -OFRoot $ofRoot -Jobs $jobs -Config 'Release'
Write-Host "OFF-player build completata." -ForegroundColor Green
