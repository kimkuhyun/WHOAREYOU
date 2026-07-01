; WHOAREYOU 2.0 인스톨러 (Inno Setup)
; 빌드: pyinstaller whoareyou.spec  →  ISCC setup.iss  →  Output\WHOAREYOU_setup.exe

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
Source: "dist\WHOAREYOU\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\WHOAREYOU"; Filename: "{app}\WHOAREYOU.exe"
Name: "{userdesktop}\WHOAREYOU"; Filename: "{app}\WHOAREYOU.exe"; Tasks: desktopicon
Name: "{userstartup}\WHOAREYOU"; Filename: "{app}\WHOAREYOU.exe"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "바탕화면 아이콘"; GroupDescription: "추가 아이콘:"
Name: "startup"; Description: "Windows 시작 시 자동 실행(트레이 상주)"; GroupDescription: "시작 옵션:"

[Run]
Filename: "{app}\WHOAREYOU.exe"; Description: "지금 실행"; Flags: nowait postinstall skipifsilent

; ── 사용자 PC엔 uv·Python 불필요(PyInstaller가 런타임·의존성 전부 번들). ──
; 유일한 시스템 요건 = Edge WebView2 런타임(pywebview 렌더러). Win11엔 기본 내장이지만,
; 없으면 아래 [Code]가 MS 부트스트래퍼를 받아 조용히 설치한다.
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
      // 오프라인 등으로 실패해도 설치는 계속(Win11은 대개 내장). 첫 실행 시 안내.
    end;
  end;
end;
