; ToolManager installer (Inno Setup 6). See installer/README.md and docs/DESIGN.md §11.
;
; Build (normally via scripts\build_windows.ps1):
;   ISCC.exe /DAppVersion=0.1.0 installer\ToolManager.iss
; Optional defines:
;   /DSourceDir=<folder holding ToolManager.exe and app-<version>\>  (default ..\dist\ToolManager)
;   /DDefaultsFile=<path to the department defaults.toml>           (bundled when given)
;   /DCloseApps=no   only for testing under Wine, which lacks the Windows Restart Manager

#ifndef AppVersion
  #error AppVersion is required, e.g. ISCC /DAppVersion=0.1.0 ToolManager.iss
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ToolManager"
#endif
#ifndef CloseApps
  #define CloseApps "yes"
#endif
#define AppDir "app-" + AppVersion

[Setup]
; Never change AppId: Windows uses it to recognise upgrades and the uninstall entry.
AppId={{6F0C2E4B-7B1D-4F43-9A0E-3C5D2B9E7A11}
AppName=ToolManager
AppVersion={#AppVersion}
AppVerName=ToolManager {#AppVersion}
VersionInfoVersion={#AppVersion}
; Per-user install: no administrator rights, entry under HKCU, files in %LOCALAPPDATA%.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\ToolManager
; The data folder is fixed (%LOCALAPPDATA%\ToolManager), so the install folder is too.
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=ToolManagerSetup-{#AppVersion}
UninstallDisplayName=ToolManager
UninstallDisplayIcon={app}\ToolManager.exe
; Created by the running app; Setup and the uninstaller ask the user to close it first.
AppMutex=ToolManagerAppMutex
CloseApplications={#CloseApps}
RestartApplications=no
SetupLogging=yes

[Languages]
#ifexist "compiler:Languages\ChineseTraditional.isl"
Name: "zh_TW"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#else
; ChineseTraditional.isl is a community translation that is not shipped with Inno Setup.
; Put it into the Inno Setup "Languages" folder to get a Chinese wizard; our own messages
; below are Chinese either way.
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif

[CustomMessages]
DesktopIcon=建立桌面捷徑
LaunchNow=立即啟動 ToolManager
DataKept=ToolManager 已移除。%n%n你安裝的工具、回收區與設定都保留在：%n%1%n%n如果不再需要，可以自行刪除這個資料夾。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\ToolManager.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\{#AppDir}\*"; DestDir: "{app}\{#AppDir}"; Excludes: ".complete"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; Last, so the launcher never sees a half-copied version folder as complete.
Source: "{#SourceDir}\{#AppDir}\.complete"; DestDir: "{app}\{#AppDir}"; Flags: ignoreversion
#ifdef DefaultsFile
Source: "{#DefaultsFile}"; DestDir: "{app}"; DestName: "defaults.toml"; Flags: ignoreversion
#endif

[Icons]
Name: "{userprograms}\ToolManager"; Filename: "{app}\ToolManager.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\ToolManager"; Filename: "{app}\ToolManager.exe"; WorkingDir: "{app}"; \
  Tasks: desktopicon

[Run]
Filename: "{app}\ToolManager.exe"; Description: "{cm:LaunchNow}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program files only: every ToolManager version folder (including ones added later by
; self-update) and leftovers of interrupted downloads. User data is kept on purpose:
; tools\, trash\, cache\, logs\ and settings.toml stay (see the message shown below).
Type: filesandordirs; Name: "{app}\app-*"
Type: filesandordirs; Name: "{app}\.staging-app-*"
Type: filesandordirs; Name: "{app}\.download-*"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    // "Start at sign-in" is switched on inside ToolManager; drop it with the program.
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run',
      'ToolManager');
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox(FmtMessage(CustomMessage('DataKept'), [ExpandConstant('{app}')]),
      mbInformation, MB_OK);
end;
