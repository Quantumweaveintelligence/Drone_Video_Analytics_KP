; ============================================================
; DVAS Edge Operator — Inno Setup Installer Script
; ============================================================
; Build-time secrets are deliberately not accepted. Setup collects credentials, then the
; launcher migrates them to the current user's DPAPI store on first start.
; ============================================================

#define AppName      "Kerala Police DVAS Edge Operator"
#ifndef AppVersion
  #define AppVersion "2.0.2"
#endif
; Pilot builds open a console window with live logs; build with /DConsoleMode=0 to hide it.
#ifndef ConsoleMode
  #define ConsoleMode "1"
#endif
#if ConsoleMode == "1"
  #define LaunchExe  "python.exe"
  #define LaunchArgs " --console"
#else
  #define LaunchExe  "pythonw.exe"
  #define LaunchArgs ""
#endif
#define AppPublisher "Kerala Police — Drone Video Analytics"
#define AppExeName   "edge_launcher.py"

#ifndef DefaultIngestUrl
  #define DefaultIngestUrl "https://qlbqcydnz8dzxu-80.proxy.runpod.net"
#endif
#ifdef SigningThumbprint
  #ifndef TimestampUrl
    #define TimestampUrl "http://timestamp.digicert.com"
  #endif
#endif

[Setup]
AppId={{D8A7E3F1-4B2C-4E5D-9A1B-3C7F8E2D6A4B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\DVAS_Edge
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=DVAS_Edge_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=dist\static\favicon.ico
WizardStyle=modern
DisableDirPage=auto
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\static\favicon.ico
ArchitecturesInstallIn64BitMode=x64compatible
#ifdef SigningThumbprint
SignTool=DVAS
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

#ifdef SigningThumbprint
[SignTools]
Name: "DVAS"; Command: "signtool.exe"; Parameters: "sign /sha1 {#SigningThumbprint} /fd SHA256 /tr {#TimestampUrl} /td SHA256 $f"
#endif

[Files]
; Main application distribution
Source: "dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Data directories (persist across upgrades)
Name: "{localappdata}\DVAS_Edge\config"
Name: "{localappdata}\DVAS_Edge\data"
Name: "{localappdata}\DVAS_Edge\logs"
Name: "{localappdata}\DVAS_Edge\data\buffer_storage"

[Icons]
; Desktop shortcut (console window in pilot builds; logs always go to %LOCALAPPDATA%\DVAS_Edge\logs)
Name: "{autodesktop}\DVAS Edge Operator"; Filename: "{app}\python_runtime\{#LaunchExe}"; Parameters: """{app}\edge_launcher.py""{#LaunchArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"; Comment: "Launch DVAS Edge Operator"
; Start menu shortcuts
Name: "{autoprograms}\DVAS Edge\DVAS Edge Operator"; Filename: "{app}\python_runtime\{#LaunchExe}"; Parameters: """{app}\edge_launcher.py""{#LaunchArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"
Name: "{autoprograms}\DVAS Edge\DVAS Edge (support console)"; Filename: "{app}\python_runtime\python.exe"; Parameters: """{app}\edge_launcher.py"" --console"; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"; Comment: "Same app with a console window showing live logs"
Name: "{autoprograms}\DVAS Edge\Change Ingest URL"; Filename: "notepad.exe"; Parameters: """{localappdata}\DVAS_Edge\config\edge.env"""
Name: "{autoprograms}\DVAS Edge\View Logs"; Filename: "{localappdata}\DVAS_Edge\logs"

[Run]
Filename: "{app}\python_runtime\{#LaunchExe}"; Parameters: """{app}\edge_launcher.py""{#LaunchArgs}"; WorkingDir: "{app}"; Description: "Launch DVAS Edge now"; Flags: postinstall skipifsilent nowait

[UninstallDelete]
; Clean up Python bytecode on uninstall (but NOT the data directory)
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\src\__pycache__"
Type: filesandordirs; Name: "{app}\ingest\__pycache__"
Type: filesandordirs; Name: "{app}\config\__pycache__"
Type: filesandordirs; Name: "{app}\src\launcher\__pycache__"
Type: filesandordirs; Name: "{app}\src\utils\__pycache__"

[Code]
var
  IngestPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  { Ingest Server URL page }
  IngestPage := CreateInputQueryPage(wpSelectDir,
    'DVAS Ingest Server',
    'Ingest server address',
    'Enter the DVAS Ingest Server URL. After setup, sign in to DVAS with your operator account. The installer does not collect or store your operator password.');
  IngestPage.Add('Ingest Server URL:', False);
  IngestPage.Values[0] := '{#DefaultIngestUrl}';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = IngestPage.ID then
  begin
    if Trim(IngestPage.Values[0]) = '' then
    begin
      MsgBox('Enter the Ingest Server URL.', mbError, MB_OK);
      Result := False;
    end
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvFile: string;
  Lines: TStringList;
  RawUrl: string;
  CloudBase: string;
  LowerUrl: string;
begin
  if CurStep = ssPostInstall then
  begin
    EnvFile := ExpandConstant('{localappdata}\DVAS_Edge\config\edge.env');
    RawUrl := Trim(IngestPage.Values[0]);
    { Remove trailing slash if present }
    while (Length(RawUrl) > 0) and (RawUrl[Length(RawUrl)] = '/') do
      RawUrl := Copy(RawUrl, 1, Length(RawUrl) - 1);

    { Accept https://host, https://host/ingest or https://host/ingest/frame: keep the host part }
    LowerUrl := Lowercase(RawUrl);
    if (Length(LowerUrl) >= 6) and (Copy(LowerUrl, Length(LowerUrl) - 5, 6) = '/frame') then
      RawUrl := Copy(RawUrl, 1, Length(RawUrl) - 6);
    LowerUrl := Lowercase(RawUrl);
    if (Length(LowerUrl) >= 7) and (Copy(LowerUrl, Length(LowerUrl) - 6, 7) = '/ingest') then
      RawUrl := Copy(RawUrl, 1, Length(RawUrl) - 7);
    CloudBase := RawUrl;
    LowerUrl := Lowercase(RawUrl);

    Lines := TStringList.Create;
    try
      Lines.Add('# DVAS Edge Operator Configuration');
      Lines.Add('# Generated by installer on ' + GetDateTimeString('yyyy-mm-dd', #0, #0));
      Lines.Add('');
      Lines.Add('DVAS_CLOUD_URL=' + CloudBase);
      Lines.Add('# Ingest server (RunPod) URL. Frame, mission, token and video endpoints are derived from it.');
      Lines.Add('# It can also be changed in the app: Settings > Ingest server URL.');
      Lines.Add('INGEST__BASE_URL=' + CloudBase);
      Lines.Add('INGEST__TOKEN_URL=' + CloudBase + '/realms/kerala-police-drone/protocol/openid-connect/token');
      Lines.Add('INGEST__CLIENT_ID=drone-ingest-client');
      Lines.Add('# Authenticate to ingest with the operator session after signing in to DVAS.');
      Lines.Add('INGEST__DRONE_ID=DRN-000');
      if Pos('https://', LowerUrl) = 1 then
        Lines.Add('INGEST__TLS_VERIFY=true')
      else
      begin
        Lines.Add('INGEST__TLS_VERIFY=false');
        Lines.Add('# Plain http accepted only because the installer was given an http:// LAN address');
        Lines.Add('DVAS_ALLOW_HTTP_INGEST=true');
      end;
      Lines.Add('EDGE_API_KEY=');
      Lines.Add('REQUIRE_EDGE_AUTH=true');
      Lines.Add('# Local ports (auto-detects free port if busy)');
      Lines.Add('# DVAS_PORT=8765');
      Lines.Add('# DVAS_CORE_PORT=8766');
      Lines.Add('ENV=production');
      Lines.SaveToFile(EnvFile);
    finally
      Lines.Free;
    end;
  end;
end;
