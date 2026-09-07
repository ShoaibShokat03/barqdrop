; Inno Setup script for BarqDrop - builds a standard Windows installer.
; Compiled by Build.bat when Inno Setup 6 (ISCC.exe) is available.

#define AppName "BarqDrop"
#define AppVersion "1.0.0"
#define AppPublisher "BarqDrop"
#define AppExe "BarqDrop.exe"

[Setup]
AppId={{7C4B4E2E-9B1C-4B4B-9E2F-BARQDROP0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=BarqDrop-Setup-{#AppVersion}
SetupIconFile=..\assets\barqdrop.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "firewall"; Description: "Allow BarqDrop through Windows Firewall on private networks (needed for discovery and transfers)"; GroupDescription: "Network:"

[Files]
Source: "..\dist\BarqDrop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Discovery is UDP 45877; transfers are TCP 45878. Program-scoped rules cover
; both even if the user changes the ports later in Settings.
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""BarqDrop (in)"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private,domain"; Flags: runhidden; Tasks: firewall
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""BarqDrop (out)"" dir=out action=allow program=""{app}\{#AppExe}"" enable=yes profile=private,domain"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""BarqDrop (in)"""; Flags: runhidden; RunOnceId: "DelFwIn"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""BarqDrop (out)"""; Flags: runhidden; RunOnceId: "DelFwOut"
