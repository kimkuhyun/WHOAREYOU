# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 스펙 — pywebview+pystray+APScheduler+curl_cffi 한 프로세스, 단일 폴더 빌드.

빌드: .venv\\Scripts\\pyinstaller whoareyou.spec  →  dist\\WHOAREYOU\\WHOAREYOU.exe
그 다음 Inno Setup(setup.iss)으로 WHOAREYOU_setup.exe 생성.
"""
from PyInstaller.utils.hooks import collect_all

_datas, _bins, _hidden = [], [], []
for pkg in ["webview", "pystray", "curl_cffi", "winotify", "apscheduler",
            "kiwipiepy", "kiwipiepy_model", "pdfplumber", "pdfminer", "rapidfuzz", "PIL"]:
    try:
        d, b, h = collect_all(pkg)
        _datas += d; _bins += b; _hidden += h
    except Exception:
        pass

# app/의 flat 모듈(자립)
_app_mods = ["config", "api", "pipeline", "store", "crawler", "jotso_client",
             "jobplanet_client", "commute", "matcher", "scoring", "dedup", "skills",
             "notifier", "scheduler", "geo_kakao", "geo_odsay", "ats", "api_status",
             "kakao_notifier", "user_settings", "jobfilter", "reranker", "ocr"]

a = Analysis(
    ["run.py"],
    pathex=["app"],
    binaries=_bins,
    datas=_datas + [("web", "web")],
    hiddenimports=_hidden + _app_mods,
    excludes=["fastembed", "onnxruntime", "torch", "tensorflow",   # 임베딩/OCR 무거운 dep 제외(경량 exe)
              "rapidocr_onnxruntime", "cv2", "tokenizers", "transformers",
              "easyocr", "surya", "torchvision", "scipy", "skimage", "sklearn"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WHOAREYOU",
          console=False, icon="web/assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="WHOAREYOU")
