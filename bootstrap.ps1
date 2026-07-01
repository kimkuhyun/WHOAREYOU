# WHOAREYOU 2.0 최초 구성 — uv로 격리 Python + 의존성 + AI 모델 설치.
# 인스톨러가 설치 막바지에 1회 실행(콘솔로 진행 표시). 실패해도 첫 실행 때 자동 재시도.
param([string]$AppDir = $PSScriptRoot)
$ErrorActionPreference = "Continue"
$uv   = Join-Path $AppDir "uv.exe"
$venv = Join-Path $AppDir ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
$req  = Join-Path $AppDir "requirements.txt"

function Say($m) { Write-Host $m }
Say "==============================================================="
Say "  WHOAREYOU 최초 구성 (인터넷 필요 · 수 분~십수 분 · 약 3GB)"
Say "  창을 닫지 마세요. 완료되면 자동으로 끝납니다."
Say "==============================================================="

# uv 없으면 내려받기(번들 안 된 경우 대비)
if (-not (Test-Path $uv)) {
  Say "[uv] 패키지 도구 내려받는 중..."
  try {
    $z = Join-Path $env:TEMP "uv_whoareyou.zip"
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/0.11.24/uv-x86_64-pc-windows-msvc.zip" -OutFile $z -UseBasicParsing
    Expand-Archive $z -DestinationPath $AppDir -Force
  } catch { Say "[오류] uv 내려받기 실패 — 인터넷 확인 후 bootstrap.ps1 다시 실행."; exit 1 }
}

# 1) 격리 Python 3.11 (uv가 조달 — 시스템 Python 불필요)
if (-not (Test-Path $py)) {
  Say "[1/3] Python 3.11 준비 중 (없으면 uv가 자동 설치 — 사용자 Python 불필요)..."
  & $uv python install 3.11
  & $uv venv $venv --python 3.11
}

# 2) 라이브러리 설치 (torch 등 ~600MB) — 3회 재시도
Say "[2/3] 라이브러리 설치 중 (~600MB, 시간 걸립니다)..."
$ok = $false
for ($i = 1; $i -le 3; $i++) {
  & $uv pip install --python $py -r $req
  if ($LASTEXITCODE -eq 0) { $ok = $true; break }
  Say "    설치 실패 — 재시도 $i/3..."; Start-Sleep 3
}
if (-not $ok) { Say "[오류] 라이브러리 설치 실패. 인터넷/방화벽 확인 후 bootstrap.ps1 다시 실행."; exit 1 }

# 3) AI 모델 워밍 (bge-reranker ~2.3GB + easyocr 한국어) — 첫 검색이 바로 완전 기능
Say "[3/3] AI 모델 내려받는 중 (~2.4GB · 가장 오래 걸립니다)..."
$env:PYTHONUTF8 = "1"
& $py -c "import sys; sys.path.insert(0, r'$AppDir\app'); import reranker; reranker._get(); print('  - 리랭커(매칭) 준비 완료'); import easyocr; easyocr.Reader(['ko','en'], gpu=False); print('  - OCR(이미지 공고) 준비 완료')"
if ($LASTEXITCODE -ne 0) { Say "[경고] 모델 다운로드가 미완일 수 있어요 — 첫 검색 때 자동으로 마저 받습니다." }

Say "==============================================================="
Say "  구성 완료! WHOAREYOU를 시작할 수 있습니다."
Say "==============================================================="
