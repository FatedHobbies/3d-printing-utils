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
Name: "manager"; Description: "Manager PC: where you prepare files and print"
Name: "slicer"; Description: "Slicer PC: the computer that does the slicing"
Name: "both"; Description: "Both on this PC (for trying it out)"

[Components]
Name: "runtime"; Description: "Python runtime and 3dpu (required)"; Types: manager slicer both; Flags: fixed
Name: "manager"; Description: "3dpu Manager desktop app"; Types: manager both
Name: "slicer"; Description: "3dpu Slicer service"; Types: slicer both

[Tasks]
Name: "desktopicon"; Description: "Put a 3dpu Manager shortcut on the desktop"; Components: manager
Name: "autostart"; Description: "Start the Slicer service automatically when I sign in to Windows"; Components: slicer
Name: "firewall"; Description: "Let Manager PCs on my home (private) network reach the Slicer service"; Components: slicer

[Files]
Source: "{#Payload}\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: runtime

[Dirs]
; The service runs as the signed-in user, so it must be able to write its config and work files.
Name: "{commonappdata}\3dpu"; Permissions: users-modify; Components: slicer

[Icons]
Name: "{group}\3dpu Manager"; Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; WorkingDir: "{userdocs}"; Comment: "Send Bambu Studio projects to the Slicer PC"; Components: manager
Name: "{autodesktop}\3dpu Manager"; Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; WorkingDir: "{userdocs}"; Tasks: desktopicon
Name: "{group}\3dpu Slicer"; Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer info"; Comment: "Slicer status, address and access token"; Components: slicer
Name: "{commonstartup}\3dpu Slicer service"; Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer serve"; Tasks: autostart
Name: "{group}\Uninstall 3dpu"; Filename: "{uninstallexe}"

[Run]
; Remove the scheduled task created by the older PowerShell setup script, if present.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""3dpu Slicer"" /F"; Flags: runhidden; Components: slicer
; Create the config file and access token.
Filename: "{#Py}"; Parameters: "-m threedpu.slicer config"; Flags: runhidden; Components: slicer
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""3dpu Slicer"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""3dpu Slicer"" dir=in action=allow protocol=TCP localport=8765 profile=private"; Flags: runhidden; Tasks: firewall
; Start the service now (as the signed-in user, not as administrator).
Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer serve"; Flags: nowait runasoriginaluser; Tasks: autostart
Filename: "{#Pyw}"; Parameters: "-m threedpu.slicer info"; Description: "Show the Slicer address and access token"; Flags: postinstall nowait runasoriginaluser skipifsilent; Components: slicer
Filename: "{#Pyw}"; Parameters: "-m threedpu.manager"; Description: "Open 3dpu Manager"; Flags: postinstall nowait runasoriginaluser skipifsilent; Components: manager

[UninstallRun]
Filename: "{#Py}"; Parameters: "-m threedpu.slicer stop"; Flags: runhidden; RunOnceId: "StopSlicer"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""3dpu Slicer"""; Flags: runhidden; RunOnceId: "DelFirewall"

[UninstallDelete]
; Python writes __pycache__ folders at runtime. Settings and job history are kept.
Type: filesandordirs; Name: "{app}\runtime"

[Code]
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
    Exec(OldPy, '-m threedpu.slicer stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;
