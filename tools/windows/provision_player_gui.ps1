#requires -version 5.1
<#
    provision_player_gui.ps1 - Interfaccia grafica per il provisioning dei player Windows.

    - Avvia "provision_player.ps1" in modalita' non interattiva.
    - Mostra output e avanzamento step-by-step in una finestra WinForms.
    - Permette di personalizzare alcuni parametri (IP statico, gateway, DNS, flag Skip*).

    Uso:
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\provision_player_gui.ps1

    Packaging (facoltativo):
        Install-Module ps2exe -Scope CurrentUser
        ps2exe -inputFile .\provision_player_gui.ps1 -outputFile .\provision_player_gui.exe -iconFile .\icon.ico

    Nota: lo script di provisioning richiede privilegi amministrativi. Se esegui la GUI da una sessione non elevata,
    il processo figlio fallira' con l'errore "Esegui questo script come Amministratore".
#>

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$provisionScript = Join-Path $scriptRoot 'provision_player.ps1'
if (-not (Test-Path $provisionScript)) {
    [System.Windows.Forms.MessageBox]::Show(
        "Impossibile trovare provision_player.ps1 in: $provisionScript",
        'Provisioning GUI',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

# Analizza il provisioning script per estrarre l'ordine degli step (Write-Step).
$scriptText = Get-Content -Path $provisionScript -Raw
$stepRegex = [System.Text.RegularExpressions.Regex]::new('Write-Step\s+(?:''([^'']+)''|"([^"]+)")', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
$orderedSteps = New-Object System.Collections.Generic.List[string]
foreach ($match in $stepRegex.Matches($scriptText)) {
    $name = if ($match.Groups[1].Success) { $match.Groups[1].Value.Trim() } else { $match.Groups[2].Value.Trim() }
    if ($name -and -not $orderedSteps.Contains($name)) {
        $orderedSteps.Add($name)
    }
}
if ($orderedSteps.Count -eq 0) {
    $orderedSteps.Add('Inizializzazione')
}

# --- UI setup -----------------------------------------------------------------
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Maroccos Player Provisioning Monitor'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(920, 640)
$form.MinimumSize = New-Object System.Drawing.Size(780, 520)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$groupOptions = New-Object System.Windows.Forms.GroupBox
$groupOptions.Text = 'Parametri provisioning'
$groupOptions.Dock = 'Top'
$groupOptions.Height = 160

$tableLayout = New-Object System.Windows.Forms.TableLayoutPanel
$tableLayout.Dock = 'Fill'
$tableLayout.ColumnCount = 4
$tableLayout.RowCount = 3
$tableLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 15)))
$tableLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 35)))
$tableLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 15)))
$tableLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 35)))
$tableLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 32)))
$tableLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 32)))
$tableLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 32)))

$labelInstallRoot = New-Object System.Windows.Forms.Label
$labelInstallRoot.Text = 'Install root:'
$labelInstallRoot.Dock = 'Fill'
$labelInstallRoot.TextAlign = 'MiddleLeft'

$textInstallRoot = New-Object System.Windows.Forms.TextBox
$textInstallRoot.Text = 'C:\\Program Files\\marocco-player'
$textInstallRoot.Dock = 'Fill'

$labelStaticIp = New-Object System.Windows.Forms.Label
$labelStaticIp.Text = 'Static IP:'
$labelStaticIp.Dock = 'Fill'
$labelStaticIp.TextAlign = 'MiddleLeft'

$textStaticIp = New-Object System.Windows.Forms.TextBox
$textStaticIp.Text = '192.168.8.221'
$textStaticIp.Dock = 'Fill'

$labelGateway = New-Object System.Windows.Forms.Label
$labelGateway.Text = 'Gateway:'
$labelGateway.Dock = 'Fill'
$labelGateway.TextAlign = 'MiddleLeft'

$textGateway = New-Object System.Windows.Forms.TextBox
$textGateway.Text = '192.168.8.1'
$textGateway.Dock = 'Fill'

$labelPrefix = New-Object System.Windows.Forms.Label
$labelPrefix.Text = 'Prefix length:'
$labelPrefix.Dock = 'Fill'
$labelPrefix.TextAlign = 'MiddleLeft'

$numericPrefix = New-Object System.Windows.Forms.NumericUpDown
$numericPrefix.Minimum = 8
$numericPrefix.Maximum = 30
$numericPrefix.Value = 24
$numericPrefix.Dock = 'Fill'

$labelDns = New-Object System.Windows.Forms.Label
$labelDns.Text = 'DNS (comma):'
$labelDns.Dock = 'Fill'
$labelDns.TextAlign = 'MiddleLeft'

$textDns = New-Object System.Windows.Forms.TextBox
$textDns.Text = '8.8.8.8,1.1.1.1'
$textDns.Dock = 'Fill'

$checkSkipVnc = New-Object System.Windows.Forms.CheckBox
$checkSkipVnc.Text = 'Skip TigerVNC'
$checkSkipVnc.AutoSize = $true

$checkSkipShare = New-Object System.Windows.Forms.CheckBox
$checkSkipShare.Text = 'Skip media share'
$checkSkipShare.AutoSize = $true

$checkSkipHostname = New-Object System.Windows.Forms.CheckBox
$checkSkipHostname.Text = 'Skip hostname sync'
$checkSkipHostname.AutoSize = $true

$optionsFlow = New-Object System.Windows.Forms.FlowLayoutPanel
$optionsFlow.Dock = 'Fill'
$optionsFlow.FlowDirection = [System.Windows.Forms.FlowDirection]::LeftToRight
$optionsFlow.WrapContents = $false
$optionsFlow.Padding = New-Object System.Windows.Forms.Padding(0)
$optionsFlow.Controls.Add($checkSkipVnc)
$optionsFlow.Controls.Add($checkSkipShare)
$optionsFlow.Controls.Add($checkSkipHostname)

$tableLayout.Controls.Add($labelInstallRoot, 0, 0)
$tableLayout.Controls.Add($textInstallRoot, 1, 0)
$tableLayout.Controls.Add($labelStaticIp, 2, 0)
$tableLayout.Controls.Add($textStaticIp, 3, 0)
$tableLayout.Controls.Add($labelGateway, 0, 1)
$tableLayout.Controls.Add($textGateway, 1, 1)
$tableLayout.Controls.Add($labelPrefix, 2, 1)
$tableLayout.Controls.Add($numericPrefix, 3, 1)
$tableLayout.Controls.Add($labelDns, 0, 2)
$tableLayout.Controls.Add($textDns, 1, 2)
$tableLayout.Controls.Add($optionsFlow, 2, 2)
$tableLayout.SetColumnSpan($optionsFlow, 2)

$groupOptions.Controls.Add($tableLayout)

$buttonPanel = New-Object System.Windows.Forms.FlowLayoutPanel
$buttonPanel.Dock = 'Top'
$buttonPanel.Height = 40
$buttonPanel.FlowDirection = [System.Windows.Forms.FlowDirection]::RightToLeft
$buttonPanel.Padding = New-Object System.Windows.Forms.Padding(5)

$buttonStart = New-Object System.Windows.Forms.Button
$buttonStart.Text = 'Avvia provisioning'
$buttonStart.AutoSize = $true

$buttonCancel = New-Object System.Windows.Forms.Button
$buttonCancel.Text = 'Annulla'
$buttonCancel.AutoSize = $true
$buttonCancel.Enabled = $false

$buttonPanel.Controls.Add($buttonStart)
$buttonPanel.Controls.Add($buttonCancel)

$logBox = New-Object System.Windows.Forms.RichTextBox
$logBox.Dock = 'Fill'
$logBox.BackColor = [System.Drawing.Color]::Black
$logBox.ForeColor = [System.Drawing.Color]::Gainsboro
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9)
$logBox.ReadOnly = $true
$logBox.HideSelection = $false
$logBox.DetectUrls = $false

$statusPanel = New-Object System.Windows.Forms.Panel
$statusPanel.Dock = 'Bottom'
$statusPanel.Height = 70
$statusPanel.Padding = New-Object System.Windows.Forms.Padding(8, 4, 8, 8)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Dock = 'Top'
$progressBar.Height = 22
$progressBar.Minimum = 0
$progressBar.Maximum = [Math]::Max(1, $orderedSteps.Count)
$progressBar.Style = [System.Windows.Forms.ProgressBarStyle]::Continuous

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Dock = 'Top'
$statusLabel.Height = 22
$statusLabel.Text = 'Pronto.'
$statusLabel.TextAlign = 'MiddleLeft'

$stepLabel = New-Object System.Windows.Forms.Label
$stepLabel.Dock = 'Top'
$stepLabel.Height = 22
$stepLabel.Text = 'Step: -'
$stepLabel.TextAlign = 'MiddleLeft'

$statusPanel.Controls.Add($stepLabel)
$statusPanel.Controls.Add($statusLabel)
$statusPanel.Controls.Add($progressBar)

$form.Controls.Add($logBox)
$form.Controls.Add($statusPanel)
$form.Controls.Add($buttonPanel)
$form.Controls.Add($groupOptions)
$form.AcceptButton = $buttonStart

# --- Helper functions ---------------------------------------------------------
function Quote-Argument {
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -match '\s|"') {
        return '"' + ($Value.Replace('"', '""')) + '"'
    }
    return $Value
}

function Append-Log {
    param(
        [string]$Text,
        [System.Drawing.Color]$Color = $null
    )
    if ($null -eq $Color) { $Color = $logBox.ForeColor }
    if ($logBox.InvokeRequired) {
        $action = [System.Action[string,System.Drawing.Color]]{
            param($t,$c)
            Append-Log $t $c
        }
        $null = $logBox.BeginInvoke($action, $Text, $Color)
        return
    }
    $start = $logBox.TextLength
    $logBox.SelectionStart = $start
    $logBox.SelectionLength = 0
    $logBox.SelectionColor = $Color
    $logBox.AppendText($Text + [Environment]::NewLine)
    $logBox.SelectionColor = $logBox.ForeColor
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()

    if ($script:CurrentRunLogFile) {
        try {
            Add-Content -Path $script:CurrentRunLogFile -Value ($Text + [Environment]::NewLine)
        } catch {}
    }
}

function Set-Status {
    param([string]$Text)
    if ($statusLabel.InvokeRequired) {
        $action = [System.Action[string]]{
            param($t)
            Set-Status $t
        }
        $null = $statusLabel.BeginInvoke($action, $Text)
        return
    }
    $statusLabel.Text = $Text
}

function Update-Step {
    param([string]$Step)
    if ([string]::IsNullOrWhiteSpace($Step)) { return }
    if ($stepLabel.InvokeRequired -or $progressBar.InvokeRequired) {
        $action = [System.Action[string]]{
            param($s)
            Update-Step $s
        }
        $null = $stepLabel.BeginInvoke($action, $Step)
        return
    }
    $stepLabel.Text = "Step: $Step"
    $index = $orderedSteps.IndexOf($Step)
    if ($index -ge 0) {
        $value = [Math]::Min($index + 1, $progressBar.Maximum)
        $progressBar.Value = [Math]::Max($progressBar.Minimum, $value)
    } elseif ($progressBar.Value -lt $progressBar.Maximum) {
        $progressBar.Value += 1
    }
}

function Classify-LineColor {
    param([string]$Line, [string]$Stream)
    if ($Stream -eq 'stderr') { return [System.Drawing.Color]::Tomato }
    if ($Line -match '(?i)\[fail\]|\[errore\]|\[error\]') { return [System.Drawing.Color]::Tomato }
    if ($Line -match '(?i)\[warn\]') { return [System.Drawing.Color]::Khaki }
    if ($Line -match '(?i)\[ok\]') { return [System.Drawing.Color]::PaleGreen }
    if ($Line -match '(?i)\[info\]') { return [System.Drawing.Color]::LightSteelBlue }
    return $logBox.ForeColor
}

function Handle-Line {
    param(
        [string]$Line,
        [string]$Stream
    )
    if ($null -eq $Line) { return }
    $clean = $Line.Trim([char]0x0D, [char]0x0A)
    $color = Classify-LineColor -Line $clean -Stream $Stream
    Append-Log -Text $clean -Color $color
    if ($clean -match '^=+\s*(.+?)\s*=+$') {
        $stepName = $matches[1].Trim()
        Update-Step $stepName
    }
}

$script:ProvisionProcess = $null
$script:StdOutHandler = $null
$script:StdErrHandler = $null
$script:ExitHandler = $null
$script:CancellationRequested = $false
$script:CurrentRunLogFile = $null

function Reset-Progress {
    if ($progressBar.InvokeRequired) {
        $action = [System.Action]{ Reset-Progress }
        $null = $progressBar.BeginInvoke($action)
        return
    }
    $progressBar.Value = 0
    $stepLabel.Text = 'Step: -'
}

function Finish-Provisioning {
    if ($null -eq $script:ProvisionProcess) { return }
    if ($form.InvokeRequired) {
        $action = [System.Action]{ Finish-Provisioning }
        $null = $form.BeginInvoke($action)
        return
    }
    try { $script:ProvisionProcess.CancelOutputRead() } catch {}
    try { $script:ProvisionProcess.CancelErrorRead() } catch {}
    if ($script:StdOutHandler) { $script:ProvisionProcess.remove_OutputDataReceived($script:StdOutHandler) }
    if ($script:StdErrHandler) { $script:ProvisionProcess.remove_ErrorDataReceived($script:StdErrHandler) }
    if ($script:ExitHandler) { $script:ProvisionProcess.remove_Exited($script:ExitHandler) }

    $exitCode = $null
    try { $exitCode = $script:ProvisionProcess.ExitCode } catch { $exitCode = $null }
    if ($exitCode -eq 0) {
        Append-Log '[OK] Provisioning completato con successo.' ([System.Drawing.Color]::PaleGreen)
        Set-Status 'Provisioning completato.'
    } elseif ($script:CancellationRequested) {
        Append-Log '[WARN] Provisioning interrotto dall''utente.' ([System.Drawing.Color]::Khaki)
        Set-Status 'Provisioning annullato.'
    } else {
        Append-Log "[FAIL] Provisioning terminato con codice $exitCode" ([System.Drawing.Color]::Tomato)
        Set-Status "Errore (exit code $exitCode)."
    }
    $buttonStart.Enabled = $true
    $buttonCancel.Enabled = $false
    $script:ProvisionProcess.Dispose()
    $script:ProvisionProcess = $null
    $script:StdOutHandler = $null
    $script:StdErrHandler = $null
    $script:ExitHandler = $null
    $script:CancellationRequested = $false
    $script:CurrentRunLogFile = $null
}

function Start-Provisioning {
    if ($script:ProvisionProcess -and -not $script:ProvisionProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show('Provisioning gia'' in esecuzione.', 'Attenzione', 'OK', 'Warning') | Out-Null
        return
    }

    $installRoot = ($textInstallRoot.Text.Trim())
    if (-not $installRoot) {
        $installRoot = 'C:\Program Files\marocco-player'
    }

    $logsDirectory = if ($installRoot) { Join-Path $installRoot 'logs' } else { Join-Path $scriptRoot 'logs' }
    $logsReady = $false
    try {
        if ($logsDirectory -and -not (Test-Path $logsDirectory)) {
            New-Item -ItemType Directory -Path $logsDirectory -Force | Out-Null
        }
        $logsReady = $true
    } catch {
        $fallback = Join-Path $scriptRoot 'logs'
        try {
            if (-not (Test-Path $fallback)) {
                New-Item -ItemType Directory -Path $fallback -Force | Out-Null
            }
            $logsDirectory = $fallback
            $logsReady = $true
            Append-Log "[WARN] Accesso negato a $installRoot\logs, uso cartella GUI: $logsDirectory" ([System.Drawing.Color]::Khaki)
        } catch {
            $logsDirectory = [System.IO.Path]::GetTempPath()
            Append-Log "[WARN] Uso cartella temporanea per log: $logsDirectory" ([System.Drawing.Color]::Khaki)
            $logsReady = $true
        }
    }

    if (-not $logsReady) {
        $logsDirectory = Join-Path $scriptRoot 'logs'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $script:CurrentRunLogFile = Join-Path $logsDirectory ("provision_gui_$timestamp.log")
    try {
        if (-not (Test-Path $script:CurrentRunLogFile)) {
            New-Item -Path $script:CurrentRunLogFile -ItemType File -Force | Out-Null
        }
    } catch {}

    Reset-Progress
    $logBox.Clear()
    Append-Log ("=== Avvio provisioning: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) ([System.Drawing.Color]::LightSteelBlue)
    Set-Status 'In esecuzione...'
    $buttonStart.Enabled = $false
    $buttonCancel.Enabled = $true
    Append-Log "Log file: $script:CurrentRunLogFile" ([System.Drawing.Color]::LightSteelBlue)

    $argList = New-Object System.Collections.Generic.List[string]
    $argList.Add('-NoProfile')
    $argList.Add('-ExecutionPolicy')
    $argList.Add('Bypass')
    $argList.Add('-File')
    $argList.Add($provisionScript)
    $argList.Add('-NonInteractive')

    if ($installRoot) { $argList.Add('-InstallRoot'); $argList.Add($installRoot) }

    $staticIp = $textStaticIp.Text.Trim()
    if ($staticIp) { $argList.Add('-StaticIP'); $argList.Add($staticIp) }

    $gateway = $textGateway.Text.Trim()
    if ($gateway) { $argList.Add('-Gateway'); $argList.Add($gateway) }

    $prefix = [int]$numericPrefix.Value
    if ($prefix -gt 0) { $argList.Add('-PrefixLength'); $argList.Add($prefix.ToString()) }

    $dnsText = $textDns.Text.Trim()
    if ($dnsText) {
        $dnsList = $dnsText -split '[,;\s]+' | Where-Object { $_ }
        if ($dnsList.Count -gt 0) {
            $argList.Add('-DnsServers')
            foreach ($dns in $dnsList) { $argList.Add($dns) }
        }
    }

    if ($checkSkipVnc.Checked) { $argList.Add('-SkipTigerVNC') }
    if ($checkSkipShare.Checked) { $argList.Add('-SkipMediaShare') }
    if ($checkSkipHostname.Checked) { $argList.Add('-SkipHostnameSync') }

    $quotedArgs = ($argList | ForEach-Object { Quote-Argument $_ }) -join ' '
    Append-Log "Command: powershell.exe $quotedArgs" ([System.Drawing.Color]::Gray)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = $quotedArgs
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.EnableRaisingEvents = $true

    $stdoutHandler = [System.Diagnostics.DataReceivedEventHandler]{
        param($sender, $eventArgs)
        if ($eventArgs.Data) { Handle-Line $eventArgs.Data 'stdout' }
    }
    $stderrHandler = [System.Diagnostics.DataReceivedEventHandler]{
        param($sender, $eventArgs)
        if ($eventArgs.Data) { Handle-Line $eventArgs.Data 'stderr' }
    }
    $exitHandler = [System.EventHandler]{
        param($sender, $eventArgs)
        Finish-Provisioning
    }

    try {
        $null = $proc.Start()
        $proc.add_OutputDataReceived($stdoutHandler)
        $proc.add_ErrorDataReceived($stderrHandler)
        $proc.add_Exited($exitHandler)
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()
        $script:ProvisionProcess = $proc
        $script:StdOutHandler = $stdoutHandler
        $script:StdErrHandler = $stderrHandler
        $script:ExitHandler = $exitHandler
    } catch {
        Append-Log "[FAIL] Impossibile avviare provisioning: $($_.Exception.Message)" ([System.Drawing.Color]::Tomato)
        Set-Status 'Errore di avvio.'
        $buttonStart.Enabled = $true
        $buttonCancel.Enabled = $false
        if ($proc) { $proc.Dispose() }
    }
}

function Stop-Provisioning {
    if ($script:ProvisionProcess -and -not $script:ProvisionProcess.HasExited) {
        $script:CancellationRequested = $true
        Append-Log '[WARN] Richiesta di annullamento inviata. Attendere...' ([System.Drawing.Color]::Khaki)
        try {
            $script:ProvisionProcess.Kill()
        } catch {
            Append-Log "[WARN] Impossibile terminare il processo: $($_.Exception.Message)" ([System.Drawing.Color]::Khaki)
        }
    }
}

$buttonStart.Add_Click({ Start-Provisioning })
$buttonCancel.Add_Click({ Stop-Provisioning })

$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:ProvisionProcess -and -not $script:ProvisionProcess.HasExited) {
        $result = [System.Windows.Forms.MessageBox]::Show(
            'Provisioning in corso. Vuoi interrompere e chiudere?',
            'Conferma uscita',
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        if ($result -eq [System.Windows.Forms.DialogResult]::Yes) {
            Stop-Provisioning
            $eventArgs.Cancel = $true
        } else {
            $eventArgs.Cancel = $true
        }
    }
})

$form.Add_Load({ $textInstallRoot.Select() })

[System.Windows.Forms.Application]::Run($form)
