; Inno Setup script for the TTS Studio Windows installer.
; Built in CI:  iscc /DAppVersion=1.0.0 packaging\windows\installer.iss
;
; The app itself is a single PyInstaller executable; this wraps it in a normal
; Windows install experience (Start-menu entry, optional desktop icon, proper
; uninstaller). Model data lives in %LOCALAPPDATA%\TTS Studio and is offered
; for deletion at uninstall time.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "TTS Studio"
#define AppPublisher "TTS Studio"
#define AppURL "https://github.com/Morpheusmatrixbot/tts-studio"
#define AppExeName "TTSStudio.exe"

[Setup]
AppId={{8E3C1A94-6D2F-4B57-9C31-2A7F0E5D8B14}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=TTS-Studio-{#AppVersion}-Windows-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; No admin rights needed, so the installer runs for a standard user account.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
english.RemoveData=Also delete downloaded voice engines and models?%n%nThis frees several gigabytes but means re-downloading them if you reinstall.
russian.RemoveData=Удалить также скачанные движки и модели?%n%nЭто освободит несколько гигабайт, но при повторной установке их придётся скачивать заново.

[Code]
// Engines and model weights are multi-gigabyte downloads kept outside {app}.
// Leaving them behind silently would be a surprise, so ask once on uninstall.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) then
      if MsgBox(ExpandConstant('{cm:RemoveData}'), mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
