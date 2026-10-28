; Paper Tablet: Windows installer (Inno Setup 6). Build it with installer\build.ps1, which
; stages build\app and build\runtime first and passes /DAppVersion from pc\version.py.
;
; Per-user install, no admin rights: %LOCALAPPDATA%\Programs\Paper Tablet unless the user picks
; another folder they can write to. Ships its own
; Python runtime; the only system prerequisite is the WebView2 runtime, installed only if missing.
; Settings (%APPDATA%\PaperTablet) and the SSH key (~\.ssh\rmtablet_ed25519) are never touched.

#ifndef AppVersion
  #error Run installer\build.ps1 (it passes /DAppVersion)
#endif
#define AppName "Paper Tablet"
#define AppExe "runtime\python\Paper Tablet.exe"

[Setup]
AppId={{6F3B2C1E-8A4D-4E57-9B0C-2D7A5E9F1C38}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/AmazingBodilyFluids/paper-tablet
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; The user may pick the folder; browsing appends "\Paper Tablet", and an upgrade reuses the
; previous folder.
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The app holds this mutex while it runs (app.py, already_running): setup and uninstall ask
; the user to close it first.
AppMutex=Local\PaperTablet.SingleInstance
OutputDir=dist
OutputBaseFilename=PaperTablet-Setup-{#AppVersion}
SetupIconFile=paper-tablet.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "build\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "build\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "build\markers\installed"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\prereqs\MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not WebView2Installed

[InstallDelete]
; An upgrade replaces the app code wholesale, so files dropped from a newer version don't linger.
; Only over an earlier install (its marker file is there), never in a folder of someone else's.
Type: filesandordirs; Name: "{app}\pc"; Check: IsUpgrade
Type: filesandordirs; Name: "{app}\tablet"; Check: IsUpgrade

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: """{app}\pc\app.py"""; WorkingDir: "{app}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: """{app}\pc\app.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."; Flags: waituntilterminated; Check: not WebView2Installed
Filename: "{app}\{#AppExe}"; Parameters: """{app}\pc\app.py"""; WorkingDir: "{app}"; \
    Description: "Start {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Python writes __pycache__ folders at run time, so remove Paper Tablet's own folders whole.
; Only these: the user may have chosen a folder that holds other files too. {app} itself goes
; only if it ends up empty.
Type: filesandordirs; Name: "{app}\pc"
Type: filesandordirs; Name: "{app}\tablet"
Type: filesandordirs; Name: "{app}\runtime"
Type: dirifempty; Name: "{app}"

[Code]
// WebView2 ships with Windows 11 and current Windows 10. Only run the bootstrapper when the
// registry keys Microsoft documents for the Evergreen runtime are all absent.
function WebView2Installed: Boolean;
var
  V: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'))
    or (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
end;

function IsUpgrade: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\installed'));
end;

// Setup runs without admin rights, so a folder like Program Files would fail halfway through
// copying. Check the chosen folder (or the nearest one that exists) takes a file first.
function CanWriteTo(Dir: String): Boolean;
var
  Probe: String;
begin
  while (not DirExists(Dir)) and (ExtractFileDir(Dir) <> Dir) do
    Dir := ExtractFileDir(Dir);
  Probe := AddBackslash(Dir) + 'paper-tablet-write-test.tmp';
  Result := SaveStringToFile(Probe, '', False);
  if Result then
    DeleteFile(Probe);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not CanWriteTo(WizardDirValue) then
  begin
    SuppressibleMsgBox('Paper Tablet can''t be installed in' #13#10 + WizardDirValue + #13#10#13#10 +
      'That folder needs administrator rights. Choose a folder in your user profile or on another drive, ' +
      'for example D:\Apps\Paper Tablet.', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;
