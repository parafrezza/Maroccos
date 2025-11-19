; Inno Setup Script parametrico per Maroccos player (OFF-player + headless)
; Requires Inno Setup 6 (ISCC.exe)

; Parametri passati da riga di comando (con default) usando #ifndef
#ifndef AppName
	#define AppName "marocco-player"
#endif
#ifndef AppVersion
	#define AppVersion "0.1.0"
#endif
#ifndef AppPublisher
	#define AppPublisher "Maroccos"
#endif
#ifdef SilentInstall
  #define SilentBuild 1
#else
  #define SilentBuild 0
#endif

#ifndef KLitePath
  #define KLitePath ""
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "marocco-installer_v" + AppVersion
#endif

#ifdef KLitePath
  #define KLiteFileName ExtractFileName(KLitePath)
#else
  #define KLiteFileName "K-Lite_Codec_Pack_Basic.exe"
#endif

#if SilentBuild = 1
  #define DisableWelcomePageValue yes
  #define DisableDirPageValue yes
  #define DisableReadyPageValue yes
  #define DisableFinishedPageValue yes
#else
  #define DisableWelcomePageValue no
  #define DisableDirPageValue no
  #define DisableReadyPageValue no
  #define DisableFinishedPageValue no
#endif

#ifndef HeadlessDirName
  #define HeadlessDirName "headless-player"
#endif
#ifndef HeadlessExeName
  #define HeadlessExeName "headless-player.exe"
#endif
#ifndef ProvisionTaskName
  #define ProvisionTaskName "MaroccosProvision"
#endif
#ifndef HeadlessTaskName
  #define HeadlessTaskName "MaroccosHeadless"
#endif
#ifndef HeadlessStartupTaskName
  #define HeadlessStartupTaskName "MaroccosHeadlessBoot"
#endif
#ifndef ExtraUserName
  #define ExtraUserName "extra"
#endif
#ifndef ExtraPassword
  #define ExtraPassword "extra"
#endif

#define AppExe "OFF-player.exe"

[Setup]
; Nota: per includere letteralmente le graffe del GUID in Inno, occorre raddoppiarle: {{GUID}}
AppId={{E4C5E5A0-9D53-4F7F-9D84-3C82A1C6F0C2}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={pf64}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#SourcePath}\\dist
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
DisableWelcomePage={#DisableWelcomePageValue}
DisableDirPage={#DisableDirPageValue}
DisableReadyPage={#DisableReadyPageValue}
DisableFinishedPage={#DisableFinishedPageValue}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ChangesEnvironment=yes
#if SilentBuild = 1
AllowCancelDuringInstall=no
SetupLogging=yes
CloseApplications=force
RestartApplications=no
#endif
; Icona dell'installer (se il file esiste)
#if FileExists(IcoSrcPath)
SetupIconFile={#IcoSrcPath}
#endif

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
#if FileExists(KLitePath)
Name: "install_klite"; Description: "Installa anche i codec K-Lite (necessari per OFF-player)"
#endif
#if SilentBuild = 1
Name: "install_tigervnc"; Description: "Installa TigerVNC (opzionale per accesso remoto)"
#else
Name: "install_tigervnc"; Description: "Installa TigerVNC (opzionale per accesso remoto)"; Flags: unchecked
#endif
Name: "autostart_headless"; Description: "Avvia headless all'avvio (crea Attività Pianificata)"
Name: "set_off_autostart"; Description: "Imposta OFF_AUTOSTART=1 (consigliato su Windows)"
#if SilentBuild = 1
Name: "provision\run_now"; Description: "Esegui ottimizzazioni Player subito (consigliato dopo installazione pulita)"; GroupDescription: "Ottimizzazioni post-installazione"; Flags: exclusive
#else
Name: "provision\run_now"; Description: "Esegui ottimizzazioni Player subito (consigliato dopo installazione pulita)"; GroupDescription: "Ottimizzazioni post-installazione"; Flags: exclusive unchecked
Name: "provision\schedule"; Description: "Pianifica ottimizzazioni al prossimo avvio"; GroupDescription: "Ottimizzazioni post-installazione"; Flags: exclusive unchecked
Name: "provision\skip"; Description: "Non applicare ottimizzazioni ora"; GroupDescription: "Ottimizzazioni post-installazione"; Flags: exclusive
#endif
#if SilentBuild = 1
Name: "auto_logon_extra"; Description: "Configura auto logon con l'utente '{#ExtraUserName}' (password '{#ExtraPassword}')"
#else
Name: "auto_logon_extra"; Description: "Configura auto logon con l'utente '{#ExtraUserName}' (password '{#ExtraPassword}')"; Flags: unchecked
#endif

[Files]
; App binaries (installa OFF-player sotto {app}\OFF-player\bin)
Source: "{#SourcePath}\\..\\OFF-player\\bin\\{#AppExe}"; DestDir: "{app}\\OFF-player\\bin"; Flags: ignoreversion
Source: "{#SourcePath}\\..\\OFF-player\\bin\\*.dll"; DestDir: "{app}\\OFF-player\\bin"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\\..\\OFF-player\\bin\\data\\*"; DestDir: "{app}\\OFF-player\\bin\\data"; Flags: ignoreversion recursesubdirs createallsubdirs

; Headless-player eseguibile e risorse (parametrico: release o debug)
#if DirExists(AddBackslash(SourcePath) + "..\\headless-player\\dist\\" + HeadlessDirName)
Source: "{#SourcePath}\\..\\headless-player\\dist\\{#HeadlessDirName}\\*"; DestDir: "{app}\\{#HeadlessDirName}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

; Utilities
Source: "{#SourcePath}\\..\\tools\\windows\\provision_player.ps1"; DestDir: "{app}\\tools\\windows"; Flags: ignoreversion
Source: "{#SourcePath}\\..\\tools\\windows\\apply_user_settings.ps1"; DestDir: "{app}\\tools\\windows"; Flags: ignoreversion
Source: "{#SourcePath}\\..\\tools\\windows\\create_provision_task.ps1"; DestDir: "{app}\\tools\\windows"; Flags: ignoreversion
Source: "{#SourcePath}\\..\\tools\\windows\\fix_autostart.ps1"; DestDir: "{app}\\tools\\windows"; Flags: ignoreversion

; Opzionale: K-Lite installer (mettere il file in installer\assets)
#if FileExists(KLitePath)
Source: "{#KLitePath}"; DestDir: "{tmp}"; DestName: "{#KLiteFileName}"; Flags: ignoreversion; Tasks: install_klite
#endif

; Copia l'icona nello spool dell'app per usarla nei collegamenti
#if FileExists(IcoSrcPath)
Source: "{#IcoSrcPath}"; DestDir: "{app}\assets"; DestName: "morocco-player.ico"; Flags: ignoreversion
#endif
Source: "{#SourcePath}\assets\tigervnc64-winvnc-1.15.0.exe"; DestDir: "{app}\assets"; Flags: ignoreversion; Tasks: install_tigervnc
Source: "{#SourcePath}\assets\SetResolution\*"; DestDir: "{app}\tools\SetResolution"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
; Esegui K-Lite silent se selezionato
#if FileExists(KLitePath)
Filename: "{tmp}\\{#KLiteFileName}"; Parameters: "/verysilent /norestart"; Flags: waituntilterminated; Tasks: install_klite
#endif

; Installa TigerVNC in modo silenzioso quando richiesto
Filename: "{app}\\assets\\tigervnc64-winvnc-1.15.0.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; Flags: runhidden waituntilterminated; StatusMsg: "Installazione TigerVNC..."; Check: FileExists(ExpandConstant('{app}\\assets\\tigervnc64-winvnc-1.15.0.exe')); Tasks: install_tigervnc
; Configura TigerVNC SENZA password (accesso non autenticato), e riavvia servizio se presente
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ErrorActionPreference='SilentlyContinue'; $kp='HKLM:\\SOFTWARE\\TigerVNC\\WinVNC4'; New-Item -Path $kp -Force | Out-Null; Remove-ItemProperty -Path $kp -Name 'Password' -ErrorAction SilentlyContinue; Remove-ItemProperty -Path $kp -Name 'ControlPassword' -ErrorAction SilentlyContinue; New-ItemProperty -Path $kp -Name 'AuthRequired' -PropertyType DWord -Value 0 -Force | Out-Null; New-ItemProperty -Path $kp -Name 'SecurityTypes' -PropertyType String -Value 'None' -Force | Out-Null; New-ItemProperty -Path $kp -Name 'AlwaysShared' -PropertyType DWord -Value 1 -Force | Out-Null; New-ItemProperty -Path $kp -Name 'QuerySetting' -PropertyType DWord -Value 2 -Force | Out-Null; New-ItemProperty -Path $kp -Name 'UseControlAuth' -PropertyType DWord -Value 0 -Force | Out-Null; Try {{ Restart-Service -Name 'tvnserver' -Force -ErrorAction SilentlyContinue }} Catch {{}}; Try {{ Restart-Service -Name 'WinVNC4' -Force -ErrorAction SilentlyContinue }} Catch {{}}; foreach($svc in 'tvnserver','WinVNC4') {{ try {{ Set-Service -Name $svc -StartupType Automatic }} catch {{}}; try {{ Start-Service -Name $svc }} catch {{}} }}"""; Flags: runhidden waituntilterminated; Tasks: install_tigervnc

; PRIMA: Crea SEMPRE l'utente "extra" (necessario per provisioning e autostart)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ErrorActionPreference = 'Stop'; if (-not (Get-LocalUser -Name '{#ExtraUserName}' -ErrorAction SilentlyContinue)) {{ $sec = ConvertTo-SecureString '{#ExtraPassword}' -AsPlainText -Force; New-LocalUser -Name '{#ExtraUserName}' -Password $sec -FullName 'Extra Admin' -PasswordNeverExpires:$true -UserMayNotChangePassword:$true | Out-Null }}; Add-LocalGroupMember -Group 'Administrators' -Member '{#ExtraUserName}' -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; StatusMsg: "Creazione utente amministratore..."

; POI: Esegui provisioning (ora l'utente extra esiste già)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\provision_player.ps1"" -NonInteractive -InstallRoot ""{app}"" -Verbose"; Flags: waituntilterminated; StatusMsg: "Esecuzione ottimizzazioni Player (console visibile per avanzamento)..."; Tasks: provision\run_now
#if SilentBuild = 0
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\create_provision_task.ps1"" -InstallRoot ""{app}"" -TaskName ""{#ProvisionTaskName}"""; Flags: runhidden waituntilterminated; StatusMsg: "Programmazione ottimizzazioni al prossimo avvio..."; Tasks: provision\schedule
#endif

; Configurazione auto-logon per utente extra
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$path = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'; Set-ItemProperty -Path $path -Name 'DefaultUserName' -Value '{#ExtraUserName}'; Set-ItemProperty -Path $path -Name 'DefaultPassword' -Value '{#ExtraPassword}'; Set-ItemProperty -Path $path -Name 'DefaultDomainName' -Value $env:COMPUTERNAME; Set-ItemProperty -Path $path -Name 'AutoAdminLogon' -Value '1'; Set-ItemProperty -Path $path -Name 'ForceAutoLogon' -Value '1'"""; Flags: runhidden waituntilterminated; Tasks: auto_logon_extra

; Crea attività pianificate per avvio headless automatico (se presente l'eseguibile)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\fix_autostart.ps1"" -InstallRoot ""{app}"" -TaskName ""{#HeadlessTaskName}"" -Trigger Logon"; Flags: runhidden waituntilterminated; Tasks: autostart_headless; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\fix_autostart.ps1"" -InstallRoot ""{app}"" -TaskName ""{#HeadlessTaskName}"" -Trigger Logon -RunAsUser ""{#ExtraUserName}"" -RunAsPassword ""{#ExtraPassword}"""; Flags: runhidden waituntilterminated; Tasks: auto_logon_extra; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\fix_autostart.ps1"" -InstallRoot ""{app}"" -TaskName ""{#HeadlessStartupTaskName}"" -Trigger Startup -RunAsSystem"; Flags: runhidden waituntilterminated; Tasks: autostart_headless; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\windows\fix_autostart.ps1"" -InstallRoot ""{app}"" -TaskName ""{#HeadlessStartupTaskName}"" -Trigger Startup -RunAsSystem"; Flags: runhidden waituntilterminated; Tasks: auto_logon_extra; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
#if SilentBuild = 0
; Checkbox finale "Avvia ora?" che propone l'avvio immediato dopo l'installazione
Filename: "{app}\{#HeadlessDirName}\{#HeadlessExeName}"; Description: "Avvia ora?"; Flags: postinstall nowait skipifsilent; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))

; Opzione di riavvio del sistema nella pagina finale (opzionale, non selezionata di default)
Filename: "{cmd}"; Parameters: "/C shutdown /r /t 0"; Description: "Riavvia il sistema (opzionale)"; Flags: postinstall skipifsilent unchecked nowait
#else
Filename: "{cmd}"; Parameters: "/C shutdown /r /t 30 /c ""Installazione Maroccos completata: riavvio automatico in 30 secondi."""; Flags: runhidden nowait; StatusMsg: "Programmazione riavvio automatico..."
#endif

[Icons]
; Unica icona desktop e Start con nome versionato, punta al launcher headless
#if FileExists(IcoSrcPath)
Name: "{commondesktop}\{#DesktopLinkName}"; Filename: "{app}\{#HeadlessDirName}\{#HeadlessExeName}"; IconFilename: "{app}\assets\morocco-player.ico"; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
Name: "{group}\{#DesktopLinkName}"; Filename: "{app}\{#HeadlessDirName}\{#HeadlessExeName}"; IconFilename: "{app}\assets\morocco-player.ico"; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
#else
Name: "{commondesktop}\{#DesktopLinkName}"; Filename: "{app}\{#HeadlessDirName}\{#HeadlessExeName}"; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
Name: "{group}\{#DesktopLinkName}"; Filename: "{app}\{#HeadlessDirName}\{#HeadlessExeName}"; Check: FileExists(ExpandConstant('{app}\{#HeadlessDirName}\{#HeadlessExeName}'))
#endif

[Registry]
; Imposta OFF_AUTOSTART=1 a livello utente (visibile ai processi futuri). Richiede ChangesEnvironment=yes
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "OFF_AUTOSTART"; ValueData: "1"; Flags: uninsdeletevalue; Tasks: set_off_autostart

; Esegui script di configurazione utente al primo login (per tutti gli utenti)
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MaroccosUserSettings"; ValueData: "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\tools\windows\apply_user_settings.ps1"""; Flags: uninsdeletevalue

[UninstallRun]
; Rimuovi l'attività pianificata in uninstall
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#HeadlessTaskName}"" /F"; Flags: runhidden
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#HeadlessStartupTaskName}"" /F"; Flags: runhidden
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#ProvisionTaskName}"" /F"; Flags: runhidden
Filename: "reg.exe"; Parameters: "ADD ""HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon"" /v AutoAdminLogon /t REG_SZ /d 0 /f"; Flags: runhidden
Filename: "reg.exe"; Parameters: "DELETE ""HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon"" /v DefaultPassword /f"; Flags: runhidden
Filename: "reg.exe"; Parameters: "DELETE ""HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon"" /v ForceAutoLogon /f"; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
type
  TSystemTime = record
    wYear: Word;
    wMonth: Word;
    wDayOfWeek: Word;
    wDay: Word;
    wHour: Word;
    wMinute: Word;
    wSecond: Word;
    wMilliseconds: Word;
  end;

var
  UninstPage: TWizardPage;
  UninstMemo: TNewMemo;
  PrevUninstallDone: Boolean;
  NeedUninstallAtStart: Boolean;
  UILogStep: Integer;
  
const
  // Mostra un avviso se la disinstallazione precedente impiega più di N secondi
  UninstallWarnSeconds = 120;

procedure GetLocalTime(var lpSystemTime: TSystemTime);
  external 'GetLocalTime@kernel32.dll stdcall';

function GetTickCount64(): Int64;
  external 'GetTickCount64@kernel32.dll stdcall';

function NowISO8601(): string;
var
  ST: TSystemTime;
begin
  // Recupera l'ora locale e la formatta in ISO8601 (secondi)
  GetLocalTime(ST);
  Result := Format('%4.4d-%2.2d-%2.2dT%2.2d:%2.2d:%2.2d', [ST.wYear, ST.wMonth, ST.wDay, ST.wHour, ST.wMinute, ST.wSecond]);
end;

procedure UILog(const S: string);
var
  Line: string;
begin
  UILogStep := UILogStep + 1;
  Line := Format('[%s] (%d) %s', [NowISO8601(), UILogStep, S]);
  Log(Line);
  if Assigned(UninstMemo) then
  begin
    UninstMemo.Lines.Add(Line);
  end;
end;
// Utility: split an uninstall command line into executable path and parameters
function SplitCmdLine(const S: string; var ExePath, Params: string): Boolean;
var
  P, L: Integer;
  T: string;
begin
  T := Trim(S);
  L := Length(T);
  if L = 0 then
  begin
    Result := False;
    exit;
  end;
  if (T[1] = '"') then
  begin
    // Find closing quote
    P := 2;
    while (P <= L) and (T[P] <> '"') do P := P + 1;
    if P <= L then
    begin
      ExePath := Copy(T, 2, P - 2);
      Params := Trim(Copy(T, P + 1, L - P));
    end
    else
    begin
      ExePath := RemoveQuotes(T);
      Params := '';
    end;
  end
  else
  begin
    P := Pos(' ', T);
    if P > 0 then
    begin
      ExePath := Copy(T, 1, P - 1);
      Params := Trim(Copy(T, P + 1, L - P));
    end
    else
    begin
      ExePath := T;
      Params := '';
    end;
  end;
  Result := (ExePath <> '');
end;

function RunUninstallString(const Cmd: string): Boolean;
var
  Exe, Args: string;
  Code: Integer;
begin
  Result := False;
  if not SplitCmdLine(Cmd, Exe, Args) then
  begin
    UILog('SplitCmdLine failed for: ' + Cmd);
    exit;
  end;
  Exe := RemoveQuotes(Exe);
  if (Pos('/VERYSILENT', UpperCase(Args)) = 0) then
    Args := Trim(Args + ' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART');
  UILog(Format('Launching uninstall: %s %s', [Exe, Args]));
  UILog('Please wait…');
  Result := ShellExec('', Exe, Args, '', SW_HIDE, ewWaitUntilTerminated, Code);
  UILog(Format('Uninstall finished: rc=%d', [Code]));
end;

procedure PerformPreviousUninstall();
var
  RootKey: Integer;
  SubKey: String;
  I: Integer;
  KeyName, DispName, UninsStr, QuietStr: String;
  RKIndex, SKIndex: Integer;
  Subkeys: TArrayOfString;
begin
  // Attempt to uninstall any existing products with DisplayName matching our AppName
  for RKIndex := 0 to 1 do
  begin
    if RKIndex = 0 then RootKey := HKLM else RootKey := HKCU;
    for SKIndex := 0 to 1 do
    begin
      if SKIndex = 0 then
        SubKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall'
      else
        SubKey := 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall';

      if RegGetSubkeyNames(RootKey, SubKey, Subkeys) then
      begin
        for I := 0 to GetArrayLength(Subkeys) - 1 do
        begin
          KeyName := Subkeys[I];
          if RegQueryStringValue(RootKey, SubKey + '\\' + KeyName, 'DisplayName', DispName) then
          begin
            if (Pos('marocco-player', LowerCase(DispName)) > 0) or (Pos(LowerCase('{#AppName}'), LowerCase(DispName)) > 0) then
            begin
              UILog('Rilevata versione precedente: ' + DispName);
              QuietStr := '';
              UninsStr := '';
              RegQueryStringValue(RootKey, SubKey + '\\' + KeyName, 'QuietUninstallString', QuietStr);
              RegQueryStringValue(RootKey, SubKey + '\\' + KeyName, 'UninstallString', UninsStr);
              if (QuietStr <> '') then
              begin
                if not RunUninstallString(QuietStr) then
                  UILog('QuietUninstallString failed: ' + QuietStr);
              end
              else if (UninsStr <> '') then
              begin
                if not RunUninstallString(UninsStr) then
                  UILog('UninstallString failed: ' + UninsStr);
              end
              else
              begin
                UILog('No uninstall string found for: ' + DispName);
              end;
            end;
          end;
        end;
      end;
    end;
  end;
  PrevUninstallDone := True;
end;

// Uninstall previous installs of marocco-player before proceeding (even if AppId differs)
function InitializeSetup(): Boolean;
begin
  PrevUninstallDone := False;
  NeedUninstallAtStart := False;
  // In modalità SILENT esegui subito; in modalità interattiva mostraremo una pagina dedicata
  if WizardSilent() then
  begin
    PerformPreviousUninstall();
  end
  else
  begin
    NeedUninstallAtStart := True;
  end;
  Result := True;
end;

procedure InitializeWizard();
begin
  // Crea pagina di avanzamento per la disinstallazione precedente (solo UI interattiva)
  UninstPage := CreateCustomPage(wpWelcome, 'Rimozione versioni precedenti', 'Attendere: disinstallazione in corso…');
  UninstMemo := TNewMemo.Create(UninstPage.Surface);
  UninstMemo.Parent := UninstPage.Surface;
  UninstMemo.Left := ScaleX(0);
  UninstMemo.Top := ScaleY(0);
  UninstMemo.Width := UninstPage.SurfaceWidth;
  UninstMemo.Height := UninstPage.SurfaceHeight;
  UninstMemo.ReadOnly := True;
  UninstMemo.ScrollBars := ssVertical;
  UILogStep := 0;
end;

function _ServiceIsRunning(const SvcName: string): Boolean; forward;
function IsVNCServiceRunning(): Boolean; forward;

procedure CurPageChanged(CurPageID: Integer);
var
  StartTick: Int64;
  ElapsedSec: Integer;
  Msg: string;
begin
  if (NeedUninstallAtStart) and (not PrevUninstallDone) and (CurPageID = UninstPage.ID) then
  begin
    NeedUninstallAtStart := False;
    WizardForm.NextButton.Enabled := False;
    UILog('Preparazione disinstallazione versioni precedenti…');
    StartTick := GetTickCount64();
    PerformPreviousUninstall();
    if StartTick > 0 then
      ElapsedSec := Integer((GetTickCount64() - StartTick) div 1000)
    else
      ElapsedSec := 0;
    if ElapsedSec > UninstallWarnSeconds then
    begin
      UILog(Format('ATTENZIONE: la disinstallazione ha richiesto %d secondi (>%d). Potrebbe essere normale su sistemi lenti.', [ElapsedSec, UninstallWarnSeconds]));
    end;
    UILog('Disinstallazione completata.');
    WizardForm.NextButton.Enabled := True;
  end;

#if SilentBuild = 0
  // Alla pagina finale, verifica lo stato del servizio TigerVNC e aggiorna la label
  if CurPageID = wpFinished then
  begin
    if IsTaskSelected('install_tigervnc') then
    begin
      try
        Msg := 'TigerVNC: verifica stato...';
        if IsVNCServiceRunning() then
          Msg := 'TigerVNC: attivo'
        else
          Msg := 'TigerVNC: NON attivo (verificare servizio)';
        WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + Msg;
      except
        // best-effort, non bloccare la pagina finale
      end;
    end;
  end;
#endif
end;

// Best-effort: usa WMI per interrogare lo stato del servizio TigerVNC (tvnserver/WinVNC4)
function _ServiceIsRunning(const SvcName: string): Boolean;
var
  ResultExec: Boolean;
  ExitCode: Integer;
  Params: string;
begin
  Result := False;
  try
    Params := Format('/C for /f "tokens=3" %%s in (' +
      '''sc.exe query "%s" ^| findstr /R /C:"STATE"'') do (' +
      ' if /I "%%s"=="RUNNING" exit /B 0 else exit /B 1 )', [SvcName]);
    ResultExec := Exec('cmd.exe', Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
    if ResultExec and (ExitCode = 0) then
      Result := True
    else if ResultExec and (ExitCode = 1) then
      Result := False
    else
      Result := False;
  except
    Result := False;
  end;
end;

function IsVNCServiceRunning(): Boolean;
begin
  Result := _ServiceIsRunning('tvnserver');
  if not Result then
    Result := _ServiceIsRunning('WinVNC4');
end;
