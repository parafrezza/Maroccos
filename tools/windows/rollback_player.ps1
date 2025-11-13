<#
.SYNOPSIS
  Ripristina alcune modifiche applicate da provision_player.ps1 su Windows 11 player.
.DESCRIPTION
  Script best-effort e idempotente per:
    - Riabilitare Windows Update e servizi principali
    - Riportare Ethernet in DHCP (IP e DNS)
    - Ripristinare impostazioni visive predefinite
    - Ripristinare piano energetico bilanciato
    - Facoltativo: rimuovere regole firewall aperte (8080 TCP, 7777 UDP, 9999 UDP)

  Eseguire in PowerShell come Amministratore:
    Set-ExecutionPolicy Bypass -Scope Process -Force
    .\rollback_player.ps1 -Confirm:$true

.PARAMETER RemoveFirewallRules
  Se true, rimuove le regole firewall create dallo script di provisioning.
#>
param(
  [switch]$RemoveFirewallRules
)

# Richiede elevazione
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Error "Eseguire questo script come Amministratore."
  exit 1
}

function Enable-WindowsUpdate {
  Write-Host "[Rollback] Abilito Windows Update..."
  try {
    New-Item -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
    Remove-ItemProperty -Path 'HKLM:SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name 'NoAutoUpdate' -ErrorAction SilentlyContinue
    sc.exe config wuauserv start= demand | Out-Null
    sc.exe start wuauserv | Out-Null
    sc.exe config usosvc start= demand | Out-Null
    sc.exe start usosvc | Out-Null
  } catch {
    Write-Warning "Impossibile riattivare Windows Update: $($_.Exception.Message)"
  }
}

function Enable-ServicesCore {
  Write-Host "[Rollback] Riabilito servizi comuni..."
  $services = @(
    'wuauserv','UsoSvc','DoSvc','BITS','Dnscache','LanmanWorkstation','WSearch'
  )
  foreach ($svc in $services) {
    try {
      sc.exe config $svc start= demand | Out-Null
      sc.exe start $svc | Out-Null
    } catch {
      Write-Verbose "Servizio $svc: $($_.Exception.Message)"
    }
  }
}

function Restore-EthernetDHCP {
  Write-Host "[Rollback] Ethernet -> DHCP per IP e DNS..."
  try {
    $eth = Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like 'Ethernet*' -and $_.Dhcp -ne 'Enabled' } | Select-Object -First 1
    if ($null -ne $eth) {
      Set-NetIPInterface -InterfaceIndex $eth.InterfaceIndex -Dhcp Enabled -ErrorAction SilentlyContinue
      Set-DnsClientServerAddress -InterfaceIndex $eth.InterfaceIndex -ResetServerAddresses -ErrorAction SilentlyContinue
    }
  } catch {
    Write-Warning "Ripristino DHCP fallito: $($_.Exception.Message)"
  }
}

function Restore-VisualsDefault {
  Write-Host "[Rollback] Ripristino impostazioni visive..."
  try {
    # Re-enable animations/trasparenze (best-effort)
    New-Item -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -Force | Out-Null
    Set-ItemProperty -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -Name 'VisualFXSetting' -Value 0 -Type DWord
    # Trasparenze on
    New-Item -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Force | Out-Null
    Set-ItemProperty -Path 'HKCU:Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'EnableTransparency' -Value 1 -Type DWord
  } catch {
    Write-Verbose "Visual rollback parziale: $($_.Exception.Message)"
  }
}

function Restore-BalancedPowerPlan {
  Write-Host "[Rollback] Piano energetico Bilanciato..."
  try {
    powercfg -setactive SCHEME_BALANCED | Out-Null
    # Re-enable sleep
    powercfg -change -standby-timeout-ac 30 | Out-Null
    powercfg -hibernate on | Out-Null
  } catch {
    Write-Verbose "Power plan rollback parziale: $($_.Exception.Message)"
  }
}

function Remove-FirewallRules {
  param([switch]$DoRemove)
  if (-not $DoRemove) { return }
  Write-Host "[Rollback] Rimuovo regole firewall player..."
  $names = @('Player_HTTP_8080','Player_UDP_7777','GUI_UDP_9999')
  foreach ($n in $names) {
    try { Remove-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue } catch {}
  }
}

# Esecuzione
Enable-WindowsUpdate
Enable-ServicesCore
Restore-EthernetDHCP
Restore-VisualsDefault
Restore-BalancedPowerPlan
Remove-FirewallRules -DoRemove:$RemoveFirewallRules

Write-Host "[Rollback] Completato."