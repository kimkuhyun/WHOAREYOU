# -*- coding: utf-8 -*-
"""이미지 공고 OCR = easyocr(한국어). 무거움(torch) → 지연로드 + graceful(없으면 "").

surya는 llama.cpp 바이너리 필요·rapidocr는 한국어 미지원 → easyocr 채택(실측 한국어 OK).
경량 배포(torch 미포함)에선 자동으로 skip → 제목 기반 폴백(crawler)이 대신 매칭.
"""
import threading

_reader = None
_reader_lock = threading.Lock()
_failed = False
_cache: dict[str, str] = {}


def _get_reader():
    global _reader, _failed
    if _failed:
        return None
    if _reader is None:
        with _reader_lock:
            if _reader is None and not _failed:
                try:
                    import easyocr
                    _reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
                except Exception:
                    _failed = True
                    return None
    return _reader


def image_text(image_bytes: bytes, cache_key: str = "") -> str:
    """이미지 바이트 → 한국어 텍스트. easyocr 없으면 ""(폴백). URL 캐시."""
    if cache_key and cache_key in _cache:
        return _cache[cache_key]
    reader = _get_reader()
    if reader is None or not image_bytes:
        return ""
    try:
        import io
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        txt = " ".join(reader.readtext(np.array(img), detail=0, paragraph=True))
    except Exception:
        txt = ""
    if cache_key:
        _cache[cache_key] = txt
    return txt
