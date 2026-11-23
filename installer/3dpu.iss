; Inno Setup script for the 3dpu installer (Slicer and/or Manager).
; Built by installer\build.ps1, which passes AppVersion and Payload (a folder holding
; an embeddable Python runtime with 3dpu and its dependencies installed).
;
; Everything is started through the signed python.exe / pythonw.exe from python.org,
; never through generated launcher .exe files, because Windows Smart App Control /
; Application Control blocks unsigned launchers. (The installer itself is unsigned
; for now; see README.)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Payload
  #define Payload "build\runtime"
#endif
#define Py "{app}\runtime\python.exe"
#define Pyw "{app}\runtime\pythonw.exe"

[Setup]
AppId={{6F1D2C3B-8A4E-4B7C-9D1E-2F3A4B5C6D7E}
AppName=3dpu
AppVersion={#AppVersion}
AppVerName=3dpu {#AppVersion}
AppPublisher=FatedHobbies
AppPublisherURL=https://github.com/FatedHobbies/3d-printing-utils
DefaultDirName={autopf}\3dpu
DefaultGroupName=3dpu
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=3dpu-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=3dpu (Slicer and Manager)
UninstallDisplayIcon={#Pyw}
CloseApplications=yes
MinVersion=10.0

[Types]
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
; Chosen on the "What is this computer?" page (the components page itself is hidden).
; Silent installs: /COMPONENTS="slicer" or /COMPONENTS="manager" (or both, for testing).
Name: "slicer"; Description: "3dpu Slicer server (this is the Slicer computer)"
Name: "manager"; Description: "3dpu Manager app (this is the Manager computer)"

[Tasks]
Name: "autostart"; Description: "Start the Slicer server automatically when I sign in to Windows"; Components: slicer
Name: "firewall"; Description: "Let the Manager computer reach the Slicer server over my home (private) network"; Components: slicer
Name: "desktopicon"; Description: "Put a 3dpu Manager shortcut on the desktop"; Components: manager

[Files]
Source: "{#Payload}\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
; The service runs as the signed-in user, so it must be able to write its config and work files.
Name: "{commonappdata}\3dpu"; Permissions: users-modify; Components: slicer

[Icons]
Name: "{group}\3dpu Manager"; Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; WorkingDir: "{userdocs}"; Comment: "Send Bambu Studio projects to the Slicer PC"; Components: manager
Name: "{autodesktop}\3dpu Manager"; Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; WorkingDir: "{userdocs}"; Tasks: desktopicon
Name: "{group}\3dpu Slicer"; Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer tray --show-info"; Comment: "Slicer server tray icon, address and access token"; Components: slicer
Name: "{commonstartup}\3dpu Slicer"; Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer tray"; Tasks: autostart
Name: "{group}\Uninstall 3dpu"; Filename: "{uninstallexe}"

[Run]
; Remove the scheduled task created by the older PowerShell setup script, if present.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""3dpu Slicer"" /F"; Flags: runhidden; Components: slicer
; Create the config file and access token.
Filename: "{#Py}"; Parameters: "-m threedpu.slicer config"; Flags: runhidden; Components: slicer
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""3dpu Slicer"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""3dpu Slicer"" dir=in action=allow protocol=TCP localport=8765 profile=private"; Flags: runhidden; Tasks: firewall
; Start the tray (which starts the server) as the signed-in user, not as administrator,
; and show the address and token to enter on the Manager computer.
Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer tray --show-info"; Description: "Start the Slicer server and show the address and token for the Manager computer"; Flags: postinstall nowait runasoriginaluser skipifsilent; Components: slicer
Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; Description: "Open 3dpu Manager"; Flags: postinstall nowait runasoriginaluser skipifsilent; Components: manager

[UninstallRun]
Filename: "{#Py}"; Parameters: "-m threedpu.slicer stop --tray"; Flags: runhidden; RunOnceId: "StopSlicer"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""3dpu Slicer"""; Flags: runhidden; RunOnceId: "DelFirewall"

[UninstallDelete]
; Python writes __pycache__ folders at runtime. Settings and job history are kept.
Type: filesandordirs; Name: "{app}\runtime"

[Code]
var
  RolePage: TInputOptionWizardPage;

procedure InitializeWizard;
var
  Previous: String;
begin
  RolePage := CreateInputOptionPage(wpWelcome,
    'What is this computer?',
    'Install the part of 3dpu that this computer needs.',
    'Run this installer on both computers, and pick a different answer on each.',
    True, False);
  RolePage.Add('This is the Slicer computer' + #13#10 +
    'The powerful computer that does the slicing. Installs the Slicer server, with a tray icon to start and stop it.');
  RolePage.Add('This is the Manager computer' + #13#10 +
    'The computer you sit at to prepare files and print. Installs the 3dpu Manager app.');
  Previous := GetPreviousData('Role', 'slicer');
  if Previous = 'manager' then
    RolePage.SelectedValueIndex := 1
  else
    RolePage.SelectedValueIndex := 0;
end;

function RoleComponent: String;
begin
  if RolePage.SelectedValueIndex = 1 then
    Result := 'manager'
  else
    Result := 'slicer';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { Silent installs choose with /COMPONENTS=..., so only apply the page when it was shown. }
  if (CurPageID = RolePage.ID) and not WizardSilent then
    WizardSelectComponents('!slicer,!manager,' + RoleComponent);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PageID = wpSelectComponents;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Role', RoleComponent);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  OldPy: String;
begin
  Result := '';
  { Upgrading: stop a running Slicer service so its files can be replaced. }
  OldPy := ExpandConstant('{app}\runtime\python.exe');
  if FileExists(OldPy) then
  begin
    Exec(OldPy, '-m threedpu.slicer stop --tray', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;
