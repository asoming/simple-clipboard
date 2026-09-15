[Setup]
AppId={{368FC64E-2930-47FC-A68D-189369201651}
AppName=Simple Clipboard
AppVersion=0.3.0
AppPublisher=asoming
AppPublisherURL=https://github.com/asoming/simple-clipboard
DefaultDirName={localappdata}\Programs\SimpleClipboard
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=SimpleClipboard-0.3.0-preview-windows-x64-setup
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\SimpleClipboard.exe

[Files]
Source: "..\dist\SimpleClipboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Simple Clipboard"; Filename: "{app}\SimpleClipboard.exe"

[Registry]
; Only remove our opt-in login entry. User history is outside {app} and retained.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "AsomingSimpleClipboard"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\SimpleClipboard.exe"; Description: "Launch Simple Clipboard"; Flags: nowait postinstall skipifsilent unchecked
