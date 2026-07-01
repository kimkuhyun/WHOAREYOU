# -*- coding: utf-8 -*-
"""카카오톡 '나에게 보내기'(memo API) + 로컬 루프백 OAuth 로그인.

정직 고지(§5): map REST 키만으로는 안 됨 — 카카오 개발자콘솔에서 1회
  ① 카카오 로그인 ON  ② Redirect URI = http://localhost:{KAKAO_OAUTH_PORT}/oauth 등록
  ③ 동의항목 '카카오톡 메시지 전송(talk_message)' 추가
설정 후 [연결](connect)로 OAuth 로그인 → refresh_token 저장 → send()로 나에게 톡 발송.
"""
import json
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

import config


class KakaoNotifier:
    AUTH = "https://kauth.kakao.com/oauth/authorize"
    TOKEN = "https://kauth.kakao.com/oauth/token"
    SEND = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

    def __init__(self, rest_key: str = "", refresh_token: str = "", client_secret: str = ""):
        self.rest_key = rest_key or ""
        self.refresh_token = refresh_token or ""
        self.client_secret = client_secret or ""   # 카카오 앱에 Client Secret 켜져 있으면 필요
        self._access = None

    @property
    def connected(self) -> bool:
        return bool(self.rest_key and self.refresh_token)

    @property
    def _redirect(self) -> str:
        return f"http://localhost:{config.KAKAO_OAUTH_PORT}/oauth"

    # ── OAuth 로그인(브라우저 + 로컬 루프백으로 code 수신) ──
    def connect(self, timeout: int = 120) -> dict:
        if not self.rest_key:
            return {"ok": False, "error": "카카오맵 REST 키를 먼저 저장하세요(키 관리 탭)."}
        box: dict = {}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path != "/oauth":
                    self.send_response(404); self.end_headers(); return
                q = urllib.parse.parse_qs(u.query)
                box["code"] = q.get("code", [None])[0]
                box["error"] = q.get("error_description", q.get("error", [None]))[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
                msg = "연결 완료! 이 탭을 닫고 앱으로 돌아가세요." if box.get("code") else f"실패: {box.get('error')}"
                self.wfile.write(("<html><body style='font-family:sans-serif;padding:48px;color:#0f172a'>"
                                  "<h2 style='color:#047857'>WHOAREYOU</h2><p>" + msg + "</p></body></html>").encode("utf-8"))

            def log_message(self, *a):
                pass

        try:
            srv = HTTPServer(("localhost", config.KAKAO_OAUTH_PORT), H)
        except OSError as e:
            return {"ok": False, "error": f"로컬 포트 {config.KAKAO_OAUTH_PORT} 사용 중: {e}"}
        srv.timeout = 3
        url = (f"{self.AUTH}?client_id={self.rest_key}"
               f"&redirect_uri={urllib.parse.quote(self._redirect)}"
               "&response_type=code&scope=talk_message")
        webbrowser.open(url)
        end = time.time() + timeout
        while time.time() < end and not box.get("code") and not box.get("error"):
            srv.handle_request()      # favicon 등 넘기고 /oauth 잡을 때까지
        srv.server_close()

        if not box.get("code"):
            return {"ok": False, "error": box.get("error")
                    or "로그인 취소/시간초과. 콘솔의 Redirect URI·동의항목을 확인하세요."}
        data = {"grant_type": "authorization_code", "client_id": self.rest_key,
                "redirect_uri": self._redirect, "code": box["code"]}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            j = httpx.post(self.TOKEN, timeout=15, data=data).json()
        except Exception as e:
            return {"ok": False, "error": f"토큰 교환 실패: {e}"}
        if not j.get("refresh_token"):
            err = j.get("error_description") or j
            if "client" in str(err).lower():        # Bad client credentials = Client Secret 문제
                err = str(err) + " → 카카오 콘솔 [보안] Client Secret을 '사용 안 함'으로 하거나 아래에 시크릿 입력"
            return {"ok": False, "error": f"토큰 실패: {err}"}
        self.refresh_token = j["refresh_token"]
        self._access = j.get("access_token")
        return {"ok": True, "refresh_token": self.refresh_token}

    def _ensure_access(self):
        if self._access:
            return self._access
        if not (self.rest_key and self.refresh_token):
            return None
        data = {"grant_type": "refresh_token", "client_id": self.rest_key,
                "refresh_token": self.refresh_token}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            j = httpx.post(self.TOKEN, timeout=15, data=data).json()
            self._access = j.get("access_token")
            if j.get("refresh_token"):          # 카카오가 갱신 주면 교체
                self.refresh_token = j["refresh_token"]
            return self._access
        except Exception:
            return None

    def send(self, text: str, link: str = "") -> dict:
        access = self._ensure_access()
        if not access:
            return {"ok": False, "error": "카카오 미연결(먼저 [연결])"}
        link = link or "https://www.saramin.co.kr"
        template = {"object_type": "text", "text": text[:400],
                    "link": {"web_url": link, "mobile_web_url": link}}
        try:
            r = httpx.post(self.SEND, timeout=15, headers={"Authorization": f"Bearer {access}"},
                           data={"template_object": json.dumps(template, ensure_ascii=False)})
            return {"ok": r.status_code == 200,
                    "error": None if r.status_code == 200 else r.text[:200]}
        except Exception as e:
            return {"ok": False, "error": str(e)}
