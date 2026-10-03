; Inno Setup 6 script for ReelFramer-Setup.exe (run by desktop\build_windows.ps1 after PyInstaller).
; Installs for the current user only (no admin rights): %LOCALAPPDATA%\Programs\Reel Framer,
; a Start menu entry, an optional desktop shortcut, and an uninstaller in Apps & features.
; Settings and videos are not touched by uninstalling: they live in %APPDATA%\Reel Framer and
; Videos\Reel Framer. AppId identifies the app across versions, so a newer installer upgrades
; in place; it must never change.

#define AppName "Reel Framer"
#define AppVersion GetEnv("REEL_FRAMER_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{1B8F688E-29A8-457F-94AA-ECD6EED3B972}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Metty AI
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ReelFramer-Setup
SetupIconFile=build\ReelFramer.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent
