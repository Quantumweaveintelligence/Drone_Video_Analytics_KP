; ============================================================
; DVAS Edge Operator — Inno Setup Installer Script
; ============================================================
; Compile with:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DClientSecret=xxx /DEdgeApiKey=yyy ^
;       [/DIngestPassword=zzz] [/DDefaultIngestUrl=https://<pod>-80.proxy.runpod.net] dvas_edge_installer.iss
; ClientSecret and EdgeApiKey are required (the build stops without them).
; ============================================================

#define AppName      "Kerala Police DVAS Edge Operator"
#define AppVersion   "1.0.0"
#define AppPublisher "Kerala Police — Drone Video Analytics"
#define AppExeName   "edge_launcher.py"

; Build-time parameters (pass via /D on command line). There are no defaults:
; a secret compiled into the installer would ship to every laptop.
#ifndef ClientSecret
  #error ClientSecret not set. Build with: ISCC.exe /DClientSecret=... /DEdgeApiKey=... dvas_edge_installer.iss
#endif
#ifndef EdgeApiKey
  ; Empty: each laptop generates its own random key on first start (stored with DPAPI)
  #define EdgeApiKey ""
#endif
#ifndef IngestPassword
  #define IngestPassword ""
#endif
#ifndef DefaultIngestUrl
  #define DefaultIngestUrl ""
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

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
; Desktop shortcut (no console window: pythonw.exe; logs go to %LOCALAPPDATA%\DVAS_Edge\logs)
Name: "{autodesktop}\DVAS Edge Operator"; Filename: "{app}\python_runtime\pythonw.exe"; Parameters: """{app}\edge_launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"; Comment: "Launch DVAS Edge Operator"
; Start menu shortcuts
Name: "{autoprograms}\DVAS Edge\DVAS Edge Operator"; Filename: "{app}\python_runtime\pythonw.exe"; Parameters: """{app}\edge_launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"
Name: "{autoprograms}\DVAS Edge\DVAS Edge (support console)"; Filename: "{app}\python_runtime\python.exe"; Parameters: """{app}\edge_launcher.py"" --console"; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"; Comment: "Same app with a console window showing live logs"
Name: "{autoprograms}\DVAS Edge\Change Ingest URL"; Filename: "notepad.exe"; Parameters: """{localappdata}\DVAS_Edge\config\edge.env"""
Name: "{autoprograms}\DVAS Edge\View Logs"; Filename: "{localappdata}\DVAS_Edge\logs"

[Run]
Filename: "{app}\python_runtime\pythonw.exe"; Parameters: """{app}\edge_launcher.py"""; WorkingDir: "{app}"; Description: "Launch DVAS Edge now"; Flags: postinstall skipifsilent nowait

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
    'Verify or edit the DVAS Ingest Server URL for this operator laptop.'#13#10 +
    'Enter the RunPod / ingest server address, e.g. https://<pod>-80.proxy.runpod.net');
  IngestPage.Add('Ingest Server URL:', False);
  IngestPage.Values[0] := '{#DefaultIngestUrl}';
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
      Lines.Add('INGEST__CLIENT_ID=drone-ingest-client');
      Lines.Add('# Secrets below are moved to an encrypted store (Windows DPAPI) on first start.');
      Lines.Add('INGEST__CLIENT_SECRET={#ClientSecret}');
      Lines.Add('# INGEST__USERNAME auto-detected from Windows computer name');
      Lines.Add('INGEST__PASSWORD={#IngestPassword}');
      Lines.Add('INGEST__DRONE_ID=DRN-000');
      if Pos('https://', LowerUrl) = 1 then
        Lines.Add('INGEST__TLS_VERIFY=true')
      else
      begin
        Lines.Add('INGEST__TLS_VERIFY=false');
        Lines.Add('# Plain http accepted only because the installer was given an http:// LAN address');
        Lines.Add('DVAS_ALLOW_HTTP_INGEST=true');
      end;
      Lines.Add('EDGE_API_KEY={#EdgeApiKey}');
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
