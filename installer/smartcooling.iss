; SmartCooling Controller — Windows installer (Inno Setup 6) — spec §260, §261.
;
; Build with:
;   ISCC.exe installer\smartcooling.iss /DAppVersion=1.0.0 /DBuildId=abc123
; or through scripts/installer/build_installer.py, which fills the defines.
;
; Design notes
; ------------
; * Per-user install by default (PrivilegesRequired=lowest) so no UAC prompt is
;   needed and the app can be installed by a standard user.  Hardware monitoring
;   degrades gracefully without elevation (spec §215, §216).
; * The optional "install for all users" path is offered through the standard
;   Inno privilege dialog, which the user can decline.
; * The data directory is *never* touched by the installer or the uninstaller;
;   removing user data is an explicit, separate choice on the uninstall page.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef BuildId
  #define BuildId "dev"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\SmartCoolingController"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\installer"
#endif

#define AppName "SmartCooling Controller"
#define AppPublisher "SmartCooling"
#define AppExeName "SmartCoolingController.exe"
#define AppId "{{9C3B1C2E-6D7A-4B0E-9E2A-5F1D8A7C4B21}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir={#OutputDir}
OutputBaseFilename=SmartCoolingController-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE.txt
SetupLogging=yes
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup";     Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"
Name: "portable";    Description: "Store my data next to the application (portable mode)"; GroupDescription: "Data:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Portable mode is expressed by dropping the marker file the app looks for.
Filename: "{app}\{#AppExeName}"; Parameters: "--portable"; Description: "Launch {#AppName} in portable mode"; Flags: nowait postinstall skipifsilent; Tasks: portable
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked; Tasks: not portable
Filename: "{app}\{#AppExeName}"; Parameters: "--safe --diagnostics"; Description: "Open Diagnostics (safe mode)"; Flags: nowait postinstall skipifsilent unchecked

[Registry]
; Optional start-at-login for the *current user* only.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "SmartCoolingController"; ValueData: """{app}\{#AppExeName}"" --no-tray"; \
  Flags: uninsdeletevalue; Tasks: startup

[UninstallDelete]
; Nothing: user data lives under the profile and is preserved on purpose.

[Code]
// The portable marker is created next to the executable when the user picked
// the portable task; the uninstaller removes it with the install directory.
procedure CurStepChanged(CurStep: TSetupStep);
var
  MarkerPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('portable') then
    begin
      MarkerPath := ExpandConstant('{app}\portable.flag');
      SaveStringToFile(MarkerPath, 'portable', False);
    end;
  end;
end;
