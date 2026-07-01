; WHOAREYOU 2.0 인스톨러 (Inno Setup) — 원클릭 완전 기능(리랭커/OCR 포함)
; 배포 모델: 경량 exe가 아니라 "소스 + uv 부트스트랩(실 Python)"으로 구동해야 heavy deps가 산다.
; 빌드: (repo 루트에 uv.exe 배치) → ISCC setup.iss → Output\WHOAREYOU_setup.exe
; 설치 시: 파일 복사 → bootstrap.ps1(uv venv + requirements + bge-reranker/easyocr 모델 워밍) → 완료

[Setup]
AppName=WHOAREYOU
AppVersion=2.0
AppPublisher=kimkuhyn
DefaultDirName={autopf}\WHOAREYOU
DefaultGroupName=WHOAREYOU
DisableProgramGroupPage=yes
OutputBaseFilename=WHOAREYOU_setup
SetupIconFile=web\assets\icon.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
; 앱 소스(개발 레이아웃 그대로). ⚠ 개발자 키/DB는 제외(user_settings.json·*.db·__pycache__)
Source: "app\*"; DestDir: "{app}\app"; Excludes: "user_settings.json,*.db,__pycache__\*,__pycache__"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "web\*"; DestDir: "{app}\web"; Excludes: "__pycache__\*"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "run.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 실행 = venv의 pythonw run.py (콘솔 없음, 트레이 상주). .venv는 설치 중 bootstrap이 생성.
Name: "{group}\WHOAREYOU"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\web\assets\icon.ico"
Name: "{userdesktop}\WHOAREYOU"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\web\assets\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\WHOAREYOU"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "바탕화면 아이콘"; GroupDescription: "추가 아이콘:"
Name: "startup"; Description: "Windows 시작 시 자동 실행(트레이 상주)"; GroupDescription: "시작 옵션:"

[Run]
; 최초 구성(의존성 ~600MB + AI 모델 ~2.4GB) — 콘솔로 진행 표시. 인터넷 필요, 수 분~십수 분.
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\bootstrap.ps1"" -AppDir ""{app}"""; \
  StatusMsg: "AI 라이브러리·모델 구성 중 (수 분~십수 분, 인터넷 필요)..."; \
  Flags: waituntilterminated
; 설치 직후 실행(선택)
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; \
  Description: "지금 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 부트스트랩이 만든 venv도 제거
Type: filesandordirs; Name: "{app}\.venv"

; ── 시스템 요건 = Edge WebView2(pywebview 렌더러). Win11 내장, 없으면 아래가 자동 설치. ──
[Code]
function WebView2Installed: Boolean;
var v: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var rc: Integer;
begin
  if (CurStep = ssInstall) and (not WebView2Installed) then begin
    try
      DownloadTemporaryFile('https://go.microsoft.com/fwlink/p/?LinkId=2124703',
                            'MicrosoftEdgeWebview2Setup.exe', '', nil);
      Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
           '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, rc);
    except
      // 오프라인 등으로 실패해도 설치는 계속(Win11은 대개 내장).
    end;
  end;
end;
