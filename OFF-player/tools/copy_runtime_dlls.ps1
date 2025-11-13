param(
    [string]$Config = 'Release',
    [string]$MsysRoot = 'C:\msys64'
)

# Usa Stop di default ma gestisci gli errori di copia in modo robusto dentro Copy-IfExists
$ErrorActionPreference = 'Stop'

function To-MSYSPath([string]$winPath) {
    $full = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0,1).ToLower()
    $rest  = $full.Substring(2).Replace('\','/')
    return "/$drive/$rest"
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$binDir   = Join-Path $repoRoot 'OFF-player' | Join-Path -ChildPath 'bin'
$exeName  = 'OFF-player.exe'
$cfgUpper = $Config.ToUpperInvariant()
if ($cfgUpper -ne 'RELEASE') {
    throw "Config '$Config' non supportata: OFF-player viene distribuito solo in Release."
}
$exePath  = Join-Path $binDir $exeName

if (-not (Test-Path $exePath)) {
    Write-Error "Eseguibile non trovato: $exePath. Compila prima ($Config)."
}

if (-not (Test-Path $MsysRoot)) {
    Write-Error "MSYS2 non trovato in $MsysRoot. Installa MSYS2 o specifica -MsysRoot."
}

$bash = Join-Path $MsysRoot 'usr\bin\bash.exe'
if (-not (Test-Path $bash)) {
    Write-Warning "bash non trovato in $bash. Procedo con fallback (lista DLL comune)."
}

$copied = @()
function Prune-StaleDlls {
    param(
        [string]$Dir,
        [string[]]$Keep
    )

    if (-not (Test-Path -LiteralPath $Dir)) { return }
    $keepSet = $Keep | Sort-Object -Unique
    Get-ChildItem -LiteralPath $Dir -File -Filter '*.dll' -ErrorAction SilentlyContinue |
        Where-Object { $keepSet -notcontains $_.Name } |
        ForEach-Object {
            try {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                Write-Host "Rimosso DLL obsoleta: $($_.Name)" -ForegroundColor DarkGray
            } catch {
                Write-Warning "Impossibile rimuovere $($_.Name): $($_.Exception.Message)"
            }
        }
}

function Copy-IfExists([string]$src, [string]$destDir) {
    if (Test-Path $src) {
        $file = Split-Path $src -Leaf
        $dest = Join-Path $destDir $file
        # Ritenta alcune volte in caso di file temporaneamente bloccato da un processo
        for ($i = 0; $i -lt 4; $i++) {
            try {
                Copy-Item -Force -Path $src -Destination $dest -ErrorAction Stop
                return $dest
            }
            catch {
                $msg = $_.Exception.Message
                if ($msg -match 'in use' -or $msg -match 'utilizzo da un altro processo' -or $msg -match 'accesso negato') {
                    Start-Sleep -Milliseconds (150 * ($i + 1))
                    continue
                } else {
                    Write-Warning "Impossibile copiare ${file}: $msg"
                    break
                }
            }
        }
        Write-Warning "File bloccato, salto: $file"
    }
    return $null
}

# Prova a chiudere eventuali istanze dell'app in esecuzione che potrebbero bloccare le DLL
try {
    $targets = @('OFF-player')
    foreach ($t in $targets) {
        Get-Process -Name $t -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host ("Chiudo processo bloccante {0} (PID {1})" -f $_.ProcessName, $_.Id) -ForegroundColor Yellow
            try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {}
        }
    }
} catch {}

New-Item -ItemType Directory -Force -Path $binDir | Out-Null

if (Test-Path $bash) {
    $msysExe = To-MSYSPath $exePath
    $cmd = "ldd '$msysExe'"
    $env:PATH = "$MsysRoot\\mingw64\\bin;$MsysRoot\\usr\\bin;" + $env:PATH
    try {
        $lines = & $bash -lc $cmd
    } catch {
        Write-Warning "ldd fallito: $_"
        $lines = @()
    }

    $dllPaths = @()
    foreach ($line in $lines) {
        # es: "libPocoNet.dll => /mingw64/bin/libPocoNet.dll (0x...)"
        if ($line -match '=>\s+(/mingw64/bin/[^\s]+)') {
            $msysPath = $Matches[1]
            $winPath = ($msysPath -replace '^/mingw64', "$MsysRoot/mingw64").Replace('/','\')
            $dllPaths += $winPath
        }
        elseif ($line -match '([A-Za-z]:\\[^\s]+\.dll)') {
            # percorso Windows assoluto già risolto da ldd
            $dllPaths += $Matches[1]
        }
    }

    $dllPaths = $dllPaths | Sort-Object -Unique
    foreach ($p in $dllPaths) {
        $dest = Copy-IfExists $p $binDir
        if ($dest) { $copied += (Split-Path $dest -Leaf) }
    }
}

if ($copied.Count -eq 0) {
    Write-Warning "Fallback: copia dll comuni. Potrebbero non bastare: esegui il programma e aggiungi le mancanti se richiesto."
    $common = @(
        'libstdc++-6.dll',
        'libgcc_s_seh-1.dll',
        'libwinpthread-1.dll',
        'libPocoFoundation.dll',
        'libPocoUtil.dll',
        'libPocoXML.dll',
        'libPocoNet.dll',
        'FreeImage.dll'
    )
    foreach ($name in $common) {
        $src = Join-Path "$MsysRoot\mingw64\bin" $name
        $dest = Copy-IfExists $src $binDir
        if ($dest) { $copied += (Split-Path $dest -Leaf) }
    }
}

if ($copied.Count -gt 0) {
    Write-Host "DLL copiate in ${binDir}:" -ForegroundColor Green
    $copied | Sort-Object -Unique | ForEach-Object { Write-Host " - $_" }
    Prune-StaleDlls -Dir $binDir -Keep $copied
} else {
    Write-Warning "Nessuna DLL copiata. Forse sono già presenti o l'analisi non ha trovato dipendenze."
}
