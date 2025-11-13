param(
    [string]$InstallRoot = 'C:\Program Files\marocco-player',
    [string]$TaskName = 'MaroccosProvision'
)

$LogDir = Join-Path $InstallRoot 'logs'
$ProvisionLogFile = Join-Path $LogDir 'provision.log'

function Write-ProvisionLog([string]$Level, [string]$Message) {
    try {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -Path $ProvisionLogFile -Value "$timestamp [$Level] $Message"
    } catch {
        # ignore
    }
}

function Show-UserWarning([string]$Message) {
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue
        [System.Windows.MessageBox]::Show(
            $Message,
            'Maroccos Provisioning',
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Warning
        ) | Out-Null
    } catch {
        Write-Host $Message
    }
}

$ScriptPath = Join-Path $InstallRoot 'tools\windows\provision_player.ps1'
if (-not (Test-Path $ScriptPath)) {
    Write-ProvisionLog 'ERROR' "Provisioning script not found: $ScriptPath"
    Show-UserWarning "Provisioning script mancante: $ScriptPath"
    exit 1
}

$innerCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -NonInteractive -InstallRoot `"$InstallRoot`" -ScheduledTaskName `"$TaskName`""
$taskRun = '"' + $innerCommand + '"'
$arguments = @(
    '/Create',
    '/TN', $TaskName,
    '/SC', 'ONLOGON',
    '/RL', 'HIGHEST',
    '/F',
    '/TR', $taskRun
)

try {
    $proc = Start-Process -FilePath 'schtasks.exe' -ArgumentList $arguments -NoNewWindow -Wait -PassThru -ErrorAction Stop
    if ($proc.ExitCode -eq 0) {
        Write-ProvisionLog 'OK' "Schedule '$TaskName' created successfully."
        exit 0
    } else {
        Write-ProvisionLog 'WARN' "Schedule '$TaskName' creation failed (exit $($proc.ExitCode))."
        Show-UserWarning "Creazione attività '$TaskName' fallita (exit $($proc.ExitCode)). Verifica manualmente."
        exit $proc.ExitCode
    }
} catch {
    Write-ProvisionLog 'ERROR' "Schedule '$TaskName' creation exception: $($_.Exception.Message)"
    Show-UserWarning "Creazione attività '$TaskName' generato un'eccezione: $($_.Exception.Message)"
    exit 1
}
