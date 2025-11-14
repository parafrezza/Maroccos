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

function From-MSYSPath([string]$msysPath) {
    if (-not $msysPath) { return $null }

    if ($msysPath -match '^/[a-zA-Z]/') {
    $drive = $msysPath.Substring(1,1).ToUpper()
    $rest  = $msysPath.Substring(2).Replace('/','\')
    return "${drive}:${rest}"
    }

    if ($msysPath -like '/mingw64/*') {
        return ($msysPath -replace '^/mingw64', "$MsysRoot/mingw64").Replace('/', '\')
    }

    if ($msysPath -like '/usr/*') {
        return ($msysPath -replace '^/usr', "$MsysRoot/usr").Replace('/', '\')
    }

    return $null
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

function Copy-IfExists([string]$src, [string]$destDir) {
    if (-not $src) { return $null }
    if (-not (Test-Path $src)) { return $null }

    $file = Split-Path $src -Leaf
    $dest = Join-Path $destDir $file
    $destFull = [System.IO.Path]::GetFullPath($dest)
    $srcFull = [System.IO.Path]::GetFullPath($src)

    if ($srcFull -ieq $destFull) {
        return $destFull
    }

    if (Test-Path $dest) {
        try {
            $srcInfo = Get-Item $src -ErrorAction Stop
            $destInfo = Get-Item $dest -ErrorAction Stop
            if ($srcInfo.Length -eq $destInfo.Length -and $srcInfo.LastWriteTimeUtc -eq $destInfo.LastWriteTimeUtc) {
                return $destFull
            }
        } catch {}
    }

    # Ritenta alcune volte in caso di file temporaneamente bloccato da un processo
    for ($i = 0; $i -lt 4; $i++) {
        try {
            Copy-Item -Force -Path $src -Destination $dest -ErrorAction Stop
            return [System.IO.Path]::GetFullPath($dest)
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
    return $null
}

function Find-MsysDependency([string]$fileName) {
    $searchRoots = @(
        (Join-Path $MsysRoot 'mingw64\bin'),
        (Join-Path $MsysRoot 'mingw64\lib'),
        (Join-Path $MsysRoot 'usr\bin')
    )

    foreach ($root in $searchRoots) {
        $candidate = Join-Path $root $fileName
        if (Test-Path $candidate) { return $candidate }
    }

    try {
        $found = Get-ChildItem -Path (Join-Path $MsysRoot 'mingw64') -Filter $fileName -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    } catch {}

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
    $env:PATH = "$MsysRoot\\mingw64\\bin;$MsysRoot\\usr\\bin;" + $env:PATH

    $queue = [System.Collections.Queue]::new()
    $queue.Enqueue([System.IO.Path]::GetFullPath($exePath))
    $processed = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $missing = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

    while ($queue.Count -gt 0) {
        $target = $queue.Dequeue()
        if (-not (Test-Path $target)) { continue }
        if (-not $processed.Add($target)) { continue }

        Write-Verbose ("Analisi dipendenze: {0}" -f $target)
        $msysTarget = To-MSYSPath $target
        try {
            $lines = & $bash -lc "ldd '$msysTarget'"
        } catch {
            Write-Warning ("ldd fallito su {0}: {1}" -f $target, $_)
            continue
        }

        foreach ($line in $lines) {
            Write-Verbose ("   ldd: {0}" -f $line)
            $sourcePath = $null
            $dllName = $null

            if ($line -match '=>\s+(/[^\s]+\.dll)') {
                $msysPath = $Matches[1]
                $dllName = Split-Path $msysPath -Leaf
                $sourcePath = From-MSYSPath $msysPath
            }
            elseif ($line -match '([A-Za-z]:\\[^\s]+\.dll)') {
                $sourcePath = $Matches[1]
                $dllName = Split-Path $sourcePath -Leaf
            }
            elseif ($line -match '^\s*([^\s]+\.dll)\s+=>\s+not found') {
                $dllName = $Matches[1]
                $sourcePath = Find-MsysDependency $dllName
            }

            if (-not $dllName) { continue }

            $destPath = Join-Path $binDir $dllName
            $destPath = [System.IO.Path]::GetFullPath($destPath)

            if ($sourcePath -and ($sourcePath -match '^[A-Za-z]:\\Windows\\')) {
                # Librerie di sistema: non copiarle, ma segnala solo se già assenti nel bin
                if (Test-Path $destPath) {
                    if (-not $processed.Contains($destPath)) { $queue.Enqueue($destPath) }
                }
                continue
            }

            if ($sourcePath -and (Test-Path $sourcePath)) {
                Write-Verbose (" - dipendenza {0} -> {1}" -f $dllName, $sourcePath)
                $copiedPath = Copy-IfExists $sourcePath $binDir
                if ($copiedPath) {
                    $copied += (Split-Path $copiedPath -Leaf)
                    $copiedPathFull = [System.IO.Path]::GetFullPath($copiedPath)
                    if (-not $processed.Contains($copiedPathFull)) { $queue.Enqueue($copiedPathFull) }
                    continue
                }
            }

            if (Test-Path $destPath) {
                if (-not $processed.Contains($destPath)) { $queue.Enqueue($destPath) }
            } else {
                $missing.Add($dllName) | Out-Null
                Write-Verbose (" - dipendenza NON trovata: {0}" -f $dllName)
            }
        }
    }

    if ($missing.Count -gt 0) {
        $missing | Sort-Object | ForEach-Object { Write-Warning "DLL non trovata in MSYS2: $_" }
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
} else {
    Write-Warning "Nessuna DLL copiata. Forse sono già presenti o l'analisi non ha trovato dipendenze."
}
