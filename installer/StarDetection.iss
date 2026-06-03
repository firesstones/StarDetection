; Installeur Star Detection (fork Circus Launcher).
; Bundle PyInstaller leger (sans Tesseract) qui delegue l'OCR a Circus OCR.
#define AppName "Star Detection"
#define AppVersion "0.3.1-launcher"
#define AppPublisher "Circus / Kainan"
#define AppExeName "StarDetection.exe"
; Dossier de sortie PyInstaller (onedir)
#define DistDir "..\build-pyinstaller\dist\StarDetection"
#define IconFile "..\build-pyinstaller\star_detection.ico"

[Setup]
AppId={{B5E2B7C1-9A4D-4F2E-9C3A-0F1A2B3C4D5E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\StarDetection
DisableDirPage=no
DefaultGroupName=Circus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-release
OutputBaseFilename=StarDetection_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile={#IconFile}
CloseApplications=yes
CloseApplicationsFilter=StarDetection.exe

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
; Tout le bundle PyInstaller (exe + _internal + liste.csv + version.json)
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Star Detection"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Star Detection"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Creer une icone sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Circus\Tools\star-detection"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Circus\Tools\star-detection"; ValueType: string; ValueName: "ExecutablePath"; ValueData: "{app}\{#AppExeName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Circus\Tools\star-detection"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey
