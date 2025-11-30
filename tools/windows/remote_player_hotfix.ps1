#requires -Version 5.1

[CmdletBinding()]
param(
	[string[]]$Targets,
	[string]$Target,
	[string]$DiscoveryPrefix = '192.168.1.',
	[int]$DiscoveryStart = 1,
	[int]$DiscoveryEnd = 254,
	[string]$InstallRoot = 'C:\Program Files\marocco-player',
	[string]$Username = 'extra',
	[string]$Password = 'extra',
	[string[]]$LocalFiles = @('tools\windows\headless_task_helpers.ps1', 'tools\windows\provision_player.ps1', 'tools\windows\register_headless_task.ps1'),
	[string]$RemotePowerShellCommand = '& "C:\Program Files\marocco-player\tools\windows\provision_player.ps1" -NonInteractive',
	[switch]$SkipRunNow,
	[switch]$AutoYes,
	[switch]$CollectLogsOnFailure
)

$ErrorActionPreference = 'Stop'
$script:Sessions = @()
$script:SftpSessions = @()
$report = @()
$localLogRoot = $null
if ($CollectLogsOnFailure) {
	$localLogRoot = Join-Path (Get-Location).Path 'remote_hotfix_logs'
	try { New-Item -ItemType Directory -Path $localLogRoot -Force | Out-Null } catch {}
}

function Ensure-PoshSSH {
	if (-not (Get-Module -ListAvailable -Name Posh-SSH)) {
		Write-Host '[INFO] Installazione modulo Posh-SSH (prima esecuzione)...'
		try {
			Install-Module -Name Posh-SSH -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop
		} catch {
			throw "Impossibile installare il modulo Posh-SSH: $($_.Exception.Message)"
		}
	}
	Import-Module Posh-SSH -ErrorAction Stop
}

function ConvertTo-EncodedCommand {
	param([string]$ScriptText)
	$bytes = [System.Text.Encoding]::Unicode.GetBytes($ScriptText)
	return [System.Convert]::ToBase64String($bytes)
}

function Convert-ToSftpRemotePath {
	param([string]$Path)
	if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
	$normalized = $Path -replace '\\','/'
	if ($normalized -match '^[A-Za-z]:') {
		return ('/' + $normalized)
	}
	return $normalized
}

function Get-RemoteProvisionLogPath {
	param($Session)
	$script = @"
$desktop = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktop)) {
	$userProfile = $Env:USERPROFILE
	if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
		$desktop = Join-Path $userProfile 'Desktop'
	}
}
if ([string]::IsNullOrWhiteSpace($desktop)) { return }
$logDir = Join-Path $desktop 'media\\_logs'
$logFile = Join-Path $logDir 'provision.log'
if (Test-Path $logFile) {
	Write-Output $logFile
}
"@
	try {
		$result = Invoke-RemotePSScript -Session $Session -ScriptText $script
		if ($result.Output) { return $result.Output.Trim() }
	} catch {}
	return $null
}

function Collect-RemoteProvisionLog {
	param(
		$Session,
		$SftpSession,
		[string]$Computer,
		[string]$LocalRoot
	)
	if (-not $LocalRoot) { return }
	$remotePath = Get-RemoteProvisionLogPath -Session $Session
	if (-not $remotePath) {
		Write-Warning ("    [WARN] Log provisioning non trovato su {0}" -f $Host)
		return
	}
	$remoteSftpPath = Convert-ToSftpRemotePath -Path $remotePath
	if (-not $remoteSftpPath) { return }
	$hostDir = Join-Path $LocalRoot ($Computer -replace '[^0-9A-Za-z_.-]', '_')
	try { New-Item -ItemType Directory -Path $hostDir -Force | Out-Null } catch {}
	$localFile = Join-Path $hostDir (Split-Path $remotePath -Leaf)
	try {
		Get-SFTPFile -SFTPSession $SftpSession -RemoteFile $remoteSftpPath -LocalFile $localFile -Overwrite -Confirm:$false
		Write-Host ("    [LOG] Scaricato {0} -> {1}" -f $remotePath, $localFile)
	} catch {
		Write-Warning ("    [WARN] Download log da {0} fallito: {1}" -f $Computer, $_.Exception.Message)
	}
}

function Invoke-RemotePSScript {
	param(
		[Parameter(Mandatory = $true)]$Session,
		[Parameter(Mandatory = $true)][string]$ScriptText
	)
	$encoded = ConvertTo-EncodedCommand -ScriptText $ScriptText
	return Invoke-SSHCommand -SSHSession $Session -Command "powershell.exe -NoProfile -EncodedCommand $encoded" -ErrorAction Stop
}

function Discover-Targets {
	param(
		[string[]]$SeedTargets,
		[string]$Prefix,
		[int]$Start,
		[int]$End
	)
	if ($SeedTargets -and $SeedTargets.Count -gt 0) {
		return $SeedTargets | Sort-Object -Unique
	}
	Write-Host ("[INFO] Scansione indirizzi {0}{1}-{2}..." -f $Prefix, $Start, $End)
	$found = @()
	for ($i = $Start; $i -le $End; $i++) {
		$candidate = "$Prefix$i"
		if (-not (Test-Connection -ComputerName $candidate -Count 1 -Quiet -ErrorAction SilentlyContinue)) { continue }
		if (Test-NetConnection -ComputerName $candidate -Port 22 -InformationLevel Quiet -ErrorAction SilentlyContinue) {
			$found += $candidate
			Write-Host "  [+] $candidate (porta 22 aperta)"
		}
	}
	return $found | Sort-Object -Unique
}

function Resolve-LocalFiles {
	param([string[]]$Paths)
	$resolved = @()
	foreach ($path in $Paths) {
		if (-not $path) { continue }
		try {
			$item = Get-Item -LiteralPath $path -ErrorAction Stop
			$resolved += $item
		} catch {
			throw "File locale non trovato: $path"
		}
	}
	return $resolved
}

function Get-RemoteVersion {
	param($Session, [string]$Root)
	$script = @"
$root = '$Root'
$paths = @(
	Join-Path $root 'VERSION',
	Join-Path $root 'headless-player\VERSION',
	Join-Path $root 'GUI\VERSION'
)
foreach ($p in $paths) {
	if (Test-Path $p) {
		try {
			$line = (Get-Content -Path $p -ErrorAction Stop | Select-Object -First 1)
			Write-Output "$(Split-Path -Path $p -Leaf):$line"
			break
		} catch {}
	}
}
"@
	try {
		$result = Invoke-RemotePSScript -Session $Session -ScriptText $script
		if ($result.Output) { return $result.Output.Trim() }
	} catch {}
	return '<n/d>'
}

function Ensure-RemoteToolsDirectory {
	param($Session, [string]$Root)
	$script = "New-Item -ItemType Directory -Path '$Root' -Force | Out-Null"
	Invoke-RemotePSScript -Session $Session -ScriptText $script | Out-Null
}

function Copy-HotfixFiles {
	param(
		$SftpSession,
		[System.IO.FileInfo[]]$LocalItems,
		[string]$RemoteDir
	)
	foreach ($item in $LocalItems) {
		$remotePath = Join-Path $RemoteDir $item.Name
		Write-Host ("    [COPY] {0} -> {1}" -f $item.FullName, $remotePath)
		Set-SFTPFile -SFTPSession $SftpSession -LocalFile $item.FullName -RemoteFile $remotePath -Overwrite -Confirm:$false
	}
}

function Try-RunRemoteCommand {
	param($Session, [string]$Command)
	$payload = ConvertTo-EncodedCommand -ScriptText $Command
	$launcher = @"
try {
	$ProgressPreference = 'SilentlyContinue'
	$ErrorActionPreference = 'Stop'
	powershell.exe -NoProfile -EncodedCommand $payload
	if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch {
	Write-Error $_.Exception.Message
	exit 1
}
"@
	try {
		$response = Invoke-RemotePSScript -Session $Session -ScriptText $launcher
		return $response.ExitStatus -eq 0
	} catch {
		Write-Warning ("    [WARN] Esecuzione diretta fallita: {0}" -f $_.Exception.Message)
		return $false
	}
}

function Schedule-RemoteHotfix {
	param($Session, [string]$Command)
	$encodedPayload = ConvertTo-EncodedCommand -ScriptText $Command
 	$script = @"
$taskName = 'MaroccosHotfixOnce'
try {
	if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
		Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
	}
} catch {}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -EncodedCommand $encodedPayload"
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -RunLevel Highest -User 'SYSTEM' -Force | Out-Null
"@
	Invoke-RemotePSScript -Session $Session -ScriptText $script | Out-Null
	Invoke-RemotePSScript -Session $Session -ScriptText "shutdown.exe /r /t 30 /c 'Applicazione hotfix Maroccos'" | Out-Null
}

Ensure-PoshSSH
$effectiveTargets = if ($Target) { @($Target) } else { $Targets }
$localItems = Resolve-LocalFiles -Paths $LocalFiles
$hosts = Discover-Targets -SeedTargets $effectiveTargets -Prefix $DiscoveryPrefix -Start $DiscoveryStart -End $DiscoveryEnd
if (-not $hosts -or $hosts.Count -eq 0) {
	Write-Warning 'Nessun host trovato.'
	return
}

$securePass = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential($Username, $securePass)

foreach ($targetHost in $hosts) {
	Write-Host "\n=== $targetHost ===" -ForegroundColor Cyan
	try {
		$session = New-SSHSession -ComputerName $targetHost -Credential $credential -AcceptKey -Force -ErrorAction Stop
		$script:Sessions += $session
	} catch {
		Write-Warning ("Impossibile aprire sessione SSH su {0}: {1}" -f $targetHost, $_.Exception.Message)
		$report += [pscustomobject]@{ Host=$targetHost; Version='<n/d>'; Status='SSH FAILED' }
		continue
	}

	try {
		$version = Get-RemoteVersion -Session $session -Root $InstallRoot
		Write-Host "  Versione corrente: $version"

		$answer = if ($AutoYes) { 'y' } else { Read-Host '  Aggiornare questo player? [y/N]' }
		if ($answer.ToLower() -ne 'y') {
			Write-Host '  [SKIP] Nessuna azione.'
			$report += [pscustomobject]@{ Host=$host; Version=$version; Status='Skipped' }
			continue
		}

		$sftp = New-SFTPSession -ComputerName $targetHost -Credential $credential -AcceptKey -ErrorAction Stop
		$script:SftpSessions += $sftp
		$remoteTools = Join-Path $InstallRoot 'tools\windows'
		Ensure-RemoteToolsDirectory -Session $session -Root $remoteTools
		Copy-HotfixFiles -SftpSession $sftp -LocalItems $localItems -RemoteDir $remoteTools

		$executed = $false
		if (-not $SkipRunNow) {
			Write-Host '  [RUN] Avvio immediato comando remoto...'
			$executed = Try-RunRemoteCommand -Session $session -Command $RemotePowerShellCommand
		}

		if ($executed) {
			Write-Host '  [OK] Comando completato senza errori.' -ForegroundColor Green
			$report += [pscustomobject]@{ Host=$targetHost; Version=$version; Status='Updated+Executed' }
		} else {
			Write-Host '  [INFO] Programmo esecuzione al prossimo riavvio e avvio reboot...' -ForegroundColor Yellow
			Schedule-RemoteHotfix -Session $session -Command $RemotePowerShellCommand
			$report += [pscustomobject]@{ Host=$targetHost; Version=$version; Status='Scheduled+Reboot' }
		}

	} catch {
		Write-Warning ("Errore durante aggiornamento {0}: {1}" -f $targetHost, $_.Exception.Message)
		if ($CollectLogsOnFailure -and $localLogRoot -and $session) {
			try {
				if (-not $sftp) {
					$sftp = New-SFTPSession -ComputerName $targetHost -Credential $credential -AcceptKey -ErrorAction Stop
					$script:SftpSessions += $sftp
				}
				if ($sftp) {
					Collect-RemoteProvisionLog -Session $session -SftpSession $sftp -Computer $targetHost -LocalRoot $localLogRoot
				}
			} catch {
				Write-Warning ("    [WARN] Raccolta log non riuscita su {0}: {1}" -f $targetHost, $_.Exception.Message)
			}
		}
		$report += [pscustomobject]@{ Host=$targetHost; Version=$version; Status='ERROR' }
	}
}

Write-Host "\n=== RIEPILOGO ===" -ForegroundColor Cyan
$report | Format-Table -AutoSize

foreach ($s in $script:SftpSessions) { try { Remove-SFTPSession -SFTPSession $s } catch {} }
foreach ($s in $script:Sessions) { try { Remove-SSHSession -SSHSession $s } catch {} }