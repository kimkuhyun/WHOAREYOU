# -*- coding: utf-8 -*-
"""3사 크롤 어댑터 (§8a 확정 선택자 · all-httpx)."""
import asyncio
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from lxml import html as LH

import config
from jobfilter import passes as _passes


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    jd: str = ""
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    is_image: bool = False
    career: str = ""      # "신입"|"경력"|"무관"|""  — 3사 상세 구조화 필드에서
    emp_type: str = ""    # "정규직"|"인턴직"|"계약직"… (정규화된 한글 라벨)
    edu_req: str = ""     # 요구 학력 정규화: "학력무관"|"고졸"|"초대졸"|"대졸"|"석사"|"박사"|""
    comp_type: str = ""   # 기업형태 원문(사람인 dl — "중소기업, 연구소" 등)
    img_urls: list = field(default_factory=list)   # 이미지 공고 본문 이미지 URL들(전부 OCR)


def _norm_edu(v: str) -> str:
    """학력 요구 정규화 — '대졸(4년제) 이상'→'대졸', '대학원(석사)이상'→'석사'."""
    v = v or ""
    if "박사" in v:
        return "박사"
    if "석사" in v or "대학원" in v:
        return "석사"
    if "초대졸" in v or "전문대" in v or "2년제" in v:
        return "초대졸"
    if "대졸" in v or "4년제" in v or "학사" in v:
        return "대졸"
    if "고졸" in v:
        return "고졸"
    if "무관" in v:
        return "학력무관"
    return ""


def _norm_emp(v: str) -> str:
    """고용형태 정규화(사람인·잡코리아 텍스트) — '정규직 수습 3개월'→'정규직'."""
    v = v or ""
    if "인턴" in v:
        return "인턴직"
    if "계약" in v:
        return "계약직"
    if "파견" in v:
        return "파견"
    if "프리랜" in v:
        return "프리랜서"
    if "아르바이트" in v or "알바" in v:
        return "아르바이트"
    if "정규" in v:
        return "정규직"
    return ""


def _norm_emp_wanted(et: str) -> str:
    """원티드 employment_type(regular/contract/intern…) → 한글."""
    return {"regular": "정규직", "contract": "계약직", "contractor": "계약직",
            "intern": "인턴직", "parttime": "아르바이트", "part_time": "아르바이트",
            "freelance": "프리랜서"}.get((et or "").lower().replace("-", "_"), "")


def _career_from_text(t: str) -> str:
    """잡코리아 experienceRequirements("신입·경력"/"경력무관"/"경력 3년" 등) → 정규화."""
    t = t or ""
    has_s, has_g = "신입" in t, "경력" in t
    if "무관" in t or (has_s and has_g):
        return "무관"
    if has_s:
        return "신입"
    if has_g:
        return "경력"
    return ""


def _career_from_field(v: str) -> str:
    """모집요강 '경력' 칸 값 전용('2년이상~5년이하'처럼 '경력' 단어 없이 연차만 있는 경우)."""
    if "무관" in v:
        return "무관"
    if "신입" in v and ("경력" in v or re.search(r"\d+\s*년", v)):
        return "무관"
    if "신입" in v:
        return "신입"
    if re.search(r"\d+\s*년|경력", v):
        return "경력"
    return ""


def _career_from_wanted(cd: dict) -> str:
    if not cd:
        return ""
    if cd.get("is_newbie"):
        return "무관" if (cd.get("annual_from") or 0) > 0 else "신입"
    return "경력"


class _Adapter:
    def __init__(self, client):
        self.c = client


class Saramin(_Adapter):
    BASE = "https://www.saramin.co.kr"
    EXP = {"신입": "1", "경력": "2"}   # exp_cd (실측). 경력무관=미지정
    # 고용형태 job_type=X (⚠ 대괄호 없이! job_type[]는 값 무시됨 — 실측). 정규직=1·계약=2·인턴=4…
    JOB_TYPE = {"정규직": "1", "계약직": "2", "인턴직": "4", "파견직": "6",
                "프리랜서": "9", "병역특례": "3", "아르바이트": "5", "위촉직": "8"}
    # ⚠ 학력(edu_max)은 동작 불규칙 → 학력은 클라측 필터로. 지역(loc)도 JS라 클라측.

    async def fetch(self, kw: str, n: int, filters: dict | None = None, skip=None) -> list[Job]:
        out: list[Job] = []
        cap = max(config.SCAN_CAP, n * config.SCAN_PER_TARGET)   # 목표(n) 비례 스캔 깊이
        f = filters or {}
        params = ["searchType=search", f"searchword={quote(kw)}",
                  "recruitPage=1", "recruitPageCount=100", "recruitSort=relation"]
        if self.EXP.get(f.get("career", "")):                        # 경력(서버측)
            params.append(f"exp_cd={self.EXP[f['career']]}")
        emps = f.get("emp_types") or []
        if len(emps) == 1 and emps[0] in self.JOB_TYPE:              # 고용형태: 단일일 때만 서버측
            params.append(f"job_type={self.JOB_TYPE[emps[0]]}")     # (복수 job_type은 깨짐 → 클라측)
        url = f"{self.BASE}/zf_user/search/recruit?" + "&".join(params)
        tree = LH.fromstring((await self.c.get(url)).text)
        seen: set[str] = set()
        scanned = 0
        for it in tree.cssselect(".item_recruit"):
            if len(out) >= n or scanned >= cap:   # 필터 통과분 n개 채우면 끝(cap=목표 비례 깊이)
                break
            a = it.cssselect(".job_tit a, .area_job a")
            if not a:
                continue
            m = re.search(r"rec_idx=(\d+)", a[0].get("href") or "")
            if not m:
                continue
            rec = m.group(1)
            view = f"{self.BASE}/zf_user/jobs/view?rec_idx={rec}"
            if view in seen or (skip and skip(view)):     # 중복·이미 처리(지원함/관심없음) → 스캔 전 제외
                continue
            seen.add(view)
            corp = it.cssselect(".corp_name a, .area_corp .corp_name, .area_corp a")
            try:
                dt = LH.fromstring((await self.c.get(view)).text)
            except Exception:
                continue
            scanned += 1
            lat = lng = addr = None
            mp = dt.cssselect("#map_0, .jv_cont.jv_location")
            if mp:
                lat, lng, addr = mp[0].get("data-latitude"), mp[0].get("data-longitude"), mp[0].get("data-address")
            if not addr:
                ad = dt.cssselect("address.address span.spr_jview.txt_adr, span.spr_jview.txt_adr")
                if ad:
                    addr = ad[0].text_content().strip()
            uc = dt.cssselect(".user_content")
            jd = uc[0].text_content().strip() if uc else ""
            is_img = bool(uc) and len(jd) < 300 and len(uc[0].cssselect("img")) >= 1
            # 상세 요약 dl(경력·학력·근무형태·기업형태) = 구조화 필드
            summ = {}
            for dl in dt.cssselect("div.jv_summary dl"):
                for k, v in zip(dl.cssselect("dt"), dl.cssselect("dd")):
                    summ[k.text_content().strip()] = v.text_content().strip()
            title = a[0].text_content().strip()
            img_urls = []
            # 이미지 공고: OCR용 이미지 URL 전부 확보 + 제목/직무 폴백(OCR 꺼졌거나 실패 시)
            match_jd = jd
            if is_img:
                match_jd = f"{title} {summ.get('직무', '')} {jd}".strip()
                for im in uc[0].cssselect("img"):
                    src = im.get("src") or im.get("data-src") or ""
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = self.BASE + src
                    if src.startswith("http") and src not in img_urls:
                        img_urls.append(src)
            job = Job("사람인", title,
                      corp[0].text_content().strip() if corp else "",
                      view, match_jd, addr or "",
                      float(lat) if lat else None, float(lng) if lng else None, is_img,
                      career=_career_from_field(summ.get("경력", "")),
                      emp_type=_norm_emp(summ.get("근무형태", "")),
                      edu_req=_norm_edu(summ.get("학력", "")),
                      comp_type=summ.get("기업형태", ""), img_urls=img_urls)
            if _passes(job, filters):                          # 필터 통과분만 수집
                out.append(job)
        return out


class Jobkorea(_Adapter):
    BASE = "https://www.jobkorea.co.kr"

    async def fetch(self, kw: str, n: int, filters: dict | None = None, skip=None) -> list[Job]:
        out: list[Job] = []
        cap = max(config.SCAN_CAP, n * config.SCAN_PER_TARGET)   # 목표(n) 비례 스캔 깊이
        seen: set[str] = set()
        gnos: list[str] = []
        for p in range(1, 12):   # 최신 여러 페이지에서 링크 수집(중복 GI_No 제거) — cap만큼 확보되면 중단
            try:
                t = LH.fromstring((await self.c.get(
                    f"{self.BASE}/Search/?stext={quote(kw)}&tabType=recruit&Page_No={p}")).text)
            except Exception:
                break
            new = 0
            for a in t.cssselect('a[href*="/Recruit/GI_Read/"]'):
                m = re.search(r"/Recruit/GI_Read/(\d+)", a.get("href") or "")
                if m and m.group(1) not in seen:
                    seen.add(m.group(1)); gnos.append(m.group(1)); new += 1
            if len(gnos) >= cap or new == 0:
                break
        scanned = 0
        for g in gnos:
            if len(out) >= n or scanned >= cap:
                break
            gurl = f"{self.BASE}/Recruit/GI_Read/{g}"
            if skip and skip(gurl):          # 이미 처리(지원함/관심없음) → 스캔 전 제외
                continue
            try:
                rd = await self.c.get(gurl)
            except Exception:
                continue
            scanned += 1
            tree = LH.fromstring(rd.text)
            jp = None
            for blk in tree.xpath('//script[@type="application/ld+json"]/text()'):
                try:
                    d = json.loads(blk)
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("@type") == "JobPosting":
                    jp = d
                    break
            if not jp:
                continue
            # 모집요강 테이블(경력·학력·근무/고용형태) = JSON-LD엔 없는 실제 요구(선택자 기반)
            summ = {}
            for lab in ("경력", "학력", "근무형태", "고용형태"):
                vals = tree.xpath(f'//*[normalize-space(text())="{lab}"]/following-sibling::*[1]//text()')
                v = " ".join(x.strip() for x in vals if x.strip())[:40]
                if v:
                    summ[lab] = v
            addr = ((jp.get("jobLocation") or {}).get("address") or {}).get("streetAddress", "")
            lat = lng = None
            mm = re.search(r'"latitude":\s*([-\d.]+),"longitude":\s*([-\d.]+)', rd.text)
            if mm:
                lat, lng = float(mm.group(1)), float(mm.group(2))
            # 경력은 모집요강 값 우선(JSON-LD "신입·경력" 요약보다 정확)
            career = _career_from_field(summ.get("경력", "")) or _career_from_text(str(jp.get("experienceRequirements", "")))
            # ⚠ 잡코리아 전문 JD는 JS 렌더라 정적 httpx로 못 얻음(§4 Playwright 금지) →
            #    가져올 수 있는 정적 필드 최대 결합(요약+모집요강+og+제목+직무태그)으로 매칭 텍스트 확보
            ogd = tree.cssselect('meta[property="og:description"], meta[name="description"]')
            tags = " ".join(x.text_content().strip() for x in tree.cssselect('.jobKeyword, .keyword a, [class*=tag]')[:20])
            jd_parts = [jp.get("title", ""),
                        re.sub(r"<[^>]+>", " ", jp.get("description", "") or ""),
                        (ogd[0].get("content") if ogd else ""),
                        " ".join(f"{k} {v}" for k, v in summ.items()), tags]
            jd_txt = re.sub(r"\s+", " ", " / ".join(p for p in jd_parts if p)).strip()
            job = Job("잡코리아", jp.get("title", ""),
                      (jp.get("hiringOrganization") or {}).get("name", ""),
                      gurl, jd_txt, addr, lat, lng,
                      career=career,
                      emp_type=_norm_emp(summ.get("고용형태", "") or summ.get("근무형태", "")),
                      edu_req=_norm_edu(summ.get("학력", "")))
            if not _passes(job, filters):
                continue
            # 통과분만 전문 JD 확보: 상세요강 iframe(GI_Read_Comt_Ifrm)의 CorpEditor 이미지 → OCR(pipeline)
            try:
                ifr = await self.c.get(f"{self.BASE}/Recruit/GI_Read_Comt_Ifrm?Gno={g}",
                                       headers={"Referer": gurl})
                itree = LH.fromstring(ifr.text)
                for im in itree.cssselect("img"):
                    s = im.get("src") or im.get("data-src") or ""
                    if ("CorpEditor" in s or "DownImage" in s) and "logo" not in s.lower():
                        if s.startswith("//"):
                            s = "https:" + s
                        if s.startswith("http") and s not in job.img_urls:
                            job.img_urls.append(s)
                if job.img_urls:
                    job.is_image = True          # 전문 JD가 이미지 → pipeline이 OCR
            except Exception:
                pass
            out.append(job)
        return out


class Wanted(_Adapter):
    BASE = "https://www.wanted.co.kr"
    # 실측: years=0=신입, locations=서버측 지역 필터(원티드 API는 진짜 서버측 필터 됨)
    LOC = {"서울 전체": "seoul.all", "강남구": "seoul.gangnam-gu", "서초구": "seoul.seocho-gu",
           "송파구": "seoul.songpa-gu", "경기": "gyeonggi.all", "인천": "incheon.all",
           "대전": "daejeon.all", "부산": "busan.all"}

    async def fetch(self, kw: str, n: int, filters: dict | None = None, skip=None) -> list[Job]:
        out: list[Job] = []
        cap = max(config.SCAN_CAP, n * config.SCAN_PER_TARGET)   # 목표(n) 비례 스캔 깊이
        f = filters or {}
        params = ["country=kr", f"query={quote(kw)}",
                  f"limit={min(max(cap, 20), 100)}", "offset=0"]
        if f.get("career") == "신입":                       # 서버측 경력(신입)
            params.append("years=0")
        for r in (f.get("regions") or []):                 # 서버측 지역
            if r in self.LOC:
                params.append(f"locations={self.LOC[r]}")
        ja = await self.c.get(f"{self.BASE}/api/v4/jobs?" + "&".join(params))
        seen: set[str] = set()
        scanned = 0
        for wid in [d["id"] for d in ja.json().get("data", [])]:
            if len(out) >= n or scanned >= cap:
                break
            wurl = f"{self.BASE}/wd/{wid}"
            if wurl in seen or (skip and skip(wurl)):      # 중복·이미 처리 → 제외
                continue
            seen.add(wurl)
            try:
                rw = await self.c.get(wurl)
                nd = LH.fromstring(rw.text).xpath('//script[@id="__NEXT_DATA__"]/text()')
                d = json.loads(nd[0])["props"]["pageProps"]["initialData"]
            except Exception:
                continue
            scanned += 1
            jd = "\n".join(str(d.get(k, "") or "") for k in
                           ["intro", "main_tasks", "requirements", "preferred_points", "benefits"])
            addr = d.get("address") or {}
            job = Job("원티드", d.get("position", ""),
                      (d.get("company") or {}).get("company_name", ""),
                      wurl, jd,
                      addr.get("full_location") or f"{addr.get('location', '')} {addr.get('district', '')}".strip(),
                      career=_career_from_wanted(d.get("career") or {}),
                      emp_type=_norm_emp_wanted(d.get("employment_type", "")))
            if _passes(job, filters):
                out.append(job)
        return out


class Crawler:
    def __init__(self, client):
        self.adapters = [Saramin(client), Jobkorea(client), Wanted(client)]

    async def collect(self, kw: str, per_site: int, filters: dict | None = None,
                      skip=None) -> list[Job]:
        # 어댑터가 "필터 통과 + 미처리 + 미중복" 공고를 per_site개 채울 때까지 훑음(SCAN_CAP 상한)
        results = await asyncio.gather(*[a.fetch(kw, per_site, filters, skip) for a in self.adapters],
                                       return_exceptions=True)
        jobs: list[Job] = []
        seen: set[str] = set()
        for r in results:
            if isinstance(r, list):
                for j in r:
                    if j.url not in seen:      # 전체 링크 단위 중복 제거(사이트 간 동일 URL)
                        seen.add(j.url)
                        jobs.append(j)
        return jobs
